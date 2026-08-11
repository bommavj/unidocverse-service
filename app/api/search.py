import json
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Query, HTTPException
from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config.model_factory import EmbeddingModelFactory
from app.core.config import settings
from app.core.pii_redactor import redact_text
from app.core.database import get_db, SessionLocal
from app.core.performance import cached_encode
from app.schemas.document import SimilarDocumentsResponse
from app.services.document_service import DocumentService
from app.services.question_suggestion import QuestionSuggestionService
from app.services.universal_qa_service import UniversalQAService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])

embedding_model = EmbeddingModelFactory.get_model()


def _translate_query_to_english_if_needed(query: str, lang: str) -> str:
    if lang == "en" or not query or not query.strip():
        return query
    try:
        import ollama
        client = ollama.Client()
        prompt = f"""You are a precise search query translator.
Translate this search query into English. If it is already in English, output it exactly as is.
Output ONLY the translation, no explanations, no quotes, no extra text.

Query: "{query}"

Translation:"""
        response = client.generate(
            model=settings.OLLAMA_MODEL,
            prompt=prompt,
            options={"temperature": 0.0}
        )
        translated = response.get("response", "").strip().strip('"').strip("'")
        if translated:
            logger.info(f"🔄 Translated query from '{query}' ({lang}) to English: '{translated}'")
            return translated
    except Exception as e:
        logger.error(f"❌ Query translation failed: {e}")
    return query


# ✅ Request model for POST
class SemanticSearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 10
    threshold: Optional[float] = 0.2


# =============================================================================
# QUERY EXPANSION — DOMAIN MAP
# =============================================================================

QUERY_EXPANSION_MAP: Dict[str, Dict[str, List[str]]] = {
    "sales": {
        "keywords": ["sales", "revenue", "product", "region", "qty", "quantity"],
        "expansions": [
            "sales data",
            "product sales information",
            "regional sales summary",
            "transaction details",
            "sales performance",
            "sales metrics",
            "main content",
        ],
    },
    "invoice": {
        "keywords": ["invoice", "billing", "payment", "po", "purchase order", "amount due"],
        "expansions": [
            "invoice details",
            "billing information",
            "payment terms",
            "invoice payment terms",
            "amount due",
            "invoice summary",
            "main content",
        ],
    },
    "tax": {
        "keywords": ["tax", "w2", "w-2", "irs", "1040", "refund", "earnings"],
        "expansions": [
            "tax information",
            "income details",
            "earnings summary",
            "w-2 wage information",
            "tax filing details",
            "taxpayer information",
            "main content",
        ],
    },
    "bank": {
        "keywords": ["bank", "statement", "account", "transaction", "balance", "deposit"],
        "expansions": [
            "bank statement balance",
            "account transactions",
            "account summary",
            "financial activity",
            "account details",
            "main content",
        ],
    },
    "loan": {
        "keywords": ["loan", "mortgage", "refinance", "apr", "interest", "escrow"],
        "expansions": [
            "loan details",
            "mortgage information",
            "interest rate summary",
            "payment schedule",
            "loan estimate",
            "main content",
        ],
    },
    "insurance": {
        "keywords": [
            "insurance", "claim", "policy", "coverage", "premium",
            "stillwater", "deductible", "liability", "endorsement",
            "carrier", "insured", "allstate", "geico", "progressive",
        ],
        "expansions": [
            "insurance policy details",
            "claim information",
            "coverage summary",
            "deductible details",
            "premium information",
            "policy number",
            "insurance carrier",
            "main content",
        ],
    },
    "medical": {
        "keywords": ["medical", "health", "eob", "patient", "provider", "treatment"],
        "expansions": [
            "medical information",
            "explanation of benefits",
            "patient details",
            "provider summary",
            "clinical information",
            "treatment summary",
            "main content",
        ],
    },
    "legal": {
        "keywords": ["contract", "agreement", "legal", "nda", "terms", "obligations"],
        "expansions": [
            "contract details",
            "legal terms",
            "agreement summary",
            "obligations and clauses",
            "legal information",
            "main content",
        ],
    },
    "hr": {
        "keywords": ["resume", "cv", "employee", "hr", "job", "payroll"],
        "expansions": [
            "employment information",
            "employee details",
            "job history",
            "skills summary",
            "payroll information",
            "main content",
        ],
    },
    "education": {
        "keywords": ["transcript", "grade", "school", "education", "student"],
        "expansions": [
            "academic information",
            "course details",
            "grade summary",
            "student record",
            "education summary",
            "main content",
        ],
    },
    "real_estate": {
        "keywords": ["property", "real estate", "listing", "home", "valuation"],
        "expansions": [
            "property details",
            "real estate information",
            "listing summary",
            "valuation details",
            "home information",
            "main content",
        ],
    },
    "payroll": {
        "keywords": ["paystub", "payroll", "earnings", "deductions", "net pay"],
        "expansions": [
            "payroll information",
            "earnings summary",
            "deductions breakdown",
            "net pay details",
            "income information",
            "main content",
        ],
    },
    "shipping": {
        "keywords": ["shipping", "tracking", "delivery", "package", "carrier"],
        "expansions": [
            "shipping details",
            "delivery information",
            "tracking summary",
            "package status",
            "main content",
        ],
    },
    "compliance": {
        "keywords": ["compliance", "audit", "regulation", "policy"],
        "expansions": [
            "compliance information",
            "audit summary",
            "regulatory details",
            "policy information",
            "main content",
        ],
    },
}


