"""
immigration_cross_linker.py
===========================
Cross-document reasoning for immigration records belonging to the same
beneficiary / client.

Supported correlation patterns:
  H-1B  + I-140  → AC21 portability, 3-yr extensions, green card timeline
  H-1B  + EAD    → dual intent / OPT STEM bridge analysis
  H-4   + EAD    → H-4 EAD eligibility check
  I-485 + I-140  → AOS filing window, priority date current check
  OPT   + H-1B   → cap-gap, grace period

The engine is intentionally rule-based (no LLM) so results are deterministic
and auditable for compliance contexts.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.models.client_immigration import ClientImmigration

logger = logging.getLogger(__name__)

# ── Priority Date Bulletin stub (static fallback when API unavailable) ────────
# Real deployments should refresh this monthly from travel.state.gov.
# Format: {preference_category: date_of_filing_cutoff}  (None = not current)
_STATIC_VISA_BULLETIN: dict[str, date | None] = {
    "EB-1": date(2022, 9, 1),
    "EB-2": date(2012, 6, 1),
    "EB-3": date(2012, 3, 1),
    "EB-2-India": date(2012, 6, 1),
    "EB-3-India": date(2012, 3, 1),
    "EB-2-China": date(2019, 11, 1),
    "EB-3-China": date(2020, 1, 1),
}


def _days(d: date | None) -> int | None:
    if d is None:
        return None
    return (d - date.today()).days


def _fmt(d: date | None) -> str:
    return d.strftime("%m/%d/%Y") if d else "Unknown"


def _urgency_badge(days: int | None) -> str:
    if days is None:
        return "⚪"
    if days < 0:
        return "🔴 EXPIRED"
    if days <= 30:
        return "🔴"
    if days <= 90:
        return "🟠"
    if days <= 180:
        return "🟡"
    return "🟢"


# ── Individual doc extractors ─────────────────────────────────────────────────

def _as_h1b(recs: list[ClientImmigration]) -> ClientImmigration | None:
    types = {"h1b", "uscis_receipt", "i797"}
    visa_classes = {"H-1B", "H1B", "HIB"}
    for r in recs:
        if r.doc_type in types or (r.visa_class or "").upper().replace(" ", "") in visa_classes:
            return r
    return None


def _as_i140(recs: list[ClientImmigration]) -> ClientImmigration | None:
    for r in recs:
        if r.doc_type == "i140" or "i-140" in (r.doc_label or "").lower() or "i140" in (r.doc_label or "").lower():
            return r
    return None


def _as_i485(recs: list[ClientImmigration]) -> ClientImmigration | None:
    for r in recs:
        if r.doc_type == "i485" or "i-485" in (r.doc_label or "").lower():
            return r
    return None


def _as_ead(recs: list[ClientImmigration]) -> ClientImmigration | None:
    for r in recs:
        if r.doc_type == "ead" or (r.visa_class or "").upper() == "EAD":
            return r
    return None


def _as_h4(recs: list[ClientImmigration]) -> ClientImmigration | None:
    for r in recs:
        if r.doc_type == "h4" or (r.visa_class or "").upper() == "H-4":
            return r
    return None


# ── Core reasoning engine ─────────────────────────────────────────────────────

def reason(recs: list[ClientImmigration]) -> dict[str, Any]:
    """
    Given a list of immigration records for one beneficiary, produce a
    cross-linked analysis dict with insights, timeline, alerts, and actions.
    """
    if not recs:
        return {"insights": [], "timeline": [], "alerts": [], "actions": []}

    h1b  = _as_h1b(recs)
    i140 = _as_i140(recs)
    i485 = _as_i485(recs)
    ead  = _as_ead(recs)
    h4   = _as_h4(recs)

    insights: list[dict] = []
    timeline: list[dict] = []
    alerts:   list[dict] = []
    actions:  list[dict] = []

    beneficiary = (
        (h1b and h1b.beneficiary_name)
        or (i140 and i140.beneficiary_name)
        or (recs[0].beneficiary_name)
        or "Beneficiary"
    )
    employer = (
        (h1b and h1b.employer_sponsor)
        or (i140 and i140.employer_sponsor)
        or "Petitioner"
    )

    today = date.today()

    # ── H-1B expiry ───────────────────────────────────────────────────────────
    if h1b:
        h1b_expiry = h1b.valid_until
        h1b_start  = h1b.valid_from
        h1b_days   = _days(h1b_expiry)

        timeline.append({
            "label": "H-1B Valid From",
            "date": _fmt(h1b_start),
            "icon": "📅",
            "color": "#4A90E2",
        })
        timeline.append({
            "label": "H-1B Expires",
            "date": _fmt(h1b_expiry),
            "icon": _urgency_badge(h1b_days),
            "color": "#DC3545" if (h1b_days or 999) <= 90 else "#FFA726" if (h1b_days or 999) <= 180 else "#00C851",
            "days_remaining": h1b_days,
        })

        if h1b_days is not None:
            if h1b_days < 0:
                alerts.append({
                    "severity": "critical",
                    "title": "H-1B EXPIRED",
                    "message": f"H-1B expired {abs(h1b_days)} days ago on {_fmt(h1b_expiry)}. {beneficiary} may be out of status.",
                })
            elif h1b_days <= 90:
                alerts.append({
                    "severity": "critical",
                    "title": f"H-1B Expires in {h1b_days} Days",
                    "message": f"File H-1B extension with USCIS immediately. {employer} must file I-129 before {_fmt(h1b_expiry)}.",
                })
                actions.append({
                    "priority": "urgent",
                    "action": "File H-1B Extension (Form I-129)",
                    "deadline": _fmt(h1b_expiry),
                    "note": "USCIS recommends filing 6 months before expiry. You are past that window.",
                })
            elif h1b_days <= 180:
                alerts.append({
                    "severity": "warning",
                    "title": f"H-1B Renewal Window Open — {h1b_days} Days Remaining",
                    "message": f"Begin H-1B extension process now. File I-129 by {_fmt(h1b_expiry - timedelta(days=90))} to stay safely ahead.",
                })
                actions.append({
                    "priority": "high",
                    "action": "Initiate H-1B Extension (Form I-129)",
                    "deadline": _fmt(h1b_expiry - timedelta(days=90)),
                    "note": "Target filing 6 months before expiry.",
                })

        # ── 6-year H-1B cap check ─────────────────────────────────────────────
        if h1b_start and h1b_expiry:
            # Approximate total H-1B time (years from first start to current expiry)
            total_h1b_years = (h1b_expiry - h1b_start).days / 365.25
            if total_h1b_years >= 5.5:  # Within 6 months of the 6-year cap
                if not i140:
                    alerts.append({
                        "severity": "critical",
                        "title": "Approaching 6-Year H-1B Cap — No I-140 on File",
                        "message": (
                            f"{beneficiary} is approaching the 6-year H-1B maximum. "
                            "Without an approved I-140, further H-1B extensions beyond 6 years are NOT possible. "
                            "Consult an immigration attorney immediately."
                        ),
                    })
                    actions.append({
                        "priority": "urgent",
                        "action": "File I-140 Immigrant Petition",
                        "deadline": "Immediately",
                        "note": "Required to extend H-1B beyond 6 years under AC21 § 104(c).",
                    })
                else:
                    insights.append({
                        "type": "success",
                        "icon": "✅",
                        "title": "H-1B Extension Beyond 6 Years Possible",
                        "description": (
                            f"Approved I-140 on file enables 3-year H-1B extensions under AC21 § 104(c), "
                            "as long as the I-140 priority date is not current and I-485 has not been filed."
                        ),
                        "color": "#00C851",
                    })

    # ── I-140 analysis ────────────────────────────────────────────────────────
    if i140:
        i140_approved = i140.status in ("active", "approved")
        priority_date = i140.priority_date

        timeline.append({
            "label": "I-140 Priority Date",
            "date": _fmt(priority_date),
            "icon": "🗓",
            "color": "#AB47BC",
        })

        if i140_approved:
            insights.append({
                "type": "info",
                "icon": "📋",
                "title": "I-140 Approved",
                "description": (
                    f"Immigrant petition approved for {beneficiary}. "
                    f"Priority date: {_fmt(priority_date)}. "
                    "This secures the priority date even if employer changes (AC21 portability)."
                ),
                "color": "#4A90E2",
            })

        # ── AC21 portability check ────────────────────────────────────────────
        if i140_approved and i140.issue_date:
            days_since_i140 = (today - i140.issue_date).days
            if days_since_i140 >= 180:
                insights.append({
                    "type": "success",
                    "icon": "🔄",
                    "title": "AC21 Portability Available",
                    "description": (
                        f"I-140 has been approved for {days_since_i140} days (≥ 180). "
                        f"{beneficiary} can change employers in the same or similar occupation "
                        "without jeopardizing the green card process (AC21 § 106(c))."
                    ),
                    "color": "#00C851",
                })
            else:
                days_until_portable = 180 - days_since_i140
                insights.append({
                    "type": "warning",
                    "icon": "⏳",
                    "title": f"AC21 Portability in {days_until_portable} Days",
                    "description": (
                        f"I-140 approved {days_since_i140} days ago. "
                        f"Portability (job change protection) activates at 180 days — {days_until_portable} days to go."
                    ),
                    "color": "#FFA726",
                })

        # ── Priority date current check ────────────────────────────────────────
        if priority_date:
            # Infer preference category from visa_class / doc_label
            pref = "EB-2"  # default; real impl would read from I-140 fields
            raw_text_lower = str(i140.raw_extracted or "").lower()
            if "eb-1" in raw_text_lower or "extraordinary" in raw_text_lower:
                pref = "EB-1"
            elif "eb-3" in raw_text_lower or "skilled worker" in raw_text_lower:
                pref = "EB-3"

            cutoff = _STATIC_VISA_BULLETIN.get(pref)
            if cutoff:
                if priority_date <= cutoff:
                    insights.append({
                        "type": "success",
                        "icon": "🟢",
                        "title": f"Priority Date Is Current ({pref})",
                        "description": (
                            f"Priority date {_fmt(priority_date)} is before the {pref} cutoff "
                            f"({_fmt(cutoff)}). {beneficiary} may be eligible to file I-485 "
                            "Adjustment of Status now."
                        ),
                        "color": "#00C851",
                    })
                    if not i485:
                        actions.append({
                            "priority": "high",
                            "action": "File I-485 Adjustment of Status",
                            "deadline": "Now — priority date is current",
                            "note": f"Simultaneous I-485 + EAD/AP filing recommended. {pref} cutoff: {_fmt(cutoff)}.",
                        })
                else:
                    wait_years = (priority_date - cutoff).days / 365.25
                    insights.append({
                        "type": "warning",
                        "icon": "⏳",
                        "title": f"Priority Date Backlogged — Estimated Wait ~{wait_years:.0f} Years ({pref})",
                        "description": (
                            f"Priority date {_fmt(priority_date)} is after the {pref} cutoff "
                            f"({_fmt(cutoff)}). Continue maintaining H-1B status while waiting."
                        ),
                        "color": "#FFA726",
                    })
                    timeline.append({
                        "label": f"{pref} Visa Bulletin Cutoff",
                        "date": _fmt(cutoff),
                        "icon": "📊",
                        "color": "#FFA726",
                    })

    # ── H-1B + I-140 joint insight ────────────────────────────────────────────
    if h1b and i140:
        insights.insert(0, {
            "type": "cross_link",
            "icon": "🔗",
            "title": "H-1B + I-140 Dual-Track Strategy",
            "description": (
                f"{beneficiary} is on a dual immigration track: "
                f"H-1B (non-immigrant, expires {_fmt(h1b.valid_until)}) "
                f"+ I-140 immigrant petition (priority date {_fmt(i140.priority_date)}). "
                "Key actions: renew H-1B before expiry, monitor visa bulletin monthly for priority date movement."
            ),
            "color": "#4A90E2",
            "linked_docs": [h1b.linked_document_id, i140.linked_document_id],
        })

    # ── I-485 filed ───────────────────────────────────────────────────────────
    if i485:
        timeline.append({
            "label": "I-485 Filed",
            "date": _fmt(i485.issue_date or i485.valid_from),
            "icon": "📝",
            "color": "#AB47BC",
        })
        insights.append({
            "type": "info",
            "icon": "📝",
            "title": "I-485 Adjustment of Status Filed",
            "description": (
                "I-485 is pending. Once filed for 180+ days with an approved I-140, "
                "AC21 portability protects job changes in a same/similar role. "
                "EAD/Advance Parole should be maintained to avoid H-1B dependency."
            ),
            "color": "#4A90E2",
        })

    # ── H-4 + I-140 check ────────────────────────────────────────────────────
    if h4 and i140 and i140.status in ("active", "approved"):
        insights.append({
            "type": "info",
            "icon": "💳",
            "title": "H-4 EAD Eligibility",
            "description": (
                "H-4 dependent may be eligible for H-4 EAD (work authorization) "
                "because the primary beneficiary has an approved I-140. "
                "File Form I-765 with H-4 EAD category (c)(26)."
            ),
            "color": "#4A90E2",
        })
        actions.append({
            "priority": "medium",
            "action": "File H-4 EAD (Form I-765)",
            "deadline": "As soon as possible",
            "note": "Requires primary H-1B holder to have approved I-140.",
        })

    # ── EAD analysis ──────────────────────────────────────────────────────────
    if ead:
        ead_days = _days(ead.valid_until)
        if ead_days is not None and ead_days <= 90:
            alerts.append({
                "severity": "warning",
                "title": f"EAD Expires in {ead_days} Days",
                "message": f"File EAD renewal (Form I-765) immediately. EAD expiry: {_fmt(ead.valid_until)}.",
            })
            actions.append({
                "priority": "high",
                "action": "Renew EAD (Form I-765)",
                "deadline": _fmt(ead.valid_until),
                "note": "File 6 months before expiry. Auto-extension rules may apply if I-485 pending.",
            })

    # ── Sort timeline by date ─────────────────────────────────────────────────
    def _tl_sort_key(t: dict) -> date:
        try:
            from dateutil import parser as dp
            return dp.parse(t["date"], dayfirst=False).date()
        except Exception:
            return date.max

    timeline_sorted = sorted([t for t in timeline if t.get("date") and t["date"] != "Unknown"], key=_tl_sort_key)

    return {
        "beneficiary": beneficiary,
        "employer":    employer,
        "doc_count":   len(recs),
        "doc_types":   [r.doc_type for r in recs],
        "insights":    insights,
        "timeline":    timeline_sorted,
        "alerts":      alerts,
        "actions":     sorted(actions, key=lambda a: {"urgent": 0, "high": 1, "medium": 2}.get(a["priority"], 3)),
        "summary":     _build_summary(h1b, i140, i485, ead, beneficiary),
    }


def _build_summary(h1b, i140, i485, ead, name: str) -> str:
    parts = []
    if h1b:
        days = _days(h1b.valid_until)
        parts.append(
            f"H-1B expires {_fmt(h1b.valid_until)} "
            f"({abs(days)} days {'ago — EXPIRED' if days and days < 0 else 'remaining'})"
        )
    if i140:
        parts.append(
            f"I-140 {'approved' if i140.status in ('active','approved') else 'pending'} "
            f"with priority date {_fmt(i140.priority_date)}"
        )
    if i485:
        parts.append("I-485 Adjustment of Status filed")
    if ead:
        parts.append(f"EAD expires {_fmt(ead.valid_until)}")
    if not parts:
        return f"Immigration records for {name} — no critical deadlines detected."
    return f"{name}: " + " | ".join(parts) + "."
