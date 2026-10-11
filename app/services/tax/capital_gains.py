"""
1099-B ↔ Form 8949 ↔ Schedule D reconciliation, by 8949 box:

  A  short-term, basis reported to the IRS     D  long-term, basis reported
  B  short-term, basis NOT reported            E  long-term, basis NOT reported
  C / F  no 1099-B (reported by the taxpayer only)

Checks per box:
  - Proceeds on the return < 1099-B proceeds       → unreported sales (IRS matching / CP2000)
  - Covered basis on the return > broker basis     → overstated basis (gain understated)
  - Covered basis on the return < broker basis     → gain overstated — tax overpaid
  - 1099-B wash-sale loss disallowed not added back (8949 column g, code W)
  - Noncovered lots reported with $0 basis         → likely overpaying; basis needs records
  - Short/long-term split differs from the 1099-B while total proceeds match
  - Schedule D lines 1b/2/3/8b/9/10 ≠ Form 8949 totals

Rules-based parsing of the documents' text; for reviewer sign-off.
"""
import re
from collections import defaultdict
from typing import Dict, List, Optional

from app.services.tax.rules import rules

_MONEY = r"\(?-?\$?\s?[\d,]+\.\d{2}\)?"
_BOXES = "ABCDEF"
_SCHED_D_LINE = {"1a": "A", "1b": "A", "2": "B", "3": "C", "8a": "D", "8b": "D", "9": "E", "10": "F"}


def _m(s: str) -> float:
    neg = "(" in s or "-" in s
    v = float(re.sub(r"[^\d.]", "", s))
    return -v if neg else v


def _tol() -> float:
    return float(rules()["info_returns"]["tolerance"])


def _row(values: List[float]) -> Optional[Dict[str, float]]:
    """(d) proceeds, (e) basis, (g) adjustment, (h) gain — the adjustment column is often blank."""
    if len(values) >= 4:
        d, e, g, h = values[:4]
    elif len(values) == 3:
        d, e, x = values
        g, h = (0.0, x) if abs(d - e - x) <= 1.0 else (x, d - e + x)
    else:
        return None
    return {"proceeds": d, "basis": e, "adjustment": g, "gain": h}


# ── 1099-B ───────────────────────────────────────────────────────────────────

_HEADER = re.compile(r"^.*\b(short|long)[- ]?term\b.*$", re.I | re.M)


def _box_of(header: str) -> Optional[str]:
    m = re.search(r"\bbox\s*([A-F])\b", header, re.I)
    if m:
        return m.group(1).upper()
    h = header.lower()
    if not re.search(r"covered|basis\s*(is\s*)?(not\s*)?reported|noncovered|transactions|lots", h):
        return None
    short = "short" in h
    noncovered = bool(re.search(r"noncovered|not\s*reported|unknown term", h))
    return ("B" if noncovered else "A") if short else ("E" if noncovered else "D")


def _labeled(section: str, label: str) -> Optional[float]:
    m = re.search(label + r"[^\d\n$(-]{0,30}(" + _MONEY + ")", section, re.I)
    return _m(m.group(1)) if m else None


def parse_1099b(text: str) -> Dict[str, Dict[str, float]]:
    """Totals per 8949 box from a 1099-B (composite summary or single-sale form)."""
    heads = [(m.start(), _box_of(m.group(0))) for m in _HEADER.finditer(text)]
    heads = [(pos, box) for pos, box in heads if box]
    sections = []
    for i, (pos, box) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        sections.append((box, text[pos:end]))
    if not sections:
        box = "D" if re.search(r"[x☒✓■]\s*\]?\s*long[- ]term", text, re.I) else "A"
        if re.search(r"basis\s*(is\s*)?not\s*reported|noncovered", text, re.I):
            box = {"A": "B", "D": "E"}[box]
        sections = [(box, text)]
    out: Dict[str, Dict[str, float]] = {}
    for box, sec in sections:
        proceeds = _labeled(sec, r"(?:1d\s*)?(?:total\s*)?proceeds")
        if proceeds is None:
            continue
        basis = _labeled(sec, r"(?:1e\s*)?cost\s*(?:or\s*other\s*)?basis")
        wash = _labeled(sec, r"(?:1g\s*)?wash\s*sale\s*loss\s*disallowed") or 0.0
        t = out.setdefault(box, {"proceeds": 0.0, "basis": 0.0, "wash": 0.0, "basis_reported": basis is not None})
        t["proceeds"] += proceeds
        t["basis"] += basis or 0.0
        t["wash"] += wash
    return out