def _expand_query(query: str) -> List[str]:
    q = query.lower().strip()
    expansions: List[str] = []

    for _, cfg in QUERY_EXPANSION_MAP.items():
        if any(keyword in q for keyword in cfg["keywords"]):
            expansions.extend(cfg["expansions"])

    if not expansions:
        expansions = [
            "main content",
            "important information",
            "key details",
            "document summary",
        ]

    return [query] + list(dict.fromkeys(expansions))


# =============================================================================
# DATE EXPANSION
# =============================================================================

MONTHS_FULL = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]
MONTHS_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
]
MONTH_MAP = {m.lower(): i + 1 for i, m in enumerate(MONTHS_FULL)}
MONTH_MAP.update({m.lower(): i + 1 for i, m in enumerate(MONTHS_ABBR)})


def expand_date_query(q: str) -> List[str]:
    expansions = {q}

    # "June 2023", "Jun 2023"
    month_regex = r"(" + "|".join(MONTHS_FULL + MONTHS_ABBR) + r")"
    m = re.search(rf"{month_regex}\s+(\d{{4}})", q, re.I)
    if m:
        month_name = m.group(1)
        year = m.group(2)
        month_num = MONTH_MAP[month_name.lower()]
        expansions.update({
            f"{year}-{month_num:02d}",
            f"{month_num:02d}/{year}",
            f"{year} {month_name}",
            f"{month_name}",
            f"{year}",
        })

    # ISO: 2023-06-14
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", q)
    if iso:
        y, mo, d = iso.group(1), iso.group(2), iso.group(3)
        month_name = MONTHS_FULL[int(mo) - 1]
        expansions.update({
            f"{month_name} {y}",
            f"{month_name} {int(d)} {y}",
            f"{y}-{mo}",
            f"{mo}/{y}",
            f"{month_name}",
            f"{y}",
        })

    # "06/2023" or "6/2023"
    slash = re.search(r"(\d{1,2})/(\d{4})", q)
    if slash:
        mo, y = int(slash.group(1)), slash.group(2)
        if 1 <= mo <= 12:
            month_name = MONTHS_FULL[mo - 1]
            expansions.update({
                f"{y}-{mo:02d}",
                f"{month_name} {y}",
                f"{month_name}",
                f"{y}",
            })

    # Standalone year: "2023"
    year = re.search(r"\b(19|20)\d{2}\b", q)
    if year:
        expansions.add(year.group(0))

    return list(expansions)


def expand_query_all(query: str) -> List[str]:
    """Combine date + domain expansions, deduped, original query first."""
    date_expanded = expand_date_query(query)
    domain_expanded = _expand_query(query)

    seen = set()
    final = []
    for q in date_expanded + domain_expanded:
        if q not in seen:
            seen.add(q)
            final.append(q)

    logger.info(f"📎 Expanded queries ({len(final)}): {final}")
    return final


