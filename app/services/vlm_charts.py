"""
Chart reading with the local vision model.

Reports draw their charts as images: the bar labels and values ("Food Services and Drinking
Places 3.1%") are not in the page text, so search and the answer model never see them, and
questions about a chart get guessed answers. Here a page that names a chart ("Chart 2—…",
"Figure 3") and carries a picture is sent to the vision model (VLM_MODEL, default gemma4),
which returns each series label and value.

Nothing is stored on trust: every value must appear in an OCR reading of the page image
(PaddleOCR). Values that can't be confirmed are dropped. Confirmed data is added to the page
as a "CHART DATA" block, so it is chunked, embedded and cited like any other text.

VLM_CHARTS=0 turns this off.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

MAX_CHART_PAGES = int(os.getenv("VLM_MAX_CHART_PAGES", "30"))
DPI = 150
_CHART_REF = re.compile(r"\b(chart|figure|graph|exhibit)\s+(\d{1,3}[A-Za-z]?)\b\s*[—:\-–.]?\s*([^\n]{0,120})", re.I)

PROMPT = ('This page contains {what}. For EVERY chart on the page, read each data point: the category / '
          'series label and its value exactly as printed on the chart (keep %, $, commas and minus signs). '
          'Only report values that are printed on the chart; never estimate from bar heights.\n'
          'Return JSON only: {{"charts": [{{"title": "chart title", "points": '
          '[{{"label": "category", "series": "series name or empty", "value": "printed value"}}]}}]}}')


def _vision_model() -> str:
    """The Vision model role (local only: page images can't be masked), else VLM_MODEL."""
    from app.core import llm
    from app.services.vlm_forms import VLM_MODEL as _default
    rc = llm.role_config("vision")
    return llm.local_model_for("vision", _default) if (rc or {}).get("engine", "local") == "local" else _default


def enabled() -> bool:
    return os.getenv("VLM_CHARTS", "1") != "0"


def chart_titles(page_text: str) -> List[str]:
    """Charts the page names ('Chart 2—Percent Change …'), in order, without duplicates."""
    out = []
    for kind, num, rest in _CHART_REF.findall(page_text or ""):
        t = f"{kind.title()} {num}" + (f"—{rest.strip()}" if rest.strip() and not rest.strip()[0].islower() else "")
        if t not in out:
            out.append(t)
    return out


def looks_like_chart_page(page, page_text: str) -> bool:
    """Names a chart and holds a picture or a drawn graphic whose numbers aren't in the text."""
    if not chart_titles(page_text):
        return False
    try:
        from app.services.page_ingest import _image_share
        if _image_share(page) > 0.08:
            return True
        return len(page.get_drawings()) >= 15          # vector bar/line charts
    except Exception:
        return False


def _ocr_text(page) -> str:
    try:
        from app.services.page_ingest import _paddle
        return _paddle(page) or ""
    except Exception:
        return ""


def _norm_num(v: str) -> Optional[str]:
    m = re.search(r"-?\(?\$?\d[\d,]*\.?\d*\)?%?", v or "")
    if not m:
        return None
    s = m.group(0).replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
    return s


def _value_confirmed(value: str, ocr: str, page_text: str) -> bool:
    n = _norm_num(value)
    if not n:
        return False
    hay = re.sub(r"(?<=\d),(?=\d)", "", (ocr or "") + "\n" + (page_text or "")).replace("$", "")
    hay = hay.replace("−", "-").replace("–", "-")
    body = n.lstrip("-")
    return re.search(r"(?<![\d.])" + re.escape(body) + r"(?![\d])", hay) is not None


def read_page(page, page_text: str) -> str:
    """'CHART DATA' block for the charts on one page, or ''."""
    import ollama
    from app.services.vlm_forms import VLM_MODEL, _parse_fields  # noqa: F401  (same model as forms)
    titles = chart_titles(page_text)
    what = "a chart" if len(titles) <= 1 else f"{len(titles)} charts"
    if titles:
        what += " (" + "; ".join(titles[:4]) + ")"
    png = page.get_pixmap(dpi=DPI).tobytes("png")
    t0 = time.perf_counter()
    try:
        r = ollama.chat(model=_vision_model(), format="json",
                        messages=[{"role": "user", "content": PROMPT.format(what=what), "images": [png]}],
                        options={"temperature": 0, "num_ctx": 12288, "num_predict": 3000})
        charts = json.loads(r["message"]["content"]).get("charts", [])
    except Exception as e:
        logger.warning(f"Chart reading failed: {e}")
        return ""
    ocr = _ocr_text(page)
    blocks, kept, dropped = [], 0, 0
    for i, ch in enumerate(charts):
        title = str(ch.get("title") or (titles[i] if i < len(titles) else "Chart")).strip()
        lines = []
        for p in ch.get("points", []) or []:
            label, value = str(p.get("label") or "").strip(), str(p.get("value") or "").strip()
            series = str(p.get("series") or "").strip()
            if not label or not value:
                continue
            if _value_confirmed(value, ocr, page_text):
                lines.append(f"{label}{f' ({series})' if series else ''}: {value}")
                kept += 1
            else:
                dropped += 1
        if lines:
            blocks.append(f"{title}\n" + "\n".join(dict.fromkeys(lines)))
    logger.info(f"📊 Chart data: {kept} values confirmed, {dropped} dropped ({time.perf_counter() - t0:.0f}s)")
    if not blocks:
        return ""
    return ("CHART DATA (read from the chart image; every value checked against an OCR reading of the page):\n"
            + "\n\n".join(blocks))
