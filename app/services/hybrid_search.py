"""
Hybrid retrieval: vector + keyword candidates, fused with Reciprocal Rank
Fusion, then reranked by a cross-encoder that reads question and passage
together.

Why: vector search alone misses exact terms (tickers, form/parcel numbers,
"Total revenues"), and the old doc-type shortcut returned the *first* 30
chunks of a document — fine for a 1-page passport, useless for a 386-chunk
10-K. Here a doc-type hint only narrows the candidate pool; ranking still
decides which passages the LLM sees.

Env:
  RERANKER_MODEL   cross-encoder id (default BAAI/bge-reranker-v2-m3, multilingual)
  RERANKER=0       disable reranking (fused order is used)
"""
import logging
import math
import os
import threading
from typing import Dict, List, Optional, Sequence

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id

logger = logging.getLogger(__name__)

_RERANKER_ID = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
_RERANK_ON = os.getenv("RERANKER", "1") != "0"
_RRF_K = 60
_PER_LIST = 40          # candidates per query per method
_RERANK_POOL = 40       # fused candidates sent to the cross-encoder
_PASSAGE_CHARS = 1400   # per passage; 8 x 1.4k keeps the LLM prompt near its old size
_TOP_PASSAGE_CHARS = 5000   # top 3 passages: whole chunk
_CONTEXT_CHARS = 12000      # all passages together (~4k tokens of the 8k window)

_reranker = None
_reranker_failed = False
_lock = threading.Lock()


def _get_reranker():
    global _reranker, _reranker_failed
    if _reranker is not None or _reranker_failed or not _RERANK_ON:
        return _reranker
    with _lock:
        if _reranker is None and not _reranker_failed:
            try:
                from sentence_transformers import CrossEncoder
                from app.core.model_lock import MODEL_LOAD_LOCK
                with MODEL_LOAD_LOCK:
                    _reranker = CrossEncoder(_RERANKER_ID, max_length=512)
                logger.info(f"🔁 Reranker loaded: {_RERANKER_ID}")
            except Exception as e:
                _reranker_failed = True
                logger.warning(f"⚠️ Reranker unavailable ({e}) — using fused ranking")
    return _reranker


def warm_up() -> None:
    r = _get_reranker()
    if r is not None:
        r.predict([("warm up", "warm up")])


def _filters(doc_ids: Optional[Sequence[str]], doc_types: Optional[Sequence[str]], params: Dict) -> str:
    sql = ""
    if doc_ids:
        sql += " AND d.id::text = ANY(:doc_ids)"
        params["doc_ids"] = list(doc_ids)
    if doc_types:
        sql += " AND d.doc_type = ANY(:doc_types)"
        params["doc_types"] = list(doc_types)
    return sql


def _vector_candidates(db, query_vec: List[float], extra: str, params: Dict) -> List[Dict]:
    """
    Two shapes, chosen for correctness at scale:
    - Scoped (specific documents/types): exact distance over just those chunks.
      Small sets; exact is precise and avoids HNSW's filter-after-index problem.
    - Tenant-wide: HNSW index scan. ORDER BY the distance directly on the chunks
      table (filtered by its own tenant_id) is the only shape pgvector can serve
      from the index; a JOIN before the ORDER BY forces a full scan. pgvector 0.5
      filters *after* the index returns ef_search rows, so ef_search is raised
      and document status is checked afterwards.
    """
    vec = str(query_vec)
    if extra.strip():
        rows = db.execute(text(f"""
            SELECT c.id::text AS chunk_id, c.document_id::text AS doc_id, c.chunk_text, c.chunk_index,
                   d.filename, d.doc_type,
                   1 - (c.embedding <=> CAST(:vec AS vector(768))) AS score
              FROM document_chunks c JOIN documents d ON d.id::text = c.document_id
             WHERE c.embedding IS NOT NULL AND d.status = 'completed' AND d.tenant_id = :tenant_id {extra}
             ORDER BY c.embedding <=> CAST(:vec AS vector(768))
             LIMIT :lim
        """), {**params, "vec": vec, "lim": _PER_LIST}).mappings().fetchall()
        return [dict(r) for r in rows]

    db.execute(text("SET LOCAL hnsw.ef_search = 200"))
    rows = db.execute(text("""
        WITH nn AS (
            SELECT c.id, c.document_id, c.chunk_text, c.chunk_index,
                   c.embedding <=> CAST(:vec AS vector(768)) AS dist
              FROM document_chunks c
             WHERE c.tenant_id = CAST(:tenant_id AS uuid)
             ORDER BY c.embedding <=> CAST(:vec AS vector(768))
             LIMIT 200
        )
        SELECT nn.id::text AS chunk_id, nn.document_id::text AS doc_id, nn.chunk_text, nn.chunk_index,
               d.filename, d.doc_type, 1 - nn.dist AS score
          FROM nn JOIN documents d ON d.id::text = nn.document_id
         WHERE d.status = 'completed'
         ORDER BY nn.dist
         LIMIT :lim
    """), {**params, "vec": vec, "lim": _PER_LIST}).mappings().fetchall()
    return [dict(r) for r in rows]