# =============================================================================
# FILENAME / KEYWORD SEARCH (handles proper nouns, policy numbers, names)
# =============================================================================

def _filename_keyword_search(query: str, db, limit: int) -> List[Dict[str, Any]]:
    """
    Direct keyword search on filename, summary, and doc_metadata.
    Critical for proper nouns (company names, person names, policy numbers)
    that semantic embeddings alone cannot rank correctly.
    """
    try:
        words = [w for w in query.strip().split() if len(w) > 2]
        if not words:
            return []

        # Build OR conditions — each word checked against filename, summary, metadata
        conditions = " OR ".join([
            f"(filename ILIKE :w{i} OR summary ILIKE :w{i} OR CAST(doc_metadata AS text) ILIKE :w{i})"
            for i in range(len(words))
        ])

        sql = text(f"""
            SELECT id, filename, doc_type, summary, created_at, doc_metadata
            FROM documents
            WHERE status = 'completed' AND ({conditions})
            LIMIT :limit
        """)

        params = {"limit": limit}
        for i, w in enumerate(words):
            params[f"w{i}"] = f"%{w}%"

        rows = db.execute(sql, params).fetchall()

        results = []
        q_lower = query.lower()
        for row in rows:
            fn = (row.filename or "").lower()
            summary_text = (row.summary or "").lower()
            meta_text = json.dumps(row.doc_metadata or {}).lower()

            word_hits_fn = sum(1 for w in words if w.lower() in fn)
            word_hits_summary = sum(1 for w in words if w.lower() in summary_text)
            word_hits_meta = sum(1 for w in words if w.lower() in meta_text)

            # Score: full query in filename = 0.95, all words in filename = 0.85,
            # partial filename = 0.60–0.75, summary/metadata match = 0.55–0.65
            if q_lower in fn:
                sim = 0.95
            elif word_hits_fn == len(words):
                sim = 0.85
            elif word_hits_fn > 0:
                sim = 0.60 + (word_hits_fn / len(words)) * 0.15
            elif word_hits_summary == len(words) or word_hits_meta == len(words):
                sim = 0.65
            elif word_hits_summary > 0 or word_hits_meta > 0:
                sim = 0.55
            else:
                sim = 0.40

            results.append({
                "id": str(row.id),
                "filename": row.filename,
                "doc_type": row.doc_type or "unknown",
                "summary": _clean_summary(row.summary),
                "key_points": _extract_key_points(row.doc_metadata),
                "created_at": _iso(row.created_at),
                "similarity": round(sim, 4),
                "match_score": int(sim * 100),
                "match_quality": _get_match_quality(sim),
            })

        logger.info(f"📁 Filename keyword matches: {len(results)} for '{query}'")
        return results

    except Exception as e:
        logger.error(f"❌ Filename keyword search failed: {e}", exc_info=True)
        return []


# =============================================================================
# PUBLIC ENDPOINTS
# =============================================================================

@router.api_route("/hybrid", methods=["GET", "POST"])
async def hybrid_search(
        request: Request,
        query: Optional[str] = Query(None),
        limit: int = Query(10),
        threshold: float = Query(0.2)
):
    """Hybrid search: keyword (exact) + semantic (pgvector)."""
    if request.method == "POST":
        try:
            body = await request.json()
            query = body.get("query", query)
            limit = body.get("limit", limit)
            threshold = body.get("threshold", threshold)
        except Exception:
            pass

    if not query:
        query = request.query_params.get("query")

    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Query required")

    query = query.strip()
    db = SessionLocal()
    try:
        from app.core.language import get_request_language
        lang = get_request_language(request, db)
        query = _translate_query_to_english_if_needed(query, lang)

        query_vector = cached_encode(embedding_model, query).tolist()

        sql = text("""
            WITH combined_matches AS (
                SELECT id, filename, doc_type, summary, created_at, doc_metadata,
                       1.0 AS similarity, 'keyword' AS match_type
                FROM documents
                WHERE status = 'completed'
                  AND (raw_text ILIKE :k OR summary ILIKE :k OR filename ILIKE :k)

                UNION ALL

                SELECT id, filename, doc_type, summary, created_at, doc_metadata,
                       1 - (embedding <=> CAST(:vec AS vector(768))) AS similarity,
                       'semantic' AS match_type
                FROM documents
                WHERE embedding IS NOT NULL AND status = 'completed'
                  AND vector_dims(embedding) = 768
            )
            SELECT DISTINCT ON (id) *
            FROM combined_matches
            WHERE similarity >= :threshold
            ORDER BY id, similarity DESC
        """)

        result = db.execute(sql, {"k": f"%{query}%", "vec": str(query_vector), "threshold": threshold})

        documents = []
        for row in result:
            sim = float(row.similarity)
            documents.append({
                "id": str(row.id),
                "filename": row.filename,
                "doc_type": row.doc_type or "unknown",
                "summary": row.summary[:200] + "..." if row.summary else "",
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "similarity": round(sim, 4),
                "match_score": int(sim * 100),
                "match_quality": _get_match_quality(sim),
                "match_type": row.match_type,
            })

        documents.sort(key=lambda x: x["similarity"], reverse=True)

        return {
            "query": query,
            "results": documents[:limit],
            "count": len(documents),
        }
    finally:
        db.close()