# ── Form 8949 / Schedule D ──────────────────────────────────────────────────

_CHECKED = re.compile(r"[\[(]\s*[xX☒✓■]\s*[\])]\s*\(?([A-F])\)", re.I)


def parse_8949(text: str) -> Dict[str, Dict[str, float]]:
    marks = [(m.start(), m.group(1).upper()) for m in _CHECKED.finditer(text)]
    out: Dict[str, Dict[str, float]] = {}
    for i, (pos, box) in enumerate(marks):
        sec = text[pos:marks[i + 1][0] if i + 1 < len(marks) else len(text)]
        m = re.search(r"\b[24]\s*totals?\b[^\n]*", sec, re.I)
        if not m:
            continue
        # The totals label names the columns "(d), (e), (g), and (h)" — skip to the numbers
        row = _row([_m(x) for x in re.findall(_MONEY, m.group(0))])
        if row:
            t = out.setdefault(box, {"proceeds": 0.0, "basis": 0.0, "adjustment": 0.0, "gain": 0.0})
            for k in t:
                t[k] += row[k]
    return out


def parse_schedule_d(text: str) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for m in re.finditer(r"^\s*(1a|1b|2|3|8a|8b|9|10)\b(.*)$", text, re.M):
        row = _row([_m(x) for x in re.findall(_MONEY, m.group(2))])
        if row:
            out[m.group(1)] = row
    return out


# ── Reconcile ────────────────────────────────────────────────────────────────

