"""
Page-accurate ingestion: every page read, stored and chunked on its own.

Why this exists
  The earlier pipeline split Docling's markdown on headings and stored the heading
  counter as "page_number", re-emitted tables several times, cut chunks up to 4,000+
  characters (the embedding model reads ~1,500 — the rest was never searchable),
  separated table labels from their values, and stored redacted text, so facts like
  policy, receipt and parcel numbers were erased before search or the model saw them.

What happens now (PDFs)
  1. Each page is read on its own. Text layer when the page has one; otherwise the page
     is rendered at 300 dpi and OCR'd with Tesseract. Tables found on the page are also
     written as "Header: value" rows so every value carries its label.
  2. Pages are stored in document_pages (document, page number, text, method, size).
  3. Each page is cut into chunks of ~900 characters on line boundaries, with a short
     overlap; a chunk never crosses a page. Identical chunks are dropped.
  4. Every chunk starts with "<filename> · page N", the only prefix — it helps both the
     embedding and the reader — and stays inside the embedding model's input limit.

Other file types keep their extracted text, chunked the same way under "page 1".
Original text is stored. Masking happens where data leaves the machine (cloud models,
webhooks, connectors), not in storage.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

CHUNK_CHARS = 900          # all-mpnet-base-v2 reads 384 word pieces ≈ 1,500 chars; leave room for the prefix
CHUNK_OVERLAP = 150
MIN_TEXT_LAYER = 40        # fewer characters than this on a page → treat it as a scan and OCR it
OCR_DPI = 300


@dataclass
class Page:
    number: int            # 1-based, as printed in a PDF viewer
    text: str
    method: str            # text | ocr | text+ocr | none


# ── Page extraction ──────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    s = s.replace("\x00", "")
    s = re.sub(r"/uni([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _covered(cells: List[str], text_lines: List[str]) -> bool:
    """The plain text already has this row on one line: its label and every value."""
    vals = [re.sub(r"\s+", " ", c).strip() for c in cells if c and c.strip()]
    if len(vals) < 2:
        return True
    norm = lambda x: re.sub(r"[\s,$]", "", x).lower()
    want = [norm(v) for v in vals]
    return any(all(w in norm(ln) for w in want) for ln in text_lines)


def _tables(page, page_text: str = "") -> str:
    """Tables on the page as labelled rows: 'Header: value | Header: value' — only rows whose
    label and values aren't already together on one line of the page text (no duplicates)."""
    try:
        found = page.find_tables()
    except Exception:
        return ""
    out = []
    for t in getattr(found, "tables", []) or []:
        try:
            rows = t.extract()
        except Exception:
            continue
        rows = [[_clean(str(c or "")).replace("\n", " ") for c in r] for r in rows if any(c for c in r)]
        if len(rows) < 2:
            continue
        header = rows[0]
        text_lines = page_text.split("\n")
        rows = [rows[0]] + [r for r in rows[1:] if not _covered(r, text_lines)]
        if len(rows) < 2:
            continue
        labelled = sum(1 for h in header if h and not re.fullmatch(r"[\d$.,%()\- ]+", h))
        for r in rows[1:] if labelled >= max(1, len(header) // 2) else rows:
            if labelled >= max(1, len(header) // 2):
                cells = [f"{h}: {v}" if h and v else v for h, v in zip(header, r) if v]
            else:
                cells = [v for v in r if v]
            if cells:
                out.append(" | ".join(cells))
    return "\n".join(out)


_MIN_LINE_CONF = 65        # mean Tesseract confidence a line needs to be kept
_MIN_WORD_CONF = 25        # words below this inside a kept line are dropped


def _real_line(words) -> bool:
    """A line of real text, not noise read off a photo: mostly confident words, at least
    one real word (3+ letters with a vowel) or a number."""
    toks = [w for w, _ in words]
    if not toks:
        return False
    conf = sum(c for _, c in words) / len(words)
    has_word = any(re.fullmatch(r"[A-Za-z]{3,}", t.strip(".,:;()'\"")) and re.search(r"[aeiouyAEIOUY]", t) for t in toks)
    has_num = any(re.search(r"\d", t) for t in toks)
    junk = sum(1 for t in toks if len(t) <= 2 and not re.search(r"[\dA-Za-z]{2}", t)) / len(toks)
    return conf >= _MIN_LINE_CONF and (has_word or has_num) and junk < 0.4


def _ocr_once(page, dpi: int, psm: int):
    """OCR one page; returns (text of real lines only, mean confidence of kept words)."""
    import pytesseract
    from PIL import Image, ImageOps
    pix = page.get_pixmap(dpi=dpi)
    img = ImageOps.grayscale(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    data = pytesseract.image_to_data(img, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    lines, cur, key_prev = [], [], None
    for i, w in enumerate(data["text"]):
        c = float(data["conf"][i])
        if not w.strip() or c < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key != key_prev and cur:
            lines.append(cur)
            cur = []
        cur.append((w, c))
        key_prev = key
    if cur:
        lines.append(cur)
    kept = [[(w, c) for w, c in ln if c >= _MIN_WORD_CONF] for ln in lines if _real_line(ln)]
    kept = [ln for ln in kept if ln]
    confs = [c for ln in kept for _, c in ln]
    return "\n".join(" ".join(w for w, _ in ln) for ln in kept), (sum(confs) / len(confs) if confs else 0.0)


_OCR_FIXES = [
    (re.compile(r"\b(Class|Classification|Visa|Status)(\s*:?\s*)H[Il|]B\b"), r"\1\2H1B"),   # "Class: HIB" → H1B
    (re.compile(r"\bH[Il|]-?B\b(?=\s+(?:visa|status|petition|classification))", re.I), "H1B"),
]


def _fix_ocr(text: str) -> str:
    for pat, rep in _OCR_FIXES:
        text = pat.sub(rep, text)
    return text


_PADDLE = None
_PADDLE_MIN_CONF = 0.6
import threading as _threading
_PADDLE_LOCK = _threading.Lock()     # the PaddleOCR predictor isn't thread-safe: concurrent calls corrupt each other


def _paddle(page) -> Optional[str]:
    """PaddleOCR (PP-OCR) at 300 dpi — far more accurate than Tesseract on photographed /
    scanned IDs, passports and notices. Lines in reading order, low-confidence lines dropped."""
    global _PADDLE
    try:
        import numpy as np
        pix = page.get_pixmap(dpi=OCR_DPI)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        with _PADDLE_LOCK:                  # one page at a time (~4 s each)
            if _PADDLE is None:
                from paddleocr import PaddleOCR
                _PADDLE = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
            res = _PADDLE.ocr(img, cls=True)
    except Exception as e:
        logger.warning(f"PaddleOCR unavailable ({e}); using Tesseract")
        return None
    items = []
    for box, (txt, conf) in (res[0] or []):
        if conf >= _PADDLE_MIN_CONF and txt.strip():
            y = sum(p[1] for p in box) / 4
            x = min(p[0] for p in box)
            items.append((y, x, txt.strip()))
    # group into lines (similar y), left to right
    items.sort()
    lines, cur, cur_y = [], [], None
    for y, x, t in items:
        if cur and abs(y - cur_y) > 18:
            lines.append(" ".join(w for _, w in sorted(cur)))
            cur = []
        cur.append((x, t))
        cur_y = y if cur_y is None or len(cur) == 1 else cur_y
    if cur:
        lines.append(" ".join(w for _, w in sorted(cur)))
    return "\n".join(lines)


def _ocr(page) -> str:
    """PaddleOCR first; Tesseract (300 dpi, retried at 400 dpi when unsure) if PaddleOCR is missing
    or reads nothing."""
    pad = _paddle(page)
    if pad and len(pad.strip()) >= 10:
        return _fix_ocr(pad)
    best = _ocr_once(page, OCR_DPI, 3)
    if best[1] < 75:
        alt = _ocr_once(page, 400, 6)
        if alt[1] > best[1]:
            best = alt
    return _fix_ocr(best[0])


_WORD = re.compile(r"[A-Za-z]{2,}")


def _quality(text: str) -> float:
    """Share of tokens that are not garbage. Garbage = undecoded glyph names (/uni004F),
    replacement / private-use characters, or long letter runs with no vowel ("BSrtkmn").
    Form codes (1a, 25b, W-2), amounts, dates and IDs all count as good."""
    toks = text.split()
    if not toks:
        return 0.0
    bad = 0
    for t in toks:
        core = t.strip(".,;:()[]{}\"'")
        if ("/uni" in t or "\ufffd" in t or any(0xE000 <= ord(c) <= 0xF8FF for c in t)
                or re.search(r"[^\x20-\x7E\u00A0-\u024F\u2010-\u2027\u20AC]", t)
                or (len(core) >= 5 and core.isalpha() and not re.search(r"[aeiouyAEIOUY]", core))):
            bad += 1
    return 1 - bad / len(toks)


def _image_share(page) -> float:
    """How much of the page is covered by images (0–1)."""
    try:
        area = abs(page.rect)
        cover = 0.0
        for info in page.get_image_info():
            cover += abs(page.rect & __import__("fitz").Rect(info["bbox"]))
        return min(1.0, cover / area) if area else 0.0
    except Exception:
        return 0.0


def pdf_pages(path: str, doc_type: Optional[str] = None) -> List[Page]:
    import fitz
    from app.services import vlm_forms
    doc = fitz.open(path)
    pages: List[Page] = []
    read_forms = vlm_forms.enabled(doc_type)
    for i, page in enumerate(doc, start=1):
        txt = _clean(page.get_text("text", sort=True))
        method = "text"
        dense = len(re.sub(r"\s", "", txt))
        needs_ocr = (dense < MIN_TEXT_LAYER                                   # scanned page
                     or _quality(txt) < 0.8                                   # garbled text layer
                     or (_image_share(page) > 0.35 and dense < 600))          # mostly an image (attached scan, I-94…)
        if needs_ocr:
            try:
                ocr = _clean(_ocr(page))
                if dense < MIN_TEXT_LAYER or _quality(txt) < 0.8:
                    # A text layer is exact when it's clean; OCR replaces it only when clearly better
                    if ocr and (dense < MIN_TEXT_LAYER or _quality(ocr) >= _quality(txt) + 0.15):
                        txt, method = ocr, "ocr"
                elif ocr:
                    # Keep the text layer, add what only the image holds
                    have = set(re.findall(r"\w{3,}", txt.lower()))
                    extra = [ln for ln in ocr.split("\n") if set(re.findall(r"\w{3,}", ln.lower())) - have]
                    if extra:
                        txt, method = txt + "\n\nTEXT IN IMAGES ON THIS PAGE:\n" + "\n".join(extra), "text+ocr"
            except Exception as e:
                logger.warning(f"OCR failed on page {i} of {path}: {e}")
        tab = _tables(page, txt) if method != "ocr" else ""
        if tab:
            txt = f"{txt}\n\nTABLE ROWS:\n{tab}"
        # Boxed forms: labels and values re-joined by a vision model, each value confirmed on the page
        if read_forms and i <= vlm_forms.MAX_PAGES and vlm_forms.worth_reading(txt, scanned=method != "text"):
            fields = vlm_forms.read_page(page, doc_type, txt, ocr=method != "text")
            if fields:
                txt = f"{fields}\n\n{txt}"
        pages.append(Page(i, txt, method if txt else "none"))
    return pages


# ── Chunking ─────────────────────────────────────────────────────────────────

def chunk_page(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Line-boundary chunks of at most `size` chars; a line longer than `size` is cut on spaces."""
    lines: List[str] = []
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        while len(ln) > size:
            cut = ln.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            lines.append(ln[:cut].strip())
            ln = ln[cut:].strip()
        lines.append(ln)
    chunks, cur, cur_len = [], [], 0
    for ln in lines:
        if cur and cur_len + len(ln) + 1 > size:
            chunks.append("\n".join(cur))
            carry, carry_len = [], 0
            for prev in reversed(cur):                 # overlap: trailing lines of the previous chunk
                if carry_len + len(prev) + 1 > overlap:
                    break
                carry.insert(0, prev)
                carry_len += len(prev) + 1
            cur, cur_len = carry, carry_len
        cur.append(ln)
        cur_len += len(ln) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


@dataclass
class Chunk:
    page: int
    index_in_page: int
    text: str              # with the "<filename> · page N" prefix


def build_chunks(pages: List[Page], filename: str) -> List[Chunk]:
    """Chunks per page, plus overlap across page breaks: the first chunk of a page starts with
    the last lines of the previous page (marked), so a sentence or table that continues over
    the break is whole in at least one chunk."""
    seen, out = set(), []
    prev_tail = ""
    for p in pages:
        body_text = p.text
        if prev_tail:
            body_text = f"(continued from page {p.number - 1})\n{prev_tail}\n(page {p.number})\n{body_text}"
        lines = [ln for ln in p.text.split("\n") if ln.strip() and not ln.startswith("[")
                 and not re.match(r"(FORM FIELDS|TABLE ROWS|TEXT IN IMAGES)\b", ln)]
        tail, n = [], 0
        for ln in reversed(lines):
            if n + len(ln) > CHUNK_OVERLAP * 2:
                break
            tail.insert(0, ln)
            n += len(ln) + 1
        prev_tail = "\n".join(tail)
        for j, body in enumerate(chunk_page(body_text)):
            key = hashlib.sha1(re.sub(r"\W+", "", body.lower()).encode()).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            out.append(Chunk(p.number, j, f"{filename} · page {p.number}\n{body}"))
    return out


def text_pages(text: str) -> List[Page]:
    """Non-PDF text. Keeps explicit '--- Page N ---' / form-feed page breaks when present."""
    text = text or ""
    parts = re.split(r"(?:^|\n)---\s*Page\s+(\d+)\s*---\s*\n", text, flags=re.I)
    if len(parts) > 1:
        return [Page(int(parts[i]), _clean(parts[i + 1]), "text") for i in range(1, len(parts) - 1, 2) if parts[i + 1].strip()]
    if "\f" in text:
        return [Page(i + 1, _clean(t), "text") for i, t in enumerate(text.split("\f")) if t.strip()]
    return [Page(1, _clean(text), "text")]


# ── Store ────────────────────────────────────────────────────────────────────

def ensure_table(db) -> None:
    from sqlalchemy import text
    db.execute(text("""CREATE TABLE IF NOT EXISTS document_pages (
        document_id UUID NOT NULL, page_number INTEGER NOT NULL, tenant_id UUID,
        text TEXT NOT NULL DEFAULT '', method TEXT NOT NULL DEFAULT 'text', char_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (document_id, page_number))"""))
    db.commit()


def ingest(document_id: str, file_path: Optional[str], filename: str, fallback_text: str = "",
           embed=None, doc_type: Optional[str] = None) -> dict:
    """Extract pages → store pages → chunk → embed → store chunks. Returns stats.
    Replaces any earlier pages / chunks of the document."""
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.models.document import DocumentChunk

    t0 = time.perf_counter()
    is_pdf = bool(file_path) and file_path.lower().endswith(".pdf")
    pages: List[Page] = []
    if is_pdf:
        try:
            pages = pdf_pages(file_path, doc_type)
        except Exception as e:
            logger.warning(f"Page extraction failed for {filename}: {e}; using extracted text")
    if not any(p.text for p in pages):
        pages = text_pages(fallback_text)
    t_pages = time.perf_counter() - t0

    chunks = build_chunks(pages, filename)
    if embed is None:
        from app.config.model_factory import EmbeddingModelFactory
        embed = EmbeddingModelFactory.get_model().encode
    t1 = time.perf_counter()
    vectors = embed([c.text for c in chunks], batch_size=32) if chunks else []
    t_embed = time.perf_counter() - t1

    db = SessionLocal()
    try:
        ensure_table(db)
        tid = db.execute(text("SELECT tenant_id FROM documents WHERE id = :id"), {"id": document_id}).scalar()
        db.execute(text("DELETE FROM document_chunks WHERE document_id = :id"), {"id": document_id})
        db.execute(text("DELETE FROM document_pages WHERE document_id = :id"), {"id": document_id})
        for p in pages:
            db.execute(text("""INSERT INTO document_pages (document_id, page_number, tenant_id, text, method, char_count)
                VALUES (:d, :n, :t, :x, :m, :c)"""), {"d": document_id, "n": p.number, "t": tid, "x": p.text,
                                                       "m": p.method, "c": len(p.text)})
        for i, (c, v) in enumerate(zip(chunks, vectors)):
            db.add(DocumentChunk(document_id=document_id, chunk_index=i, chunk_text=c.text, page_number=c.page,
                                 chunk_metadata={"page": c.page, "part": c.index_in_page,
                                                 "method": pages[[p.number for p in pages].index(c.page)].method},
                                 embedding=v.tolist(), tenant_id=tid))
        full = "\n\n".join(f"--- Page {p.number} ---\n{p.text}" for p in pages if p.text)
        db.execute(text("UPDATE documents SET raw_text = :r WHERE id = :id"), {"r": full, "id": document_id})
        db.commit()
    finally:
        db.close()
    stats = {"pages": len(pages), "ocr_pages": sum(p.method != "text" for p in pages),
             "empty_pages": sum(not p.text for p in pages), "chunks": len(chunks),
             "chars": sum(len(p.text) for p in pages),
             "seconds": {"pages": round(t_pages, 1), "embed": round(t_embed, 1), "total": round(time.perf_counter() - t0, 1)}}
    logger.info(f"📄 Ingested {filename}: {stats}")
    return stats