@router.api_route("/semantic", methods=["GET", "POST"])
async def semantic_search(
        request: Request,
        query: Optional[str] = Query(None, alias="q", description="Search query"),
        limit: int = Query(10, ge=1, le=100),
        threshold: float = Query(0.2, ge=0.0, le=1.0, description="Similarity threshold"),
):
    """Document-level semantic search (GET + POST)."""
    if request.method == "POST":
        try:
            body = await request.json()
            query = body.get("query", query)
            limit = body.get("limit", limit)
            threshold = body.get("threshold", threshold)
        except Exception:
            pass

    if not query:
        query = request.query_params.get("query")

    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Query parameter required")

    query = query.strip()
    logger.info(f"🔍 Semantic search (document-level): '{query}' (limit={limit}, threshold={threshold})")

    return await _perform_semantic_search(query=query, limit=limit, threshold=threshold, request=request)


@router.api_route("/semantic/chunks", methods=["GET", "POST"])
async def semantic_chunk_search(
        request: Request,
        query: Optional[str] = Query(None, alias="q", description="Search query"),
        limit: int = Query(10, ge=1, le=100),
        threshold: float = Query(0.2, ge=0.0, le=1.0, description="Similarity threshold"),
):
    """Chunk-level semantic search (GET + POST)."""
    if request.method == "POST":
        try:
            body = await request.json()
            query = body.get("query", query)
            limit = body.get("limit", limit)
            threshold = body.get("threshold", threshold)
        except Exception:
            pass

    if not query:
        query = request.query_params.get("query")

    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Query parameter required")

    query = query.strip()
    logger.info(f"🔍 Semantic search (chunk-level): '{query}' (limit={limit}, threshold={threshold})")

    return await _perform_chunk_semantic_search(query=query, limit=limit, threshold=threshold, request=request)


# =============================================================================
# CORE SEARCH LOGIC — DOCUMENT LEVEL (pgvector-native + keyword merge)
# =============================================================================

