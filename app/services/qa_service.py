"""
qa_service.py
=============
Intelligent Q&A service using pre-stored chunk embeddings from document_chunks table.

RAG Pipeline (per-document):
  1. RETRIEVE  — pgvector similarity search on document_chunks (pre-embedded, instant)
  2. AUGMENT   — build context from top-K relevant chunks + metadata
  3. GENERATE  — stream phi3:mini answer from augmented context only

Falls back to in-memory chunking if no chunks stored for the document.
"""

import logging
import re
from typing import Dict, List, Any, Optional
import ollama
import numpy as np

from app.core import config
from app.core.database import SessionLocal
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
from sqlalchemy import text

logger = logging.getLogger(__name__)

from app.config.model_factory import EmbeddingModelFactory
embedding_model = EmbeddingModelFactory.get_model()

ollama_client = ollama.Client()


# =============================================================================
# QUESTION TYPE DETECTION
# =============================================================================

def _detect_question_type(question: str) -> str:
    q = question.lower()
    if any(w in q for w in ["how much", "total", "amount", "balance", "fee", "cost", "price", "sum", "count"]):
        return "numeric"
    if any(w in q for w in ["when", "date", "period", "month", "year", "due"]):
        return "temporal"
    if any(w in q for w in ["who", "name", "holder", "owner", "party", "company", "bank"]):
        return "entity"
    if any(w in q for w in ["list", "what are", "show me", "enumerate", "transactions"]):
        return "list"
    return "general"


# =============================================================================
# PROMPT BUILDER
# =============================================================================

def _build_prompt(question: str, context: str, q_type: str) -> str:
    type_instructions = {
        "numeric":  "Extract the EXACT numbers, amounts, and figures directly from the context. Quote them verbatim.",
        "temporal": "Extract the EXACT date, time, and location directly from the context. Quote the exact words. Do NOT infer, calculate, or guess.",
        "entity":   "Extract the EXACT names, companies, and parties directly from the context. Quote them verbatim.",
        "list":     "List each item exactly as it appears in the context, one per line. Do not add items not present.",
        "general":  "Extract the exact relevant information from the context. Quote directly where possible."
    }
    instruction = type_instructions.get(q_type, type_instructions["general"])

    return f"""You are a document extraction tool. Your ONLY job is to find and return information that is explicitly written in the document context below.

STRICT RULES:
- ONLY use information that appears word-for-word in the context below
- Do NOT infer, guess, summarize, or add any information not in the context
- Do NOT suggest websites, URLs, or external sources
- Do NOT say "may be found" or "likely" — only state what IS in the context
- If the exact answer is not present in the context, say exactly: "This information is not available in the document."

{instruction}

--- DOCUMENT CONTEXT ---
{context}
--- END CONTEXT ---

Question: {question}
Answer (extracted directly from context above):"""


# =============================================================================
# RETRIEVAL — pgvector chunk search (pre-embedded, instant)
# =============================================================================

def _retrieve_chunks_from_db(document_id: str, question: str, top_k: int = 6) -> List[str]:
    """
    RETRIEVE step: find most relevant chunks using pgvector cosine similarity.
    Uses pre-stored embeddings — no re-computation needed.
    """
    try:
        query_vector = embedding_model.encode(question, show_progress_bar=False).tolist()

        db = SessionLocal()
        try:
            rows = db.execute(text("""
                SELECT chunk_text,
                       1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                FROM document_chunks
                WHERE document_id = :doc_id
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT :top_k
            """), {
                "vec": str(query_vector),
                "doc_id": str(document_id),
                "top_k": top_k,
            }).fetchall()

            logger.info(f"🔍 pgvector chunk retrieval: {len(rows)} chunks for doc {document_id}")
            for row in rows[:3]:
                logger.info(f"  📊 similarity={float(row.similarity):.4f} | {row.chunk_text[:60]}...")

            return [row.chunk_text for row in rows if row.chunk_text]

        finally:
            db.close()

    except Exception as e:
        logger.error(f"❌ pgvector chunk retrieval failed: {e}", exc_info=True)
        return []