def reconcile(b1099: List[Dict], f8949: List[Dict], sched_d: List[Dict]) -> Dict:
    """Each input: [{filename, data}] where data is the parse_* output."""
    tol = _tol()
    broker: Dict[str, Dict] = defaultdict(lambda: {"proceeds": 0.0, "basis": 0.0, "wash": 0.0, "basis_reported": True, "docs": []})
    for d in b1099:
        for box, t in d["data"].items():
            b = broker[box]
            b["proceeds"] += t["proceeds"]; b["basis"] += t["basis"]; b["wash"] += t["wash"]
            b["basis_reported"] = b["basis_reported"] and t["basis_reported"]
            b["docs"].append(d["filename"])
    filed: Dict[str, Dict] = defaultdict(lambda: {"proceeds": 0.0, "basis": 0.0, "adjustment": 0.0, "gain": 0.0, "docs": []})
    for d in f8949:
        for box, t in d["data"].items():
            for k in ("proceeds", "basis", "adjustment", "gain"):
                filed[box][k] += t[k]
            filed[box]["docs"].append(d["filename"])
    sd = {}
    for d in sched_d:
        for line, row in d["data"].items():
            sd[line] = {**row, "doc": d["filename"]}
    # Covered sales with no adjustments go straight to Schedule D lines 1a / 8a
    for line, box in (("1a", "A"), ("8a", "D")):
        if line in sd:
            for k in ("proceeds", "basis", "adjustment", "gain"):
                filed[box][k] += sd[line][k]
            filed[box]["docs"].append(sd[line]["doc"] + f" (line {line})")
    # Only Schedule D filed (no 8949): use its 8949-box lines
    if not f8949:
        for line, box in _SCHED_D_LINE.items():
            if line in ("1a", "8a") or line not in sd:
                continue
            for k in ("proceeds", "basis", "adjustment", "gain"):
                filed[box][k] += sd[line][k]
            filed[box]["docs"].append(sd[line]["doc"] + f" (line {line})")

    findings = []

    def add(box, kind, amount, detail, docs, effect):
        findings.append({"box": box, "type": kind, "amount": round(amount, 2), "detail": detail,
                         "effect": effect, "evidence": sorted(set(docs))})

    for box in sorted(set(broker) | set(filed)):
        b, f = broker.get(box), filed.get(box)
        term = "short-term" if box in "ABC" else "long-term"
        docs = (b["docs"] if b else []) + (f["docs"] if f else [])
        if b and (not f or f["proceeds"] + tol < b["proceeds"]):
            missing = b["proceeds"] - (f["proceeds"] if f else 0.0)
            add(box, "Proceeds not reported", missing,
                f"1099-B shows ${b['proceeds']:,.2f} of {term} proceeds (box {box}); the return reports "
                f"${(f['proceeds'] if f else 0):,.2f}" + ("" if b["basis_reported"] else
                " — basis isn't reported to the IRS, so it will assume $0 basis on the gap"),
                docs, "underreported — CP2000 risk")
        if not (b and f):
            continue
        if b["basis_reported"]:
            diff = f["basis"] - b["basis"]
            if diff > tol:
                add(box, "Basis higher than the broker reported", diff,
                    f"Return basis ${f['basis']:,.2f} vs 1099-B ${b['basis']:,.2f} (covered lots) — needs support "
                    "(e.g. reinvested dividends, adjusted basis) or the gain is understated", docs, "underreported gain")
            elif -diff > tol:
                add(box, "Basis lower than the broker reported", -diff,
                    f"Return basis ${f['basis']:,.2f} vs 1099-B ${b['basis']:,.2f} — gain overstated by "
                    f"${-diff:,.2f}; amend to recover the tax", docs, "tax overpaid — refund opportunity")
        elif f["basis"] <= tol and f["proceeds"] > tol:
            add(box, "Noncovered sales reported with $0 basis", f["proceeds"],
                f"${f['proceeds']:,.2f} of proceeds taxed in full — the broker didn't report basis. Purchase "
                "confirmations or brokerage statements usually support a basis", docs, "likely overpaid — needs records")
        if b["wash"] > tol and f["adjustment"] + tol < b["wash"]:
            add(box, "Wash-sale loss not added back", b["wash"] - f["adjustment"],
                f"1099-B box 1g disallows ${b['wash']:,.2f}; Form 8949 column (g) shows ${f['adjustment']:,.2f} "
                "(code W)", docs, "loss overstated")

    # Short/long-term classification swapped: totals agree, split doesn't
    bt, ft = sum(b["proceeds"] for b in broker.values()), sum(f["proceeds"] for f in filed.values())
    st_b = sum(broker[x]["proceeds"] for x in "AB" if x in broker)
    st_f = sum(filed[x]["proceeds"] for x in "ABC" if x in filed)
    if abs(bt - ft) <= tol and abs(st_b - st_f) > tol:
        add("A–F", "Short/long-term split differs", abs(st_b - st_f),
            f"Short-term proceeds: 1099-B ${st_b:,.2f}, return ${st_f:,.2f}", [], "holding period mismatch")

    # Schedule D must carry the Form 8949 totals
    if f8949:
        per_box = defaultdict(lambda: {"proceeds": 0.0, "gain": 0.0})
        for d in f8949:
            for box, t in d["data"].items():
                per_box[box]["proceeds"] += t["proceeds"]; per_box[box]["gain"] += t["gain"]
        for line, box in _SCHED_D_LINE.items():
            if line in ("1a", "8a") or box not in per_box:
                continue
            row = sd.get(line)
            if not sched_d:
                break
            if row is None or abs(row["proceeds"] - per_box[box]["proceeds"]) > tol or abs(row["gain"] - per_box[box]["gain"]) > tol:
                add(box, f"Schedule D line {line} ≠ Form 8949 box {box}", abs((row or {"gain": 0})["gain"] - per_box[box]["gain"]),
                    f"Form 8949 box {box}: proceeds ${per_box[box]['proceeds']:,.2f}, gain ${per_box[box]['gain']:,.2f}; "
                    + (f"Schedule D line {line}: ${row['proceeds']:,.2f} / ${row['gain']:,.2f}" if row else f"line {line} blank"),
                    [d["filename"] for d in f8949 + sched_d], "transcription error")

    return {"broker": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in broker.items()},
            "filed": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in filed.items()},
            "findings": findings}