async def _perform_semantic_search(query: str, limit: int = 10, threshold: float = 0.2, request: Optional[Request] = None) -> Dict[str, Any]:
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        db = SessionLocal()
        try:
            if request:
                from app.core.language import get_request_language
                lang = get_request_language(request, db)
                query = _translate_query_to_english_if_needed(query, lang)

            expanded_queries = expand_query_all(query)
            query_vectors = [embedding_model.encode(q).tolist() for q in expanded_queries]
            logger.info(f"🔍 Query vectors: {len(query_vectors)}, dim={len(query_vectors[0])}")
            # ── Step 1: pgvector semantic search across all expanded queries ──
            union_parts = " UNION ALL ".join([
                f"""
                SELECT
                    id,
                    filename,
                    doc_type,
                    summary,
                    created_at,
                    doc_metadata,
                    1 - (embedding <=> CAST(:vec_{i} AS vector(768))) AS similarity
                FROM documents
                WHERE embedding IS NOT NULL AND status = 'completed'
                  AND vector_dims(embedding) = 768
                """
                for i in range(len(query_vectors))
            ])

            sql = text(f"""
                SELECT
                    id, filename, doc_type, summary, created_at, doc_metadata,
                    MAX(similarity) AS similarity
                FROM ({union_parts}) AS combined
                GROUP BY id, filename, doc_type, summary, created_at, doc_metadata
                HAVING MAX(similarity) >= :threshold
                ORDER BY similarity DESC
                LIMIT :limit
            """)

            params = {"threshold": threshold, "limit": limit}
            for i, vec in enumerate(query_vectors):
                params[f"vec_{i}"] = str(vec)

            rows = db.execute(sql, params).fetchall()

            # Log top 5 raw scores for debugging
            for row in rows[:5]:
                logger.info(f"  📊 {row.filename}: raw_sim={float(row.similarity):.4f}")

            logger.info(f"✅ Document-level results: {len(rows)} (threshold={threshold})")

            results = []
            for row in rows:
                sim = float(row.similarity)

                # Small additive boosts — pgvector score is the primary signal
                metadata_boost = 0.05 if _metadata_boost(query, row.doc_metadata) else 0.0
                filename_boost = 0.05 if _filename_boost(query, row.filename) else 0.0
                doctype_boost = 0.02 if _doctype_boost(query, row.doc_type) else 0.0

                final_score = min(1.0, sim + metadata_boost + filename_boost + doctype_boost)

                results.append({
                    "id": str(row.id),
                    "filename": row.filename,
                    "doc_type": row.doc_type or "unknown",
                    "summary": _clean_summary(row.summary),
                    "key_points": _extract_key_points(row.doc_metadata),
                    "created_at": _iso(row.created_at),
                    "similarity": round(final_score, 4),
                    "match_score": int(final_score * 100),
                    "match_quality": _get_match_quality(final_score),
                })

            # ── Step 2: Always run filename keyword search and merge ──
            # Handles proper nouns, company names, policy numbers, person names
            filename_matches = _filename_keyword_search(query, db, limit)
            existing_ids = {r["id"] for r in results}

            for fm in filename_matches:
                if fm["id"] not in existing_ids:
                    # New match from keyword — add to results
                    results.append(fm)
                    existing_ids.add(fm["id"])
                else:
                    # Already in semantic results — boost it if keyword also matched
                    for r in results:
                        if r["id"] == fm["id"]:
                            boosted = min(1.0, r["similarity"] + 0.30)
                            r["similarity"] = round(boosted, 4)
                            r["match_score"] = int(boosted * 100)
                            r["match_quality"] = _get_match_quality(boosted)
                            break

            results.sort(key=lambda r: r["similarity"], reverse=True)
            results = results[:limit]

            # ── Step 3: Chunk-level fallback if still no results ──
            if not results:
                logger.info("🔍 No matches — falling back to chunk-level search")
                chunk_response = await _perform_chunk_semantic_search(
                    query=query, limit=limit, threshold=threshold
                )

                doc_results: Dict[str, Any] = {}
                for r in chunk_response["results"]:
                    doc_id = r["document_id"]
                    if doc_id not in doc_results or r["similarity"] > doc_results[doc_id]["similarity"]:
                        doc_results[doc_id] = {
                            "id": doc_id,
                            "filename": r["filename"],
                            "doc_type": r["doc_type"],
                            "summary": r["snippet"],
                            "key_points": [],
                            "created_at": r["created_at"],
                            "similarity": r["similarity"],
                            "match_score": int(r["similarity"] * 100),
                            "match_quality": r["match_quality"],
                        }

                results = sorted(doc_results.values(), key=lambda r: r["similarity"], reverse=True)
                logger.info(f"✅ Chunk fallback results: {len(results)}")

            return {
                "query": query,
                "expanded_queries": expanded_queries,
                "results": results,
                "count": len(results),
                "threshold": threshold,
                "model": "all-mpnet-base-v2",
                "dimensions": 768,
            }

        finally:
            db.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Semantic search failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Semantic search failed")


# =============================================================================
# CORE SEARCH LOGIC — CHUNK LEVEL (pgvector-native)
# =============================================================================