def _keyword_candidates(db, query: str, extra: str, params: Dict) -> List[Dict]:
    # OR over the query's stemmed terms; ts_rank_cd rewards chunks matching more
    # (and closer) terms. AND-only missed chunks that lack e.g. the company name.
    rows = db.execute(text(f"""
        WITH q AS (
            SELECT to_tsquery('english', string_agg(lexeme, ' | ')) AS tsq
              FROM unnest(to_tsvector('english', :q)) AS t(lexeme, positions, weights)
        )
        SELECT c.id::text AS chunk_id, c.document_id::text AS doc_id, c.chunk_text, c.chunk_index,
               d.filename, d.doc_type,
               ts_rank_cd(to_tsvector('english', coalesce(c.chunk_text, '')), q.tsq, 32) AS score
          FROM q, document_chunks c JOIN documents d ON d.id = c.document_id
         WHERE q.tsq IS NOT NULL
           AND to_tsvector('english', coalesce(c.chunk_text, '')) @@ q.tsq
           AND d.status = 'completed' AND d.tenant_id = :tenant_id {extra}
         ORDER BY score DESC
         LIMIT :lim
    """), {**params, "q": query, "lim": _PER_LIST}).mappings().fetchall()
    return [dict(r) for r in rows]


_LABEL_RE = __import__("re").compile(r"\s*\|\s*Document [0-9a-f-]{36}\s*")


_STOP = set("""a an the of to in on for and or with by at from as is are was were be been being what which who whom
whose when where why how my our your their his her its this that these those me i we you do does did can could
should would will shall may might much many please show tell give list find get let know about""".split())


def _question_phrases(question: str) -> List[str]:
    """Consecutive content-word bigrams/trigrams: 'total revenues', 'ending balance'."""
    import re as _re
    words = [w for w in _re.findall(r"[a-z0-9][a-z0-9.,'-]*", question.lower())]
    words = [w.strip(".,'") for w in words]
    content = [w for w in words if w not in _STOP and len(w) > 1 and not w.endswith("'s")]
    grams = []
    for n in (3, 2):
        grams += [" ".join(content[i:i + n]) for i in range(len(content) - n + 1)]
    return grams