# ── Loading / answer ────────────────────────────────────────────────────────

def _year(text: str) -> Optional[int]:
    from app.services.tax.irs_forms import _year as y
    return y(text)


def load(tenant_id: str, year: Optional[int] = None) -> Dict[str, List[Dict]]:
    from app.services.tax.reconcile import _docs
    got = {"1099b": [], "8949": [], "sched_d": []}
    for d in _docs(tenant_id):
        body = (d.raw_text or "").replace("\\$", "$")
        if not body or (year and _year(body) != year):
            continue
        head = body[:1500]
        if re.search(r"\bform 8949\b", head, re.I):
            data = parse_8949(body)
            if data:
                got["8949"].append({"filename": d.filename, "data": data})
        elif re.search(r"\bschedule d\b\s*\(form 1040", head, re.I):
            data = parse_schedule_d(body)
            if data:
                got["sched_d"].append({"filename": d.filename, "data": data})
        elif re.search(r"\b1099-?b\b|proceeds from broker", body, re.I):
            data = parse_1099b(body)
            if data:
                got["1099b"].append({"filename": d.filename, "data": data})
    return got


def format_cg(r: Dict, year: Optional[int]) -> str:
    L = [f"**1099-B ↔ Form 8949 ↔ Schedule D reconciliation**" + (f" — {year}" if year else ""), ""]
    names = {"A": "Short-term, basis reported", "B": "Short-term, basis not reported", "C": "Short-term, no 1099-B",
             "D": "Long-term, basis reported", "E": "Long-term, basis not reported", "F": "Long-term, no 1099-B"}
    L += ["| Box | Category | 1099-B proceeds | Return proceeds | 1099-B basis | Return basis | Wash sale | Adj. (g) |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for box in sorted(set(r["broker"]) | set(r["filed"])):
        b, f = r["broker"].get(box), r["filed"].get(box)
        bb = (f"${b['basis']:,.2f}" if b["basis_reported"] else "not reported") if b else "—"
        L.append(f"| {box} | {names[box]} | {'$%s' % format(b['proceeds'], ',.2f') if b else '—'} | "
                 f"{'$%s' % format(f['proceeds'], ',.2f') if f else '—'} | {bb} | "
                 f"{'$%s' % format(f['basis'], ',.2f') if f else '—'} | {'$%s' % format(b['wash'], ',.2f') if b else '—'} | "
                 f"{'$%s' % format(f['adjustment'], ',.2f') if f else '—'} |")
    L.append("")
    if not r["findings"]:
        L.append("✓ The return reports every 1099-B category with matching proceeds, basis and wash-sale adjustments.")
    else:
        L += [f"**{len(r['findings'])} finding(s)**", "", "| Box | Finding | Amount | Effect | Detail |", "|---|---|---:|---|---|"]
        for x in sorted(r["findings"], key=lambda x: -x["amount"]):
            L.append(f"| {x['box']} | **{x['type']}** | ${x['amount']:,.2f} | {x['effect']} | {x['detail']} |")
    L += ["", "_Amounts are proceeds/basis/gain, not tax. Box letters follow Form 8949. For reviewer sign-off — "
              "not a tax determination._"]
    return "\n".join(L)


_CG_RE = re.compile(r"\b(1099-?b|8949|schedule d|capital gains?|cost basis|wash sales?|brokerage (sales|1099))\b", re.I)


def answer_capital_gains(question: str, tenant_id: str) -> Optional[Dict]:
    if not _CG_RE.search(question):
        return None
    ym = re.search(r"\b(20\d{2})\b", question)
    year = int(ym.group(1)) if ym else None
    got = load(tenant_id, year)
    if not got["1099b"] and not got["8949"]:
        return None
    r = reconcile(got["1099b"], got["8949"], got["sched_d"])
    srcs = sorted({d["filename"] for v in got.values() for d in v})
    return {"answer": format_cg(r, year), "sources": srcs, "rows": len(r["findings"]), "capital_gains": r}