async def _perform_chunk_semantic_search(query: str, limit: int = 10, threshold: float = 0.2, request: Optional[Request] = None) -> Dict[str, Any]:
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        db = SessionLocal()
        try:
            if request:
                from app.core.language import get_request_language
                lang = get_request_language(request, db)
                query = _translate_query_to_english_if_needed(query, lang)

            expanded_queries = expand_query_all(query)
            query_vectors = [embedding_model.encode(q).tolist() for q in expanded_queries]
            logger.info(f"🔍 Chunk query vectors: {len(query_vectors)}, dim={len(query_vectors[0])}")
            union_parts = " UNION ALL ".join([
                f"""
                SELECT
                    c.id          AS chunk_id,
                    c.document_id,
                    c.chunk_index,
                    c.chunk_text,
                    d.filename,
                    d.doc_type,
                    d.created_at,
                    d.doc_metadata,
                    1 - (c.embedding <=> CAST(:vec_{i} AS vector(768))) AS similarity
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL AND d.status = 'completed'
                  AND vector_dims(c.embedding) = 768
                """
                for i in range(len(query_vectors))
            ])

            sql = text(f"""
                SELECT
                    chunk_id, document_id, chunk_index, chunk_text,
                    filename, doc_type, created_at, doc_metadata,
                    MAX(similarity) AS similarity
                FROM ({union_parts}) AS combined
                GROUP BY chunk_id, document_id, chunk_index, chunk_text,
                         filename, doc_type, created_at, doc_metadata
                HAVING MAX(similarity) >= :threshold
                ORDER BY similarity DESC
                LIMIT :limit
            """)

            params = {"threshold": threshold, "limit": limit}
            for i, vec in enumerate(query_vectors):
                params[f"vec_{i}"] = str(vec)

            rows = db.execute(sql, params).fetchall()
            logger.info(f"✅ Chunk-level results: {len(rows)} (threshold={threshold})")

            results = []
            for row in rows:
                sim = float(row.similarity)
                results.append({
                    "chunk_id": str(row.chunk_id),
                    "document_id": str(row.document_id),
                    "chunk_index": row.chunk_index,
                    "filename": row.filename,
                    "doc_type": row.doc_type or "unknown",
                    "snippet": _clean_chunk_text(row.chunk_text),
                    "created_at": _iso(row.created_at),
                    "similarity": round(sim, 4),
                    "match_score": int(sim * 100),
                    "match_quality": _get_match_quality(sim),
                })

            return {
                "query": query,
                "expanded_queries": expanded_queries,
                "results": results,
                "count": len(results),
                "threshold": threshold,
                "model": "all-mpnet-base-v2",
                "dimensions": 768,
            }

        finally:
            db.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Chunk semantic search failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Chunk semantic search failed")


# =============================================================================
# SCORING HELPERS
# =============================================================================

def _metadata_boost(query: str, metadata: Any) -> bool:
    if not metadata:
        return False
    try:
        text_blob = json.dumps(metadata).lower()
        q_words = [w for w in query.lower().split() if len(w) > 2]
        return any(w in text_blob for w in q_words)
    except Exception:
        return False


def _filename_boost(query: str, filename: Optional[str]) -> bool:
    if not filename:
        return False
    f = filename.lower()
    q_words = [w for w in query.lower().split() if len(w) > 2]
    return any(w in f for w in q_words)


def _doctype_boost(query: str, doc_type: Optional[str]) -> bool:
    if not doc_type:
        return False
    return doc_type.lower() in query.lower() or query.lower() in doc_type.lower()


def _get_match_quality(score: float) -> str:
    if score >= 0.85:
        return "excellent"
    if score >= 0.70:
        return "good"
    if score >= 0.50:
        return "fair"
    return "poor"


# =============================================================================
# FORMAT HELPERS
# =============================================================================

def _clean_summary(summary: Optional[str]) -> Optional[str]:
    if not summary:
        return None
    s = redact_text(summary.strip())
    return s[:200] + "..." if len(s) > 200 else s


def _clean_chunk_text(text_val: Optional[str]) -> Optional[str]:
    if not text_val:
        return None
    s = redact_text(text_val.strip().replace("\n", " "))
    return s[:240] + "..." if len(s) > 240 else s


def _extract_key_points(metadata: Any) -> List[str]:
    if not metadata:
        return []
    try:
        return (metadata or {}).get("key_points", [])[:3]
    except Exception:
        return []


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# =============================================================================
# TEST / STATUS ENDPOINT
# =============================================================================

