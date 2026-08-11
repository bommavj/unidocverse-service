# app/api/workflow_router.py
"""
Interactive workflow endpoints:
  POST /api/workflow/compare          — diff two documents field-by-field
  POST /api/workflow/merge            — produce merged output from approved comparison
  POST /api/workflow/three-way-match  — invoice + PO + receipt line-item matching
  POST /api/workflow/reconcile        — bank statement vs ledger reconciliation
  POST /api/workflow/hitl/decision    — record HITL approve/reject for a run step
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.document import Document

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workflow", tags=["workflow"])


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_doc(db: Session, doc_id: str) -> Document:
    doc = db.query(Document).filter(Document.id == str(doc_id)).first()
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return doc


def _fields(doc: Document) -> Dict[str, Any]:
    meta = doc.doc_metadata or {}
    ad   = meta.get("analyzed_data") or {}
    fields = ad.get("fields") or {}
    if fields:
        return fields

    # Fallback 1: structured metadata extracted at ingest time via regex scan.
    # Works for ALL doc types — payroll, insurance, invoices, medical, etc.
    structured: Dict[str, Any] = {}
    key_amounts = meta.get("key_amounts") or {}
    if key_amounts:
        structured.update({k: str(v) for k, v in key_amounts.items()})
    for dim in ("identifiers", "entities"):
        dim_data = meta.get(dim) or {}
        if dim_data:
            structured.update({k: str(v) for k, v in dim_data.items()})
    # Period fields
    for pf in ("pay_period", "period_start", "period_end", "pay_date", "due_date"):
        if pf in meta:
            structured[pf] = meta[pf]
    if structured:
        return structured

    # Fallback 2: parse "Field | Value" table from raw_text (spreadsheet formats)
    raw = (doc.raw_text or "").strip()
    if raw:
        parsed: Dict[str, Any] = {}
        skip_prefixes = ("field", "header", "sheet:", "excel file:", "table", "sheets:")
        for line in raw.splitlines():
            if "|" in line:
                parts = [p.strip() for p in line.split("|", 1)]
                if len(parts) == 2 and parts[0] and parts[1]:
                    raw_key = parts[0].lower()
                    if any(raw_key.startswith(p) for p in skip_prefixes):
                        continue
                    key = raw_key.replace(" ", "_")
                    parsed[key] = parts[1]
        if parsed:
            return parsed

    return fields


def _analyzed(doc: Document) -> Dict[str, Any]:
    meta = doc.doc_metadata or {}
    return meta.get("analyzed_data") or {}


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


# ── request / response models ─────────────────────────────────────────────────

class CompareRequest(BaseModel):
    doc_a_id: str
    doc_b_id: str
    label_a: Optional[str] = None
    label_b: Optional[str] = None


class MergeRequest(BaseModel):
    doc_a_id: str
    doc_b_id: str
    strategy: str = "prefer_a"          # prefer_a | prefer_b | manual
    overrides: Optional[Dict[str, str]] = None   # field → chosen value


class ThreeWayMatchRequest(BaseModel):
    invoice_id: str
    po_id: Optional[str] = None
    receipt_id: Optional[str] = None


class ReconcileRequest(BaseModel):
    statement_id: str
    ledger_id: str


class HitlDecisionRequest(BaseModel):
    run_id: str
    step_index: int
    decision: str           # "approve" | "reject" | "edit"
    feedback: Optional[str] = None
    edits: Optional[Dict[str, Any]] = None


# ── in-memory HITL log (per process; replace with DB for prod) ────────────────
_hitl_log: Dict[str, List[Dict]] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# COMPARE
# ═══════════════════════════════════════════════════════════════════════════════

def _locale_info(doc: Document) -> Optional[Dict[str, Any]]:
    """Return the stored locale_info dict from doc_metadata, or None."""
    meta = doc.doc_metadata or {}
    return meta.get("locale_info")


_DATE_FIELD_SUFFIXES = ("_date", "_at", "_on", "_expiry", "_due", "_from", "_to",
                        "_start", "_end", "_issued", "_signed", "_created")
_AMOUNT_FIELD_SUFFIXES = ("_amount", "_total", "_price", "_cost", "_fee",
                          "_premium", "_value", "_balance", "_proceeds",
                          "_pay", "_wages", "_earnings", "_compensation",
                          "_deduction", "_tax", "_insurance", "_benefit")

def _is_date_field(key: str) -> bool:
    return any(key.lower().endswith(s) for s in _DATE_FIELD_SUFFIXES) or "date" in key.lower()

def _is_amount_field(key: str) -> bool:
    return any(key.lower().endswith(s) for s in _AMOUNT_FIELD_SUFFIXES) or "amount" in key.lower()

def _normalize_date_for_compare(val: str, locale_info: Optional[Dict]) -> str:
    """Attempt to convert a locale-specific date to ISO YYYY-MM-DD for comparison."""
    if not val:
        return val
    import re
    date_order = (locale_info or {}).get("date_order", "MDY")
    # Already ISO: YYYY-MM-DD or YYYY-MM — pass through unchanged
    if re.match(r'^20\d{2}[-/]\d{2}([-/]\d{2})?$', val.strip()):
        return val.strip()
    # DMY / MDY: 1-2 digit day/month with 2-4 digit year
    m = re.match(r'^(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{2,4})$', val.strip())
    if not m:
        return val
    a, b, c = m.group(1), m.group(2), m.group(3)
    if len(c) == 2:
        c = "20" + c
    try:
        if date_order == "DMY":
            return f"{c}-{a.zfill(2)}-{b.zfill(2)}"
        elif date_order == "YMD":
            # This branch is unreachable via the above regex (4-digit year won't match
            # \d{1,2} in group 1), but kept as safety for any future callers.
            return f"{a}-{b.zfill(2)}-{c.zfill(2)}"
        else:  # MDY
            return f"{c}-{a.zfill(2)}-{b.zfill(2)}"
    except Exception:
        return val

def _normalize_amount_for_compare(val: str) -> Optional[float]:
    """Strip currency symbols and parse as float. Returns None on failure.

    Handles: "$3,436.00", "3.436,00" (European), "3 436 00" (Docling space-separated).
    """
    if not val:
        return None
    import re
    s = str(val).strip()
    # Detect Docling space-separated format: digits separated by single spaces
    # e.g. "3 436 00" or "75 592 00" — the last group of 2 digits is cents
    space_m = re.match(r'^-?(\d{1,3}(?:\s\d{3})*)\s(\d{2})$', s)
    if space_m:
        whole = space_m.group(1).replace(' ', '')
        cents = space_m.group(2)
        sign  = '-' if s.startswith('-') else ''
        try:
            return float(f"{sign}{whole}.{cents}")
        except ValueError:
            pass
    # Standard: strip all non-numeric except comma, dot, minus
    s = re.sub(r'[^\d.,\-]', '', s).strip()
    if not s:
        return None
    last_comma = s.rfind(',')
    last_dot   = s.rfind('.')
    try:
        if last_comma > last_dot:
            # European format: 1.234,56
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
        return float(s)
    except ValueError:
        return None


@router.post("/compare")
def compare_documents(req: CompareRequest, db: Session = Depends(get_db)):
    """
    Field-by-field diff of two documents.
    Returns rows with status: match | mismatch | only_a | only_b

    When the two documents have different locale_info, dates and amounts are
    normalised to a common form before comparing, and each row carries a
    `locale_mismatch` flag so the UI can show a warning.
    """
    doc_a = _get_doc(db, req.doc_a_id)
    doc_b = _get_doc(db, req.doc_b_id)

    fa = _fields(doc_a)
    fb = _fields(doc_b)

    label_a = req.label_a or doc_a.filename
    label_b = req.label_b or doc_b.filename

    locale_a = _locale_info(doc_a)
    locale_b = _locale_info(doc_b)

    country_a = (locale_a or {}).get("country", "")
    country_b = (locale_b or {}).get("country", "")
    locale_mismatch = bool(country_a and country_b and country_a != country_b)

    # Merge alias groups so gross_pay == gross_wages, total_amount == invoice_total etc.
    _COMPARE_ALIASES: List[List[str]] = [
        ["total_amount", "grand_total", "invoice_total", "total_due", "amount_due",
         "subtotal", "total_cost", "total_charges"],
        ["gross_pay", "gross_wages", "gross_earnings", "gross_compensation"],
        ["net_pay", "net_check", "net_deposit", "take_home_pay"],
        ["vendor_name", "supplier_name", "payee_name", "employer_name"],
        ["invoice_number", "inv_number", "invoice_no"],
        ["po_number", "purchase_order_number", "order_number"],
        ["period_start", "pay_period_start", "billing_from"],
        ["period_end",   "pay_period_end",   "billing_to"],
        ["account_number", "account_no", "acct_number"],
    ]

    def _canon_key(k: str) -> str:
        for grp in _COMPARE_ALIASES:
            if k in grp:
                return grp[0]
        return k

    # Re-map both field dicts to canonical keys
    fa = {_canon_key(k): v for k, v in fa.items()}
    fb = {_canon_key(k): v for k, v in fb.items()}

    all_keys = sorted(set(list(fa.keys()) + list(fb.keys())))

    rows = []
    match_count = mismatch_count = only_a = only_b = 0

    for key in all_keys:
        va = _fmt(fa.get(key))
        vb = _fmt(fb.get(key))
        in_a = key in fa
        in_b = key in fb

        compare_a = va
        compare_b = vb
        field_locale_note: Optional[str] = None

        if in_a and in_b:
            # Always normalise amounts (not just on locale mismatch)
            na = _normalize_amount_for_compare(va)
            nb = _normalize_amount_for_compare(vb)
            if na is not None and nb is not None:
                compare_a = str(round(na, 2))
                compare_b = str(round(nb, 2))
            elif locale_mismatch and _is_date_field(key):
                compare_a = _normalize_date_for_compare(va, locale_a)
                compare_b = _normalize_date_for_compare(vb, locale_b)
                field_locale_note = f"Dates normalised: {country_a} vs {country_b}"

        if in_a and in_b:
            if compare_a.lower() == compare_b.lower():
                status = "match"
                match_count += 1
            else:
                status = "mismatch"
                mismatch_count += 1
        elif in_a:
            status = "only_a"
            only_a += 1
        else:
            status = "only_b"
            only_b += 1

        row: Dict[str, Any] = {
            "field":    key.replace("_", " ").title(),
            "field_key": key,
            "value_a":  va,
            "value_b":  vb,
            "status":   status,
        }
        if field_locale_note:
            row["locale_note"] = field_locale_note
        rows.append(row)

    # Also surface key summary metrics
    ad_a = _analyzed(doc_a)
    ad_b = _analyzed(doc_b)
    summary_metrics = []
    for k in ["total_amount", "total_proceeds", "total_gain_loss", "net_amount",
              "closing_balance", "total_premium", "grand_total"]:
        va = ad_a.get(k) or fa.get(k)
        vb = ad_b.get(k) or fb.get(k)
        if va is not None or vb is not None:
            na = _normalize_amount_for_compare(str(va)) if va is not None else None
            nb = _normalize_amount_for_compare(str(vb)) if vb is not None else None
            summary_metrics.append({
                "label": k.replace("_", " ").title(),
                "value_a": f"${na:,.2f}" if na is not None else "—",
                "value_b": f"${nb:,.2f}" if nb is not None else "—",
                "delta": f"${nb - na:+,.2f}" if na is not None and nb is not None else None,
            })

    return {
        "label_a":   label_a,
        "label_b":   label_b,
        "doc_type_a": doc_a.doc_type,
        "doc_type_b": doc_b.doc_type,
        "locale_a":  locale_a,
        "locale_b":  locale_b,
        "locale_mismatch": locale_mismatch,
        "rows":      rows,
        "summary_metrics": summary_metrics,
        "stats": {
            "total":    len(rows),
            "match":    match_count,
            "mismatch": mismatch_count,
            "only_a":   only_a,
            "only_b":   only_b,
            "match_pct": round(match_count / max(len(rows), 1) * 100),
        },
        "run_id": str(uuid.uuid4()),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MERGE
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/merge")
def merge_documents(req: MergeRequest, db: Session = Depends(get_db)):
    """
    Produce a merged field set from two documents.
    strategy: prefer_a | prefer_b | manual (use overrides dict)
    Returns merged fields + CSV rows ready for download.
    """
    doc_a = _get_doc(db, req.doc_a_id)
    doc_b = _get_doc(db, req.doc_b_id)

    fa = _fields(doc_a)
    fb = _fields(doc_b)

    all_keys = sorted(set(list(fa.keys()) + list(fb.keys())))
    merged: Dict[str, str] = {}
    decisions: List[Dict] = []

    overrides = req.overrides or {}

    for key in all_keys:
        va = _fmt(fa.get(key))
        vb = _fmt(fb.get(key))

        if key in overrides:
            chosen = overrides[key]
            source = "manual"
        elif va and not vb:
            chosen = va
            source = "doc_a"
        elif vb and not va:
            chosen = vb
            source = "doc_b"
        elif req.strategy == "prefer_b":
            chosen = vb or va
            source = "doc_b"
        else:
            chosen = va or vb
            source = "doc_a"

        merged[key] = chosen
        decisions.append({
            "field":   key.replace("_", " ").title(),
            "chosen":  chosen,
            "source":  source,
            "value_a": va,
            "value_b": vb,
        })

    # Build CSV rows for download
    csv_rows = [["Field", "Value", "Source"]]
    for d in decisions:
        csv_rows.append([d["field"], d["chosen"], d["source"]])

    return {
        "merged_fields": merged,
        "decisions":     decisions,
        "csv_rows":      csv_rows,
        "doc_a_name":    doc_a.filename,
        "doc_b_name":    doc_b.filename,
        "strategy":      req.strategy,
        "total_fields":  len(merged),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# THREE-WAY MATCH
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/three-way-match")
def three_way_match(req: ThreeWayMatchRequest, db: Session = Depends(get_db)):
    """
    Match invoice vs PO vs receipt (or any 2-3 documents).
    Dynamically compares all fields present in the documents — not a
    hardcoded list — so it works for payroll, invoices, insurance, etc.
    """
    invoice = _get_doc(db, req.invoice_id)
    inv_fields = _fields(invoice)

    docs: Dict[str, tuple] = {"Doc A": (invoice, inv_fields)}
    if req.po_id:
        po = _get_doc(db, req.po_id)
        docs["Doc B"] = (po, _fields(po))
    if req.receipt_id:
        receipt = _get_doc(db, req.receipt_id)
        docs["Doc C"] = (receipt, _fields(receipt))

    # ── Field alias groups: treat these as the same logical field ─────────────
    # Any key that is a member of a group gets normalised to the canonical name.
    _ALIASES: List[List[str]] = [
        ["total_amount", "grand_total", "invoice_total", "total_due",
         "amount_due", "subtotal", "total_cost", "total_charges"],
        ["vendor_name", "supplier_name", "payee_name", "employer_name", "client_name"],
        ["invoice_number", "inv_number", "invoice_no", "bill_number"],
        ["po_number", "purchase_order_number", "order_number"],
        ["due_date", "payment_due", "invoice_due_date"],
        ["gross_pay", "gross_wages", "gross_earnings", "gross_compensation"],
        ["net_pay", "net_check", "net_deposit", "take_home_pay"],
        ["period_start", "pay_period_start", "billing_from", "coverage_start"],
        ["period_end",   "pay_period_end",   "billing_to",   "coverage_end"],
        ["account_number", "account_no", "acct_number"],
        ["employee_name", "employee"],
        ["employer_name", "company_name", "employer"],
    ]

    def _canonical(key: str) -> str:
        for group in _ALIASES:
            if key in group:
                return group[0]
        return key

    def _normalise_val(val: Any) -> str:
        """Strip currency symbols, commas, spaces; lower-case for comparison."""
        s = str(val).strip()
        try:
            # Try numeric normalisation first
            cleaned = s.replace("$", "").replace("£", "").replace("€", "").replace(",", "").strip()
            return str(round(float(cleaned), 2))
        except ValueError:
            return s.lower()

    # ── Build a canonical field → {role: raw_value} map ──────────────────────
    canonical_map: Dict[str, Dict[str, Any]] = {}
    for role, (_, fields) in docs.items():
        for raw_key, val in fields.items():
            if val is None or str(val).strip() == "":
                continue
            canon = _canonical(raw_key)
            canonical_map.setdefault(canon, {})[role] = val

    # ── Compare fields present in ≥ 2 documents ───────────────────────────────
    matches, discrepancies = [], []

    for canon_key, role_vals in sorted(canonical_map.items()):
        if len(role_vals) < 2:
            # Only in one doc — report as info, not a mismatch
            role, val = next(iter(role_vals.items()))
            discrepancies.append({
                "field":  canon_key.replace("_", " ").title(),
                "status": "only_in_one",
                "values": {role: _fmt(val)},
                "note":   f"Only present in {role}",
            })
            continue

        norm_vals = {role: _normalise_val(val) for role, val in role_vals.items()}
        unique    = set(norm_vals.values())
        status    = "match" if len(unique) <= 1 else "mismatch"

        row = {
            "field":  canon_key.replace("_", " ").title(),
            "status": status,
            "values": {role: _fmt(val) for role, val in role_vals.items()},
        }
        if status == "match":
            matches.append(row)
        else:
            discrepancies.append(row)

    hitl_required = any(r["status"] == "mismatch" for r in discrepancies)

    return {
        "doc_names":     {role: doc.filename for role, (doc, _) in docs.items()},
        "matches":       matches,
        "discrepancies": discrepancies,
        "hitl_required": hitl_required,
        "summary": {
            "matched":       len(matches),
            "discrepancies": sum(1 for r in discrepancies if r["status"] == "mismatch"),
            "only_in_one":   sum(1 for r in discrepancies if r["status"] == "only_in_one"),
            "docs_checked":  len(docs),
            "status":        "PASS" if not hitl_required else "REVIEW REQUIRED",
        },
        "run_id": str(uuid.uuid4()),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BANK RECONCILIATION
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/reconcile")
def reconcile(req: ReconcileRequest, db: Session = Depends(get_db)):
    """
    Compare bank statement vs internal ledger/invoice doc.
    Surface unmatched entries that need human review.
    """
    statement = _get_doc(db, req.statement_id)
    ledger    = _get_doc(db, req.ledger_id)

    s_ad = _analyzed(statement)
    l_ad = _analyzed(ledger)

    s_fields = _fields(statement)
    l_fields = _fields(ledger)

    # Balance comparison
    s_balance = s_ad.get("closing_balance") or s_fields.get("closing_balance")
    l_balance = l_ad.get("total_amount") or l_fields.get("grand_total") or l_fields.get("total_amount")

    balance_diff = None
    if s_balance is not None and l_balance is not None:
        try:
            balance_diff = float(s_balance) - float(l_balance)
        except (TypeError, ValueError):
            pass

    # Transactions comparison
    s_txns: List[Dict] = s_ad.get("transactions") or []
    l_txns: List[Dict] = l_ad.get("line_items") or l_ad.get("transactions") or []

    unmatched_statement = []
    unmatched_ledger    = []

    def _txn_key(raw_val: Any) -> str:
        """Normalise a transaction amount to a rounded-float string for matching."""
        n = _normalize_amount_for_compare(str(raw_val)) if raw_val is not None else None
        return str(round(n, 2)) if n is not None else _fmt(raw_val)

    s_amounts = {_txn_key(t.get("amount") or t.get("debit") or t.get("credit")): t for t in s_txns}
    l_amounts = {_txn_key(t.get("amount") or t.get("total") or t.get("unit_price")): t for t in l_txns}

    for amt, txn in s_amounts.items():
        if amt and amt not in l_amounts:
            unmatched_statement.append({
                "amount":      amt,
                "description": txn.get("description") or txn.get("payee") or "—",
                "date":        txn.get("date") or "—",
            })

    for amt, txn in l_amounts.items():
        if amt and amt not in s_amounts:
            unmatched_ledger.append({
                "amount":      amt,
                "description": txn.get("description") or txn.get("item") or "—",
                "date":        txn.get("date") or "—",
            })

    hitl_required = bool(unmatched_statement or unmatched_ledger or (balance_diff and abs(balance_diff) > 0.01))

    return {
        "statement":        statement.filename,
        "ledger":           ledger.filename,
        "balance_statement": s_balance,
        "balance_ledger":    l_balance,
        "balance_diff":      balance_diff,
        "unmatched_statement": unmatched_statement[:20],
        "unmatched_ledger":    unmatched_ledger[:20],
        "hitl_required":    hitl_required,
        "summary": {
            "status":               "BALANCED" if not hitl_required else "DISCREPANCIES FOUND",
            "unmatched_statement":  len(unmatched_statement),
            "unmatched_ledger":     len(unmatched_ledger),
            "balance_diff":         f"${balance_diff:+,.2f}" if balance_diff is not None else "N/A",
        },
        "run_id": str(uuid.uuid4()),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HITL DECISION LOG
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/hitl/decision")
def record_hitl_decision(req: HitlDecisionRequest):
    """Record a human approve/reject/edit decision for a workflow run step."""
    if req.run_id not in _hitl_log:
        _hitl_log[req.run_id] = []

    _hitl_log[req.run_id].append({
        "step_index": req.step_index,
        "decision":   req.decision,
        "feedback":   req.feedback,
        "edits":      req.edits,
        "timestamp":  datetime.utcnow().isoformat(),
    })

    logger.info(f"✅ HITL [{req.run_id}] step {req.step_index}: {req.decision}")
    return {"status": "recorded", "run_id": req.run_id, "decision": req.decision}


@router.get("/hitl/log/{run_id}")
def get_hitl_log(run_id: str):
    return {"run_id": run_id, "decisions": _hitl_log.get(run_id, [])}
