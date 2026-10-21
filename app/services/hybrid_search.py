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

def _reranker_source() -> str:
    """The bundled copy (DMG / Docker / dev: models/bge-reranker-v2-m3) — never a download.
    RERANKER_PATH overrides the folder; RERANKER_MODEL names a different model to fetch."""
    from pathlib import Path
    if os.getenv("RERANKER_MODEL"):
        return os.environ["RERANKER_MODEL"]
    candidates = [os.getenv("RERANKER_PATH")]
    if os.getenv("MODEL_PATH"):                       # bundled embedding model's folder → its sibling
        candidates.append(str(Path(os.environ["MODEL_PATH"]).parent / "bge-reranker-v2-m3"))
    candidates.append(str(Path(__file__).resolve().parents[2] / "models" / "bge-reranker-v2-m3"))
    for c in candidates:
        if c and (Path(c) / "model.safetensors").exists():
            return c
    return "BAAI/bge-reranker-v2-m3"


_RERANKER_ID = _reranker_source()
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


def _filters(doc_ids: Optional[Sequence[str]], doc_types: Optional[Sequence[str]], params: Dict,
             exclude_doc_ids: Optional[Sequence[str]] = None) -> str:
    sql = ""
    if exclude_doc_ids:
        sql += " AND d.id::text <> ALL(:ex_doc_ids)"
        params["ex_doc_ids"] = list(exclude_doc_ids)
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


_REF_RE = None


def _references(question: str) -> List[tuple]:
    """Parts of a document the question names: ("table", "1"), ("schedule", "C"), ("page", "47")…"""
    import re as _re
    global _REF_RE
    if _REF_RE is None:
        # not "box" / "line": those labels repeat on every form of a kind (W-2 box 1), so they
        # are left to normal search instead of pulling one form's whole page to the front
        _REF_RE = _re.compile(r"\b(table|schedule|part|section|exhibit|appendix|attachment|page)\s+"
                              r"([0-9]{1,3}[A-Z]?|[IVX]{1,5}|[A-Z])\b(?![-./]\d)", _re.I)
    out = []
    for kind, ident in _REF_RE.findall(question or ""):
        if kind.lower() in ("part", "section", "schedule", "appendix", "exhibit", "attachment") and ident.isdigit() and len(ident) > 2:
            continue
        out.append((kind.lower(), ident))
    return list(dict.fromkeys(out))[:3]


def _reference_chunks(db, question: str, extra: str, params: Dict) -> List[Dict]:
    """Chunks of the pages that carry a named heading ("TABLE 1—Summary of Revenues…"), or of a
    named page. Index / contents pages (many "Table N … 43" lines) are skipped. These go to the
    model first: similarity search alone ranked page 37 above the table the question named."""
    import re as _re
    refs = _references(question)
    if not refs:
        return []
    found: List[Dict] = []
    for kind, ident in refs:
        if kind == "page":
            if not ident.isdigit():
                continue
            rows = db.execute(text(f"""
                SELECT c.id::text AS chunk_id, c.document_id::text AS doc_id, c.chunk_text, c.chunk_index,
                       d.filename, d.doc_type
                  FROM document_chunks c JOIN documents d ON d.id = c.document_id
                 WHERE d.status = 'completed' AND d.tenant_id = :tenant_id {extra} AND c.page_number = :pg
                 ORDER BY d.uploaded_at DESC NULLS LAST, c.chunk_index LIMIT 8"""),
                {**params, "pg": int(ident)}).mappings().fetchall()
            found += [dict(r) for r in rows]
            continue
        # heading at the start of a line: "TABLE 1—", "Table 1:", "SCHEDULE C (Form 1040)"
        pat = rf"(^|\n)\s*{kind}\s*{_re.escape(ident)}(?![0-9A-Za-z])"
        pages = db.execute(text(f"""
            SELECT p.document_id::text AS doc_id, p.page_number, p.text
              FROM document_pages p JOIN documents d ON d.id::text = p.document_id::text
             WHERE d.status = 'completed' AND d.tenant_id = :tenant_id {extra} AND p.text ~* :pat
             LIMIT 40"""), {**params, "pat": pat}).mappings().fetchall()
        best = []
        for pg in pages:
            lines = pg["text"].split("\n")
            index_lines = sum(1 for ln in lines if _re.match(r"\s*(table|schedule|part|section)\s+\S+\s.*\s\d{1,3}\s*$", ln, _re.I))
            if index_lines >= 5:                       # a contents page, not the table itself
                continue
            nums = len(_re.findall(r"\d[\d,]{2,}", pg["text"]))
            best.append((nums, pg["doc_id"], pg["page_number"]))
        # A table can run over several pages ("(1 of 2)"): take up to 3 matching pages of the
        # document with the most matches, in page order
        by_doc: Dict[str, List[int]] = {}
        for _, did, pn in best:
            by_doc.setdefault(did, []).append(pn)
        top_doc = max(by_doc, key=lambda k: len(by_doc[k])) if by_doc else None
        for did, pn in [(top_doc, p) for p in sorted(by_doc.get(top_doc, []))[:3]]:
            rows = db.execute(text("""
                SELECT c.id::text AS chunk_id, c.document_id::text AS doc_id, c.chunk_text, c.chunk_index,
                       d.filename, d.doc_type
                  FROM document_chunks c JOIN documents d ON d.id = c.document_id
                 WHERE c.document_id = :d AND c.page_number = :pg ORDER BY c.chunk_index"""),
                {"d": did, "pg": pn}).mappings().fetchall()
            found += [dict(r) for r in rows]
    return found


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