def _has_chunks_in_db(document_id: str) -> bool:
    """Check if document has pre-stored chunks with embeddings."""
    try:
        db = SessionLocal()
        try:
            count = db.execute(text("""
                SELECT COUNT(*) FROM document_chunks
                WHERE document_id = :doc_id AND embedding IS NOT NULL
            """), {"doc_id": str(document_id)}).scalar()
            return count > 0
        finally:
            db.close()
    except Exception:
        return False


# =============================================================================
# FALLBACK — in-memory chunking (for docs without stored chunks)
# =============================================================================

def _chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> List[str]:
    """
    Markdown-aware chunker. Keeps table blocks intact so rows are never split
    mid-row and lose their header context. Non-table text falls back to the
    original character-window approach.
    """
    lines = text.splitlines(keepends=True)
    chunks: List[str] = []
    buffer = ""
    in_table = False
    table_header = ""

    def flush(buf: str) -> None:
        # character-window split for non-table buffers
        start = 0
        while start < len(buf):
            end = min(start + chunk_size, len(buf))
            chunk = buf[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start += chunk_size - overlap

    for line in lines:
        is_table_line = line.lstrip().startswith("|")

        if is_table_line and not in_table:
            # entering a table — flush preceding prose first
            if buffer.strip():
                flush(buffer)
                buffer = ""
            in_table = True
            table_header = line          # capture header row
            buffer = line

        elif is_table_line and in_table:
            buffer += line
            # if this table chunk is getting too large, split here and
            # re-attach the header so the next chunk is self-contained
            if len(buffer) > chunk_size * 3:
                chunks.append(buffer.strip())
                buffer = table_header    # restart with header

        elif not is_table_line and in_table:
            # leaving a table — emit the whole table as one chunk
            if buffer.strip():
                chunks.append(buffer.strip())
            buffer = line
            in_table = False
            table_header = ""

        else:
            buffer += line

    # flush whatever remains
    if buffer.strip():
        if in_table:
            chunks.append(buffer.strip())
        else:
            flush(buffer)

    return [c for c in chunks if c]


def _top_chunks_in_memory(question: str, chunks: List[str], top_k: int = 4) -> List[str]:
    """Semantic + keyword chunk selection for fallback path."""
    if not chunks:
        return []

    # Force-include keyword matches
    keywords = [w for w in question.lower().split() if len(w) > 3]
    forced = [c for c in chunks if any(kw in c.lower() for kw in keywords)]

    # Semantic fill
    q_emb  = embedding_model.encode(question, show_progress_bar=False)
    c_embs = embedding_model.encode(chunks, show_progress_bar=False, batch_size=32)
    scores = np.dot(c_embs, q_emb) / (
        np.linalg.norm(c_embs, axis=1) * np.linalg.norm(q_emb) + 1e-9
    )
    top_idx = np.argsort(scores)[::-1]
    semantic = [chunks[i] for i in top_idx if chunks[i] not in forced]

    result = forced[:top_k] + semantic[:max(0, top_k - len(forced))]
    return result[:top_k + len(forced)]


# =============================================================================
# CONTEXT BUILDER — Augmentation step
# =============================================================================

def _build_augmented_context(
    chunks: List[str],
    document: Any,
    q_type: str,
) -> str:
    """
    AUGMENT step: build rich context from retrieved chunks + document metadata.
    """
    context_parts = []

    # Relevant chunks FIRST — most important for accurate answers
    if chunks:
        context_parts.append(
            "Relevant Document Sections:\n" + "\n\n---\n\n".join(chunks)
        )

    # Document type
    if document.doc_type:
        context_parts.append(
            f"Document Type: {document.doc_type.replace('_', ' ').title()}"
        )

    # Summary
    if document.summary:
        context_parts.append(f"Summary:\n{document.summary}")

    # Key points
    metadata = document.doc_metadata or {}
    key_points = metadata.get("key_points", [])
    if key_points:
        context_parts.append(
            "Key Points:\n" + "\n".join(f"• {kp}" for kp in key_points[:7])
        )

    # Structured data
    analyzed = metadata.get("analyzed_data", {})
    if analyzed:
        useful = _extract_useful_structured(analyzed)
        if useful:
            context_parts.append(f"Structured Data:\n{useful}")

    # Tables for numeric/list questions
    if q_type in ("numeric", "list"):
        tables = metadata.get("tables", [])
        if tables:
            context_parts.append("Tables:\n" + _summarize_tables(tables))

    context = "\n\n".join(context_parts)

    # Cap at 6000 chars
    if len(context) > 6000:
        context = context[:6000] + "..."

    return context


# =============================================================================
# MAIN SERVICE
# =============================================================================

class QAService:

    @staticmethod
    def build_prompt(question: str, document: Any) -> tuple[str, str, dict]:
        """
        Full RAG pipeline: Retrieve → Augment → return prompt for generation.
        Returns (prompt, question_type, sources).
        """
        q_type = _detect_question_type(question)
        doc_id = str(document.id)
        chunks_searched = 0
        used_db_chunks = False

        # ── RETRIEVE ──────────────────────────────────────────────────────────
        if _has_chunks_in_db(doc_id):
            # Fast path: use pre-stored pgvector embeddings
            chunks = _retrieve_chunks_from_db(doc_id, question, top_k=6)
            chunks_searched = len(chunks)
            used_db_chunks = True
            logger.info(f"✅ Using pre-stored chunks from DB ({chunks_searched} chunks)")
        elif document.raw_text:
            # Fallback: chunk + embed at query time
            all_chunks = _chunk_text(document.raw_text, chunk_size=600, overlap=100)
            chunks = _top_chunks_in_memory(question, all_chunks, top_k=6)
            chunks_searched = len(all_chunks)
            logger.info(f"⚠ Fallback: in-memory chunking ({chunks_searched} chunks)")
        else:
            chunks = []
            logger.warning(f"⚠ No text or chunks available for doc {doc_id}")

        # ── AUGMENT ───────────────────────────────────────────────────────────
        context = _build_augmented_context(chunks, document, q_type)

        metadata = document.doc_metadata or {}
        sources = {
            "has_summary": bool(document.summary),
            "has_key_points": len(metadata.get("key_points", [])) > 0,
            "has_tables": len(metadata.get("tables", [])) > 0,
            "text_length": len(document.raw_text) if document.raw_text else 0,
            "chunks_searched": chunks_searched,
            "used_db_chunks": used_db_chunks,
            "retrieval_method": "pgvector" if used_db_chunks else "in-memory",
        }

        logger.info(
            f"📚 RAG context built: {len(chunks)} chunks, {len(context)} chars, "
            f"method={sources['retrieval_method']}"
        )

        return _build_prompt(question, context, q_type), q_type, sources

    @staticmethod
    def answer_question(
        question: str,
        document: Any,
        context_window: int = 3000
    ) -> Dict[str, Any]:
        """
        Synchronous Q&A (non-streaming) using full RAG pipeline.
        """
        try:
            prompt, q_type, sources = QAService.build_prompt(question, document)

            logger.info(f"🔍 RAG prompt preview:\n{prompt[:1500]}")

            response = ollama_client.generate(
                model=config.get_active_model(),
                prompt=prompt,
                options={
                    "temperature": 0.1,
                    "num_predict": 400,
                    "num_ctx": 4096,
                }
            )

            answer = response["response"].strip()
            confidence = _assess_confidence(answer)

            logger.info(
                f"✅ RAG answer: q_type={q_type}, confidence={confidence}, "
                f"method={sources['retrieval_method']}"
            )

            return {
                "answer": answer,
                "confidence": confidence,
                "question_type": q_type,
                "sources": sources,
            }

        except Exception as e:
            logger.error(f"❌ Q&A Error: {e}", exc_info=True)
            return {
                "answer": "I encountered an error while processing your question.",
                "confidence": 0.0,
                "error": str(e),
            }


# =============================================================================
# CHUNK STORAGE — called during document processing to pre-store chunks
# =============================================================================

def store_document_chunks(document_id: str, raw_text: str) -> int:
    """
    Pre-chunk and embed a document's text, storing in document_chunks table.
    Call this after a document is processed to enable instant RAG retrieval.
    Returns number of chunks stored.
    """
    if not raw_text or not raw_text.strip():
        return 0

    try:
        chunks = _chunk_text(raw_text, chunk_size=600, overlap=100)
        if not chunks:
            return 0

        # Batch encode all chunks
        embeddings = embedding_model.encode(
            chunks, show_progress_bar=False, batch_size=32
        )

        db = SessionLocal()
        try:
            # Delete existing chunks for this document
            db.execute(text(
                "DELETE FROM document_chunks WHERE document_id = :doc_id"
            ), {"doc_id": document_id})

            # Insert new chunks with embeddings
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                db.execute(text("""
                    INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding, tenant_id)
                    VALUES (:doc_id, :idx, :text, CAST(:emb AS vector), :tenant_id)
                """), {
                    "doc_id": document_id,
                    "idx": i,
                    "text": chunk,
                    "emb": str(emb.tolist()),
                    "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID,
                })

            db.commit()
            logger.info(f"✅ Stored {len(chunks)} chunks for doc {document_id}")
            return len(chunks)

        finally:
            db.close()

    except Exception as e:
        logger.error(f"❌ Failed to store chunks for doc {document_id}: {e}", exc_info=True)
        return 0


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _extract_useful_structured(analyzed: Dict) -> str:
    lines = []
    important_keys = [
        "total_deposits", "total_withdrawals", "beginning_balance", "ending_balance",
        "service_fees", "account_holder", "account_number", "statement_period",
        "loan_amount", "interest_rate", "monthly_payment", "due_date",
        "total_amount", "vendor", "invoice_number", "tax_year",
        "gross_pay", "net_pay", "employer"
    ]
    for key in important_keys:
        val = analyzed.get(key)
        if val is not None and val != "" and val != 0:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")
    for k, v in analyzed.items():
        if isinstance(v, dict) and k not in ("error", "metadata"):
            for sk, sv in v.items():
                if sv is not None and sv != "" and sk in important_keys:
                    lines.append(f"{sk.replace('_',' ').title()}: {sv}")
    return "\n".join(lines[:20])


def _summarize_tables(tables: List[Dict]) -> str:
    summaries = []
    for i, table in enumerate(tables[:3], 1):
        columns = table.get("columns", [])
        rows = table.get("rows", 0)
        if columns:
            summaries.append(f"Table {i} ({rows} rows): {', '.join(columns[:10])}")
    return "\n".join(summaries)


def _assess_confidence(answer: str) -> float:
    lower = answer.lower()
    if any(p in lower for p in ["not available", "cannot find", "not in the document",
                                  "not enough information", "unable to determine", "i don't know"]):
        return 0.2
    if any(p in lower for p in ["appears to", "seems to", "possibly", "might be", "approximately"]):
        return 0.6
    return 0.85

# """
# Intelligent Q&A service with context-aware answering
# """
#
# import logging
# from typing import Dict, List, Any, Optional
# import ollama
# from sentence_transformers import SentenceTransformer
# import numpy as np
#
# from app.core import config
#
# logger = logging.getLogger(__name__)
#
# # Use same embedding model as documents
# #embedding_model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")
# from app.config.model_factory import EmbeddingModelFactory
# embedding_model = EmbeddingModelFactory.get_model()
#
# ollama_client = ollama.Client()
#
#
# class QAService:
#
#     @staticmethod
#     def answer_question(
#             question: str,
#             document: Any,  # Document model
#             context_window: int = 3000
#     ) -> Dict[str, Any]:
#         """
#         Answer a question about a document using enhanced context
#
#         Args:
#             question: User's question
#             document: Document model with text and metadata
#             context_window: Max characters of context to use
#
#         Returns:
#             Dict with answer, confidence, sources
#         """
#
#         try:
#             # Build rich context
#             context_parts = []
#
#             # Add document metadata
#             if document.doc_type:
#                 context_parts.append(f"Document Type: {document.doc_type}")
#
#             # Add summary if available
#             if document.summary:
#                 context_parts.append(f"\nSummary:\n{document.summary}")
#
#             # Add key points if available
#             metadata = document.doc_metadata or {}
#             key_points = metadata.get("key_points", [])
#             if key_points:
#                 context_parts.append(f"\nKey Points:\n" + "\n".join(f"- {kp}" for kp in key_points[:5]))
#
#             # Add relevant text chunks
#             if document.raw_text:
#                 # Find most relevant section using semantic search
#                 relevant_text = QAService._find_relevant_text(
#                     question=question,
#                     full_text=document.raw_text,
#                     max_chars=context_window
#                 )
#                 context_parts.append(f"\nDocument Content:\n{relevant_text}")
#
#             # Add table info if available
#             tables = metadata.get("tables", [])
#             if tables:
#                 table_summary = QAService._summarize_tables(tables)
#                 context_parts.append(f"\nData Tables:\n{table_summary}")
#
#             context = "\n\n".join(context_parts)
#
#             # Build prompt
#             prompt = f"""You are analyzing a document to answer questions accurately.
#
# Context:
# {context}
#
# Question: {question}
#
# Please provide a clear, accurate answer based ONLY on the information in the context above. If the context doesn't contain enough information to answer the question, say "I cannot find that information in this document."
#
# Answer:"""
#
#             # Get answer from AI
#             response = ollama_client.generate(
#                 model=config.get_active_model(),
#                 prompt=prompt
#             )
#
#             answer = response['response'].strip()
#
#             # Assess confidence based on answer
#             confidence = QAService._assess_confidence(answer)
#
#             return {
#                 "answer": answer,
#                 "confidence": confidence,
#                 "sources": {
#                     "has_summary": bool(document.summary),
#                     "has_key_points": len(key_points) > 0,
#                     "has_tables": len(tables) > 0,
#                     "text_length": len(document.raw_text) if document.raw_text else 0
#                 }
#             }
#
#         except Exception as e:
#             logger.error(f"❌ Q&A Error: {e}")
#             return {
#                 "answer": "I encountered an error while processing your question.",
#                 "confidence": 0.0,
#                 "error": str(e)
#             }
#
#     @staticmethod
#     def _find_relevant_text(question: str, full_text: str, max_chars: int = 2000) -> str:
#         """Find most relevant section of text using embeddings"""
#
#         try:
#             # Split into chunks
#             chunk_size = 500
#             chunks = [full_text[i:i + chunk_size] for i in range(0, len(full_text), chunk_size)]
#
#             if len(chunks) == 0:
#                 return ""
#
#             # If small enough, return all
#             if len(full_text) <= max_chars:
#                 return full_text
#
#             # Encode question
#             question_embedding = embedding_model.encode(question)
#
#             # Encode chunks
#             chunk_embeddings = embedding_model.encode(chunks)
#
#             # Calculate similarities
#             similarities = np.dot(chunk_embeddings, question_embedding)
#
#             # Get top 3 most relevant chunks
#             top_indices = np.argsort(similarities)[-3:][::-1]
#
#             relevant_chunks = [chunks[i] for i in top_indices]
#             relevant_text = "\n\n".join(relevant_chunks)
#
#             # Trim to max_chars
#             if len(relevant_text) > max_chars:
#                 relevant_text = relevant_text[:max_chars] + "..."
#
#             return relevant_text
#
#         except Exception as e:
#             logger.error(f"Error finding relevant text: {e}")
#             # Fallback: return first part
#             return full_text[:max_chars]
#
#     @staticmethod
#     def _summarize_tables(tables: List[Dict]) -> str:
#         """Create text summary of table structures"""
#
#         summaries = []
#         for i, table in enumerate(tables[:3], 1):  # Max 3 tables
#             columns = table.get("columns", [])
#             rows = table.get("rows", 0)
#
#             if columns and rows:
#                 summary = f"Table {i}: {rows} rows with columns: {', '.join(columns[:10])}"
#                 if len(columns) > 10:
#                     summary += f" (and {len(columns) - 10} more)"
#                 summaries.append(summary)
#
#         return "\n".join(summaries)
#
#     @staticmethod
#     def _assess_confidence(answer: str) -> float:
#         """Assess confidence based on answer content"""
#
#         answer_lower = answer.lower()
#
#         # Low confidence indicators
#         if any(phrase in answer_lower for phrase in [
#             "cannot find",
#             "not enough information",
#             "unable to determine",
#             "not clear",
#             "i don't know"
#         ]):
#             return 0.3
#
#         # Medium confidence
#         if any(phrase in answer_lower for phrase in [
#             "appears to",
#             "seems to",
#             "possibly",
#             "might be"
#         ]):
#             return 0.6
#
#         # High confidence (definitive answer)
#         return 0.85