def _rare_name_boost(question: str, pool: List[str], rows: Dict, scores: List[float]) -> List[float]:
    """
    A capitalised name from the question ("Trinidad", "Costco", "LXRX") that
    occurs in only a few candidate passages is near-certain evidence — even in
    a page of numbers the reranker scores near zero.
    """
    import re as _re
    names = {w for w in _re.findall(r"\b[A-Z][A-Za-z0-9&'-]{3,}\b", question)
             if w.lower() not in _STOP and not _re.fullmatch(r"(What|When|Where|Which|Who|How|Can|Could|Please|Show|Tell|Give)", w)}
    if not names:
        return scores
    texts = [(rows[c]["chunk_text"] or "") for c in pool]
    out = list(scores)
    for name in names:
        pat = _re.compile(r"\b" + _re.escape(name) + r"\b", _re.I)
        hits = [i for i, t in enumerate(texts) if pat.search(t)]
        if 0 < len(hits) <= max(3, len(pool) // 10):
            for i in hits:
                out[i] = min(1.0, max(out[i], 0.6) + 0.2)
    return out


def _phrase_boost(phrases: List[str], chunk_text: Optional[str]) -> float:
    # An exact multi-word term from the question is strong evidence the passage
    # is about it — cross-encoders under-score dense financial tables.
    low = " ".join((chunk_text or "").lower().replace("|", " ").split())
    for p in phrases:
        if p in low:
            return 0.35 if p.count(" ") >= 2 else 0.25
    return 0.0


def _for_reranker(chunk_text: Optional[str]) -> str:
    """
    Markdown tables as plain rows: "| Total revenues | 49,803 | 31,081 |" →
    "Total revenues: 49,803; 31,081". Separator rows (|---|) are dropped. Cross-
    encoders are trained on prose; raw pipes and dashes sank financial tables.
    """
    out = []
    for line in _clean(chunk_text).splitlines():
        if "|" not in line:
            out.append(line)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        cells = [c for c in cells if c and not set(c) <= set("-: ")]
        if not cells:
            continue
        out.append(cells[0] + (": " + "; ".join(cells[1:]) if len(cells) > 1 else ""))
    return "\n".join(out)


def _clean(chunk_text: Optional[str]) -> str:
    """Drop the internal '| Document <uuid>' label stored on every chunk."""
    return _LABEL_RE.sub("\n", chunk_text or "").strip()


_PREFER_BONUS = 0.15        # added to passages from preferred (guessed) documents
_OUTSIDE_MIN = 0.5          # other documents need this reranker score to be included


def hybrid_search(question: str, queries: Sequence[str] = (), doc_ids: Optional[Sequence[str]] = None,
                  doc_types: Optional[Sequence[str]] = None, top_n: int = 8,
                  prefer_doc_ids: Optional[Sequence[str]] = None,
                  prefer_doc_types: Optional[Sequence[str]] = None,
                  keep_doc_ids: Optional[Sequence[str]] = None) -> List[Dict]:
    """
    Returns up to top_n passages: {doc_id, filename, doc_type, text, similarity, chunk_index}.
    `similarity` is the reranker's probability (0–1) when reranking ran, otherwise
    a fused score scaled to 0–1, so existing confidence checks keep working.

    doc_ids / doc_types are hard limits (the user's own scope). prefer_doc_ids /
    prefer_doc_types are guesses (planner doc type, date filter): those documents
    always get candidates and a bonus, and anything else must clear a high
    reranker score — so a wrong guess can't hide the right document, while weakly
    related documents still can't leak in.
    """
    from app.services.document_react_agent import _get_embedding_model

    all_queries = list(dict.fromkeys([question, *[q for q in queries if q and q.strip()]]))
    params: Dict = {"tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID}
    extra = _filters(doc_ids, doc_types, params)
    prefer = bool(prefer_doc_ids or prefer_doc_types)
    pparams = dict(params)
    pextra = extra
    if prefer_doc_ids:
        pextra += " AND d.id::text = ANY(:p_doc_ids)"
        pparams["p_doc_ids"] = list(prefer_doc_ids)
    if prefer_doc_types:
        pextra += " AND d.doc_type = ANY(:p_doc_types)"
        pparams["p_doc_types"] = list(prefer_doc_types)

    model = _get_embedding_model()
    vecs = model.encode(all_queries)
    fused: Dict[str, float] = {}
    rows: Dict[str, Dict] = {}
    db = SessionLocal()
    try:
        # Named terms ("Trinidad") get their own keyword pass: in an OR query they
        # are outranked by pages dense in common words ("transactions and use tax").
        import re as _re
        names = {w for w in _re.findall(r"\b[A-Z][A-Za-z0-9&-]{3,}\b", question) if w.lower() not in _STOP
                 and not _re.fullmatch(r"(What|When|Where|Which|Who|How|Can|Could|Please|Show|Tell|Give|City|County)", w)}
        for name in names:
            for rank, r in enumerate(_keyword_candidates(db, name, extra, params)):
                fused[r["chunk_id"]] = fused.get(r["chunk_id"], 0.0) + 1.0 / (_RRF_K + rank + 1)
                rows.setdefault(r["chunk_id"], r)
        for q, v in zip(all_queries, vecs):
            lists = [_vector_candidates(db, v.tolist(), extra, params), _keyword_candidates(db, q, extra, params)]
            if prefer:
                lists += [_vector_candidates(db, v.tolist(), pextra, pparams), _keyword_candidates(db, q, pextra, pparams)]
            for ranked in lists:
                for rank, r in enumerate(ranked):
                    fused[r["chunk_id"]] = fused.get(r["chunk_id"], 0.0) + 1.0 / (_RRF_K + rank + 1)
                    rows.setdefault(r["chunk_id"], r)
    finally:
        db.close()
    if not fused:
        return []

    pool = sorted(fused, key=fused.get, reverse=True)[:_RERANK_POOL]
    reranker = _get_reranker()
    if reranker is not None:
        scores = reranker.predict([(question, _for_reranker(rows[c]["chunk_text"])[:2000]) for c in pool])
        probs = [float(s) if 0.0 <= float(s) <= 1.0 else 1 / (1 + math.exp(-float(s))) for s in scores]
        phrases = _question_phrases(question)
        boosted = [min(1.0, p + _phrase_boost(phrases, rows[c]["chunk_text"])) for c, p in zip(pool, probs)]
        boosted = _rare_name_boost(question, pool, rows, boosted)
        pids, ptypes = set(prefer_doc_ids or []), set(prefer_doc_types or [])
        # A guess that matches nothing (e.g. type "stock_dataset" with no such
        # documents) must not filter anything — plain ranking decides.
        if prefer and any(rows[c]["doc_id"] in pids or rows[c]["doc_type"] in ptypes for c in pool):
            def _preferred(c):
                # Either signal is enough: a named type ("resume") must not be vetoed by
                # a date/name filter that guessed a different document, or vice versa.
                return rows[c]["doc_id"] in pids or rows[c]["doc_type"] in ptypes
            boosted = [min(1.0, b + _PREFER_BONUS) if _preferred(c) else (b if b >= _OUTSIDE_MIN else -1.0)
                       for c, b in zip(pool, boosted)]
        ranked = sorted(zip(pool, boosted), key=lambda x: -x[1])
        order = [o for o in ranked if o[1] >= 0] or [(c, max(b, 0.0)) for c, b in ranked]
    else:
        top = fused[pool[0]]
        order = [(c, fused[c] / top) for c in pool]

    if reranker is not None and order:
        # Drop passages far below the best one: extra near-irrelevant text (e.g.
        # transaction lists) pulls a small LLM off the exact answer it was given.
        floor = max(0.05, order[0][1] * 0.25)
        # Documents the question names (filename "H-1B Approval Notice" for "H1B")
        # keep their best passage even below the floor.
        keep, kept = set(keep_doc_ids or []), set()
        def _keep(c):
            d = rows[c]["doc_id"]
            if d in keep and d not in kept:
                kept.add(d)
                return True
            return False
        order = [o for i, o in enumerate(order) if i < 2 or o[1] >= floor or _keep(o[0])]

    out = []
    seen_text = set()
    budget = _CONTEXT_CHARS
    for cid, score in order:
        if len(out) >= top_n or budget <= 200:
            break
        r = rows[cid]
        txt = _clean(r["chunk_text"])
        if len(txt) < 20:
            continue
        key = " ".join(txt.split())[:400]
        if key in seen_text:          # same file uploaded twice — one copy is enough
            continue
        seen_text.add(key)
        # Best passages go in whole while the budget lasts: a table cut at 1.4k
        # loses its tail ("list all Bradley-Burns payments" stopped at Ventura).
        limit = min(_TOP_PASSAGE_CHARS if len(out) < 3 else _PASSAGE_CHARS, budget)
        budget -= min(len(txt), limit)
        out.append({"doc_id": r["doc_id"], "filename": r["filename"], "doc_type": r["doc_type"],
                    "text": txt[:limit], "similarity": round(float(score), 3),
                    "chunk_index": r["chunk_index"]})
    return out