_OLD_MARKERS = [
    (__import__("re").compile(r"\[Form fields[^\]\n]*\]"), "FORM FIELDS (read from the page image; every value checked against the page text):"),
    (__import__("re").compile(r"\[Table rows\]"), "TABLE ROWS:"),
    (__import__("re").compile(r"\[Text in images on this page\]"), "TEXT IN IMAGES ON THIS PAGE:"),
    (__import__("re").compile(r"\[…continued from page (\d+)\]"), r"(continued from page \1)"),
    (__import__("re").compile(r"^\[page (\d+)\]$", __import__("re").M), r"(page \1)"),
]


def _clean(chunk_text: Optional[str]) -> str:
    """Drop the internal '| Document <uuid>' label stored on every chunk, and rewrite section
    markers stored in square brackets by earlier ingests — the model cited "[Form fields …]"
    as if it were a file name."""
    t = _LABEL_RE.sub("\n", chunk_text or "")
    for pat, rep in _OLD_MARKERS:
        t = pat.sub(rep, t)
    return t.strip()


_PREFER_BONUS = 0.15        # added to passages from preferred (guessed) documents
_OUTSIDE_MIN = 0.5          # other documents need this reranker score to be included


_PART_SPLIT = None


def question_parts(question: str) -> List[str]:
    """Split a multi-part question into its parts: separate sentences / questions, and clauses
    joined by ", and what/which/how…" or "Furthermore". Context sentences (no question) are
    attached to the first part. One part → [question]."""
    import re as _re
    global _PART_SPLIT
    if _PART_SPLIT is None:
        _PART_SPLIT = _re.compile(r"(?<=[?])\s+|(?<=\.)\s+(?=(?:What|Which|How|When|Where|Who|Why|Furthermore|Also|Additionally)\b)"
                                  r"|,?\s+and\s+(?=(?:what|which|how much|how many|how|when|where|who)\b)"
                                  r"|\s*\b(?:Furthermore|Additionally|Also),?\s+", _re.I)
    q = (question or "").strip()
    pieces = [p.strip(" ,;") for p in _PART_SPLIT.split(q) if p and p.strip(" ,;")]
    ask = _re.compile(r"\?|\b(what|which|how|when|where|who|why)\b|^(list|show|give|find|compare)\b", _re.I)
    parts, context = [], []
    for p in pieces:
        (parts if ask.search(p) else context).append(p)
    if len(parts) <= 1:
        return [q]
    if context:                      # framing sentences ("In FY 2023-24, …") apply to every part
        parts = [" ".join(context) + " " + p for p in parts]
    return [p if p.endswith("?") else p + "?" for p in parts[:5]]


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _dates_in(text_: str) -> set:
    """(year|None, month, day|None) for every date written in the text."""
    import re as _re
    t, out = text_ or "", set()
    for m, d, y in _re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", t):
        y = int(y) + (2000 if len(y) == 2 else 0)
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            out.add((y, int(m), int(d)))
    for y, m, d in _re.findall(r"\b(20\d\d|19\d\d)-(\d{2})-(\d{2})\b", t):
        out.add((int(y), int(m), int(d)))
    for mon, d, y in _re.findall(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b,?\s*(20\d\d|19\d\d)?",
                                 t, _re.I):
        out.add((int(y) if y else None, _MONTHS[mon.lower()[:3]], int(d)))
    for mon, y in _re.findall(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(20\d\d|19\d\d)\b", t, _re.I):
        out.add((int(y), _MONTHS[mon.lower()[:3]], None))
    return out


_LAST_MATCHED: List[str] = []
_TYPE_WORDS = {
    "w2": ["w-2", "w2", "wage and tax"], "payroll": ["pay stub", "paystub", "payslip", "pay slip", "payroll", "earnings statement"],
    "tax_form": ["w-2", "w2", "tax form", "1099", "1040"], "bank_statement": ["bank statement", "statement"],
    "uscis_receipt": ["i-797", "i797", "receipt notice", "approval notice", "h-1b", "h1b", "h-4", "h4"],
    "passport": ["passport"], "mortgage_statement": ["mortgage"], "property_tax_bill": ["property tax"],
    "invoice": ["invoice"], "form_1040": ["1040", "tax return"], "schedule_k1": ["k-1", "k1"],
    "form_1099_nec": ["1099-nec", "1099 nec", "1099"], "w9": ["w-9", "w9"],
}


def _identity_exclusions(question: str, doc_ids, doc_types) -> List[str]:
    """Similar documents (same type) the question rules out by date: "the Jan 9 pay stub",
    "my 2024 W-2", "December 2025 statement". Returns the ids of same-type documents that do NOT
    match, so only the right one(s) compete. Other types are never excluded; no match → nothing."""
    import re as _re
    q_dates = {d for d in _dates_in(question)}
    q_years = {int(y) for y in _re.findall(r"(?<![\d-])(19\d\d|20\d\d)(?![\d-])", question)}
    if not q_dates and not q_years:
        return []
    params: Dict = {"tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID}
    extra = _filters(doc_ids, doc_types, params)
    db = SessionLocal()
    try:
        rows = db.execute(text(f"""
            SELECT d.id::text AS id, d.doc_type, d.filename, left(coalesce(p.text, ''), 2500) AS head
              FROM documents d LEFT JOIN document_pages p ON p.document_id::text = d.id::text AND p.page_number = 1
             WHERE d.status = 'completed' AND d.tenant_id = :tenant_id {extra}"""), params).mappings().fetchall()
    finally:
        db.close()

    def match(r) -> bool:
        if q_dates:
            # a date in the file name ("Statement for Jan 9, 2026") decides; else the first page
            have = _dates_in(r["filename"]) or _dates_in(r["head"])
            for qy, qm, qd in q_dates:
                for hy, hm, hd in have:
                    if qm == hm and (qd is None or qd == hd) and (qy is None or hy is None or qy == hy):
                        return True
            return False
        # year only: the document's own header first ("2021 W-2"), the file name only if the header has no year
        head_years = {int(y) for y in _re.findall(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", r["head"][:300])}
        years = head_years or {int(y) for y in _re.findall(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", r["filename"])}
        return bool(years & q_years)

    # Look-alikes: same classified type, or same first-page header once digits are removed
    # ("2022 W-2 and EARNINGS SUMMARY" filed as tax_form is still a W-2 like the others)
    def head_sig(r) -> str:
        return _re.sub(r"[\W\d_]+", "", (r["head"] or "")[:120]).lower()[:48]
    by_type: Dict[str, List] = {}
    for r in rows:
        by_type.setdefault("t:" + (r["doc_type"] or ""), []).append(r)
        sig = head_sig(r)
        if len(sig) >= 20:
            by_type.setdefault("h:" + sig, []).append(r)
    # A year alone ("2024") is common text, so it narrows only the kind of document the question
    # names ("my 2024 W-2"); a full date or month narrows any look-alikes.
    ql = question.lower()
    def named(docs) -> bool:
        for r in docs:
            words = _TYPE_WORDS.get(r["doc_type"] or "", [(r["doc_type"] or "").replace("_", " ")])
            if any(w and w in ql for w in words):
                return True
        return False

    exclude, matched = [], []
    for t, docs in by_type.items():
        if len(docs) < 2:
            continue
        if not q_dates and not named(docs):
            continue
        hits = [r for r in docs if match(r)]
        if hits and len(hits) < len(docs):       # the question picks some of these look-alikes
            exclude += [r["id"] for r in docs if r not in hits]
            matched.extend(r["id"] for r in hits)
    _LAST_MATCHED[:] = list(dict.fromkeys(m for m in matched if m not in exclude))
    return list(dict.fromkeys(exclude))


def hybrid_search(question: str, queries: Sequence[str] = (), doc_ids: Optional[Sequence[str]] = None,
                  doc_types: Optional[Sequence[str]] = None, top_n: int = 8,
                  prefer_doc_ids: Optional[Sequence[str]] = None,
                  prefer_doc_types: Optional[Sequence[str]] = None,
                  keep_doc_ids: Optional[Sequence[str]] = None) -> List[Dict]:
    """Divide and conquer: a multi-part question is searched part by part (each part keeps its
    own best passages), then merged round-robin so no part is crowded out. Named references
    ("Table 1") in any part are honoured for that part."""
    parts = question_parts(question)
    exclude = []
    try:
        exclude = _identity_exclusions(question, doc_ids, doc_types)
        if exclude:
            logger.info(f"Similar documents ruled out by the question's date/year: {len(exclude)}")
            # every look-alike the question picked ("December 2025 pay stubs") keeps its best passage
            if _LAST_MATCHED:
                # the documents the question picked get their own candidates and rank first
                prefer_doc_ids = list(dict.fromkeys([*(prefer_doc_ids or []), *_LAST_MATCHED]))
            if 1 < len(_LAST_MATCHED) <= 8:
                keep_doc_ids = list(dict.fromkeys([*(keep_doc_ids or []), *_LAST_MATCHED]))
                top_n = max(top_n, len(_LAST_MATCHED) + 3)
    except Exception as e:
        logger.warning(f"Date/year matching skipped: {e}")
    kw = dict(doc_ids=doc_ids, doc_types=doc_types, prefer_doc_ids=prefer_doc_ids,
              prefer_doc_types=prefer_doc_types, keep_doc_ids=keep_doc_ids, exclude_doc_ids=exclude)
    if len(parts) == 1:
        return _hybrid_one(question, queries, top_n=top_n, **kw)
    logger.info(f"Divide: {len(parts)} parts → {parts}")
    # Hybrid: the whole question first (keeps the subject that later parts only refer to —
    # "this figure"), then each part on its own
    per = [_hybrid_one(question, queries, top_n=max(4, top_n // 2), **kw)]
    per += [_hybrid_one(p, [p], top_n=max(4, top_n // len(parts) + 3), **kw) for p in parts]
    merged, seen, budget = [], set(), _CONTEXT_CHARS
    for i in range(max(len(x) for x in per)):
        for lst in per:
            if i < len(lst):
                h = lst[i]
                key = (h["doc_id"], h["chunk_index"])
                if key in seen or budget - len(h["text"]) < 0:
                    continue
                seen.add(key)
                budget -= len(h["text"])
                merged.append(h)
    return merged


def _hybrid_one(question: str, queries: Sequence[str] = (), doc_ids: Optional[Sequence[str]] = None,
                doc_types: Optional[Sequence[str]] = None, top_n: int = 8,
                prefer_doc_ids: Optional[Sequence[str]] = None,
                prefer_doc_types: Optional[Sequence[str]] = None,
                keep_doc_ids: Optional[Sequence[str]] = None,
                exclude_doc_ids: Optional[Sequence[str]] = None) -> List[Dict]:
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
    extra = _filters(doc_ids, doc_types, params, exclude_doc_ids)
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
    ref_rows: List[Dict] = []
    try:
        try:
            ref_rows = _reference_chunks(db, question, extra, params)
        except Exception as e:
            db.rollback()
            logger.warning(f"Reference lookup failed: {e}")
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
    if not fused and not ref_rows:
        return []

    pool = sorted(fused, key=fused.get, reverse=True)[:_RERANK_POOL]
    reranker = _get_reranker()
    if not pool:
        order = []
    elif reranker is not None:
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
        # Each kept document's best passage moves up, right after the top one — otherwise the
        # top document's passages fill the context and the others never reach the model
        if keep:
            firsts, seen_docs = [], set()
            for o in order:
                d = rows[o[0]]["doc_id"]
                if d in keep and d not in seen_docs:
                    seen_docs.add(d)
                    firsts.append(o)
            order = order[:1] + [o for o in firsts if o != order[0]] + [o for o in order[1:] if o not in firsts]

    # Sliding window: each of the best passages brings its neighbours (previous / next chunk of the
    # same document), so a label in one chunk and its value in the next arrive together.
    try:
        neigh_db = SessionLocal()
        try:
            expanded, have = [], {c for c, _ in order}
            for rank, (cid, score) in enumerate(order):
                expanded.append((cid, score))
                if rank >= 4:
                    continue
                r = rows[cid]
                for nb in neigh_db.execute(text("""
                    SELECT c.id::text AS chunk_id, c.document_id::text AS doc_id, c.chunk_text, c.chunk_index,
                           d.filename, d.doc_type
                      FROM document_chunks c JOIN documents d ON d.id = c.document_id
                     WHERE c.document_id = :d AND c.chunk_index IN (:a, :b)
                     ORDER BY c.chunk_index"""), {"d": r["doc_id"], "a": r["chunk_index"] - 1,
                                                    "b": r["chunk_index"] + 1}).mappings():
                    nb = dict(nb)
                    if nb["chunk_id"] not in have:
                        have.add(nb["chunk_id"])
                        rows[nb["chunk_id"]] = nb
                        expanded.append((nb["chunk_id"], score * 0.9))
            order = expanded
        finally:
            neigh_db.close()
    except Exception as e:
        logger.warning(f"Neighbour expansion skipped: {e}")

    # The page the question names comes first, in page order
    if ref_rows:
        ref_ids = []
        for r in ref_rows:
            rows.setdefault(r["chunk_id"], r)
            if r["chunk_id"] not in ref_ids:
                ref_ids.append(r["chunk_id"])
        order = [(c, 1.0) for c in ref_ids] + [o for o in order if o[0] not in set(ref_ids)]
        top_n = max(top_n, min(len(ref_ids) + 3, 24))
        logger.info(f"Named reference(s) {_references(question)} → {len(ref_ids)} passage(s) placed first")

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
        key = hash(" ".join(txt.split()))
        if key in seen_text:          # same passage twice (file uploaded twice) — one copy is enough
            continue
        seen_text.add(key)
        # Best passages go in whole while the budget lasts: a table cut at 1.4k
        # loses its tail ("list all Bradley-Burns payments" stopped at Ventura).
        # several documents the question picked ("2023 and 2024 W-2s") share the space fairly
        many = keep_doc_ids and len(set(keep_doc_ids)) > 1
        limit = min(_PASSAGE_CHARS if many else (_TOP_PASSAGE_CHARS if len(out) < 3 else _PASSAGE_CHARS), budget)
        budget -= min(len(txt), limit)
        out.append({"doc_id": r["doc_id"], "filename": r["filename"], "doc_type": r["doc_type"],
                    "text": txt[:limit], "similarity": round(float(score), 3),
                    "chunk_index": r["chunk_index"]})
    return out