@router.get("/test")
async def test_search_config():
    """Test search configuration and DB stats."""
    try:
        db = SessionLocal()
        try:
            stats = db.execute(text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(embedding) AS with_embeddings,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed
                FROM documents
            """)).fetchone()

            pg_info = db.execute(text("""
                SELECT
                    EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS has_pgvector,
                    (SELECT extversion FROM pg_extension WHERE extname = 'vector') AS pgvector_version
            """)).fetchone()

            chunk_stats = db.execute(text("""
                SELECT
                    COUNT(*) AS total_chunks,
                    COUNT(embedding) AS chunks_with_embeddings
                FROM document_chunks
            """)).fetchone()

            return {
                "status": "ok",
                "total_documents": stats.total,
                "documents_with_embeddings": stats.with_embeddings,
                "completed_documents": stats.completed,
                "total_chunks": chunk_stats.total_chunks,
                "chunks_with_embeddings": chunk_stats.chunks_with_embeddings,
                "pgvector_installed": pg_info.has_pgvector,
                "pgvector_version": pg_info.pgvector_version,
                "embedding_model": "all-mpnet-base-v2",
                "embedding_dimensions": 768,
                "search_mode": "pgvector-native + keyword merge",
                "endpoints": {
                    "semantic_get": "/api/search/semantic?q=your_query&limit=10",
                    "semantic_post": "POST /api/search/semantic  body: {query, limit, threshold}",
                    "chunks_get": "/api/search/semantic/chunks?q=your_query",
                    "hybrid_get": "/api/search/hybrid?query=your_query",
                },
            }
        finally:
            db.close()

    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return {"status": "error", "error": str(e)}


# =============================================================================
# SIMILAR DOCUMENTS
# =============================================================================

@router.get("/documents/{doc_id}/similar", response_model=SimilarDocumentsResponse)
async def similar(
        doc_id: str,
        limit: int = 5,
        db: Session = Depends(get_db)
):
    """Find similar documents using pgvector."""
    try:
        doc = DocumentService.get_document_by_id(db, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        if doc.embedding is None or len(doc.embedding) == 0:
            raise HTTPException(status_code=400, detail="Document has no embedding")

        similar_docs = DocumentService.find_similar_documents(db, doc_id, limit)

        return SimilarDocumentsResponse(
            source_document={
                "id": str(doc.id),
                "filename": doc.filename,
                "doc_type": doc.doc_type or "unknown",
            },
            similar_documents=similar_docs,
            count=len(similar_docs),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Similar search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# DOCUMENT Q&A
# =============================================================================

@router.post("/documents/{doc_id}/ask")
async def ask_document_question(
        doc_id: str,
        request: dict,
        db: Session = Depends(get_db)
):
    """Ask a question about a specific document."""
    question = request.get("question", "")
    conversation_history = request.get("conversation_history", [])

    doc = DocumentService.get_document_by_id(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.raw_text:
        raise HTTPException(status_code=400, detail="No text content")

    qa_service = UniversalQAService(ollama_model=settings.OLLAMA_MODEL)

    result = qa_service.ask_question(
        question=question,
        document=doc,
        conversation_history=conversation_history,
    )

    return result


# =============================================================================
# SUGGESTED QUESTIONS
# =============================================================================

@router.get("/documents/{doc_id}/suggested-questions")
async def get_suggested_questions(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """Get contextual question suggestions for a document."""
    try:
        doc = DocumentService.get_document_by_id(db, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        analysis_results = None
        if hasattr(doc, "analysis_results") and doc.analysis_results:
            analysis_results = doc.analysis_results

        questions = QuestionSuggestionService.generate_questions(
            doc_type=doc.doc_type or "unknown",
            filename=doc.filename,
            analysis_results=analysis_results,
            summary=doc.summary,
        )

        return {
            "document_id": str(doc.id),
            "document_name": doc.filename,
            "document_type": doc.doc_type,
            "has_analysis": bool(analysis_results),
            "suggested_questions": questions,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate questions: {e}")
        raise HTTPException(status_code=500, detail=str(e))