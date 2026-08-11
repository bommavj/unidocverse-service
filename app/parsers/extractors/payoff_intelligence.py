from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PayoffScore:
    score: int
    grade: str
    label: str
    timing_score: int
    fee_score: int
    wiring_score: int
    completeness_score: int


@dataclass
class PayoffIntelligenceReport:
    key_insights: List[str]
    cost_analysis: Dict[str, Any]
    timing_analysis: Dict[str, Any]
    wire_risk: Dict[str, Any]
    risk_flags: List[str]
    action_items: Dict[str, List[Dict[str, Any]]]
    score: PayoffScore

    def to_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


class PayoffIntelligence:
    """
    Reasoning layer on top of PayoffStatementAnalyzer output.

    Answers:
      1. What is this payoff made of? (principal, interest, fees)
      2. How urgent is it? (days left, expired, weekend risk)
      3. How clean is the wiring story? (missing/ambiguous instructions)
      4. What should the user do next? (ranked actions)
      5. How strong is this payoff overall? (score/grade/label)
    """

    def analyze(self, payoff_result: Dict[str, Any]) -> PayoffIntelligenceReport:
        logger.info("🧠 PayoffIntelligence: analyzing payoff result")

        metrics = payoff_result
        payoff_raw = payoff_result.get("payoff_raw", {}) or {}
        risk_flags = payoff_result.get("payoff_risk_flags", {}) or {}

        cost = self._cost_analysis(metrics)
        timing = self._timing_analysis(metrics)
        wire = self._wire_risk(payoff_raw, risk_flags)
        score = self._score(cost, timing, wire, risk_flags)
        key_insights = self._key_insights(metrics, cost, timing, wire, score)
        actions = self._action_items(metrics, cost, timing, wire, risk_flags, score)

        risk_list = self._risk_flag_messages(risk_flags, timing)

        return PayoffIntelligenceReport(
            key_insights=key_insights,
            cost_analysis=cost,
            timing_analysis=timing,
            wire_risk=wire,
            risk_flags=risk_list,
            action_items=actions,
            score=score,
        )

    # ---------------- COST ----------------

    def _cost_analysis(self, m: Dict[str, Any]) -> Dict[str, Any]:
        payoff = float(m.get("payoff_amount") or 0.0)
        principal = float(m.get("principal_balance") or 0.0)
        interest = float(m.get("interest_due") or 0.0)
        fees = float(m.get("total_fees") or 0.0)
        escrow = float(m.get("escrow_balance") or 0.0)
        suspense = float(m.get("suspense_balance") or 0.0)

        non_principal = payoff - principal
        fee_ratio = (fees / payoff) * 100 if payoff > 0 else 0.0
        interest_ratio = (interest / payoff) * 100 if payoff > 0 else 0.0

        return {
            "payoff_amount": round(payoff, 2),
            "principal_balance": round(principal, 2),
            "interest_due": round(interest, 2),
            "total_fees": round(fees, 2),
            "escrow_balance": round(escrow, 2),
            "suspense_balance": round(suspense, 2),
            "non_principal_amount": round(non_principal, 2),
            "fee_ratio_pct": round(fee_ratio, 2),
            "interest_ratio_pct": round(interest_ratio, 2),
        }

    # ---------------- TIMING ----------------

    def _timing_analysis(self, m: Dict[str, Any]) -> Dict[str, Any]:
        days = m.get("days_until_expiration")
        expired = bool(m.get("is_expired"))

        urgency = "unknown"
        if days is None:
            urgency = "unknown"
        elif days < 0:
            urgency = "expired"
        elif days <= 1:
            urgency = "critical"
        elif days <= 3:
            urgency = "high"
        elif days <= 7:
            urgency = "medium"
        else:
            urgency = "low"

        return {
            "days_until_expiration": days,
            "is_expired": expired,
            "urgency_level": urgency,
        }

    # ---------------- WIRE / OPS RISK ----------------

    def _wire_risk(self, payoff_raw: Dict[str, Any], flags: Dict[str, Any]) -> Dict[str, Any]:
        wiring_present = bool(payoff_raw.get("wiring_instructions_present"))
        mailing_present = bool(payoff_raw.get("mailing_instructions_present"))
        multiple_options = bool(payoff_raw.get("multiple_payoff_options"))

        missing_wiring = flags.get("missing_wiring", not wiring_present)
        ambiguous = flags.get("ambiguous_instructions", multiple_options)

        risk_level = "low"
        if missing_wiring and ambiguous:
            risk_level = "critical"
        elif missing_wiring or ambiguous:
            risk_level = "high"

        return {
            "wiring_instructions_present": wiring_present,
            "mailing_instructions_present": mailing_present,
            "multiple_payoff_options": multiple_options,
            "missing_wiring": missing_wiring,
            "ambiguous_instructions": ambiguous,
            "wire_risk_level": risk_level,
        }

    # ---------------- SCORE ----------------

    def _score(
        self,
        cost: Dict[str, Any],
        timing: Dict[str, Any],
        wire: Dict[str, Any],
        flags: Dict[str, Any],
    ) -> PayoffScore:
        # Start from 100 and subtract penalties
        score = 100

        # Timing
        timing_score = 100
        urgency = timing.get("urgency_level")
        if urgency == "expired":
            timing_score = 20
        elif urgency == "critical":
            timing_score = 40
        elif urgency == "high":
            timing_score = 60
        elif urgency == "medium":
            timing_score = 80

        # Fees
        fee_ratio = cost.get("fee_ratio_pct", 0.0)
        fee_score = 100
        if fee_ratio > 10:
            fee_score = 40
        elif fee_ratio > 5:
            fee_score = 70
        elif fee_ratio > 2:
            fee_score = 85

        # Wiring
        wiring_score = 100
        if wire.get("wire_risk_level") == "critical":
            wiring_score = 30
        elif wire.get("wire_risk_level") == "high":
            wiring_score = 60

        # Completeness
        completeness_score = 100
        if flags.get("expired_quote"):
            completeness_score -= 20
        if flags.get("missing_wiring"):
            completeness_score -= 20
        if flags.get("ambiguous_instructions"):
            completeness_score -= 20

        completeness_score = max(0, completeness_score)

        # Aggregate
        score = int(
            0.3 * timing_score +
            0.3 * fee_score +
            0.2 * wiring_score +
            0.2 * completeness_score
        )

        if score >= 90:
            grade, label = "A", "Excellent — clear payoff with low operational risk."
        elif score >= 75:
            grade, label = "B", "Good — manageable risk with some items to confirm."
        elif score >= 60:
            grade, label = "C", "Fair — several items require attention before funding."
        elif score >= 40:
            grade, label = "D", "High risk — multiple issues; proceed carefully."
        else:
            grade, label = "F", "Critical — payoff appears risky or incomplete."

        return PayoffScore(
            score=score,
            grade=grade,
            label=label,
            timing_score=timing_score,
            fee_score=fee_score,
            wiring_score=wiring_score,
            completeness_score=completeness_score,
        )

    # ---------------- INSIGHTS / ACTIONS ----------------

    def _key_insights(
        self,
        m: Dict[str, Any],
        cost: Dict[str, Any],
        timing: Dict[str, Any],
        wire: Dict[str, Any],
        score: PayoffScore,
    ) -> List[str]:
        insights: List[str] = []

        insights.append(
            f"Payoff amount ${cost['payoff_amount']:,.2f} with principal ${cost['principal_balance']:,.2f} "
            f"and fees ${cost['total_fees']:,.2f} ({cost['fee_ratio_pct']:.2f}% of payoff)."
        )

        if timing.get("days_until_expiration") is not None:
            insights.append(
                f"Payoff quote expires in {timing['days_until_expiration']} day(s) "
                f"(urgency: {timing['urgency_level']})."
            )

        if not wire["wiring_instructions_present"]:
            insights.append("No wiring instructions detected — confirm routing/account details before sending funds.")

        insights.append(
            f"Payoff quality score: {score.score}/100 — {score.label}"
        )

        return insights

    def _action_items(
        self,
        m: Dict[str, Any],
        cost: Dict[str, Any],
        timing: Dict[str, Any],
        wire: Dict[str, Any],
        flags: Dict[str, Any],
        score: PayoffScore,
    ) -> Dict[str, List[Dict[str, Any]]]:
        immediate: List[Dict[str, Any]] = []
        short_term: List[Dict[str, Any]] = []
        questions: List[str] = []

        # Timing
        if timing.get("urgency_level") in ("critical", "high"):
            immediate.append({
                "title": "Confirm funding date with escrow/title",
                "priority": "high",
                "category": "timing",
                "description": "Ensure payoff funds will be wired and received before the payoff quote expires.",
                "potential_savings": 0.0,
            })

        # Wiring
        if not wire["wiring_instructions_present"]:
            immediate.append({
                "title": "Obtain written wiring instructions",
                "priority": "high",
                "category": "operations",
                "description": "Request official wiring instructions directly from the lender; verify by phone using a known-good number.",
                "potential_savings": 0.0,
            })
            questions.append("Can you provide official wiring instructions and a callback number to verify them?")

        if wire["multiple_payoff_options"]:
            short_term.append({
                "title": "Clarify applicable payoff amount",
                "priority": "medium",
                "category": "clarity",
                "description": "Confirm which payoff amount and date apply to this transaction (e.g., funding date vs. recording date).",
                "potential_savings": 0.0,
            })
            questions.append("Which payoff amount applies to our expected funding/recording date?")

        # Fees
        if cost["fee_ratio_pct"] > 5:
            short_term.append({
                "title": "Review fee breakdown",
                "priority": "medium",
                "category": "fees",
                "description": "Request a detailed breakdown of late fees and other charges; confirm they match the note and servicing history.",
                "potential_savings": 0.0,
            })
            questions.append("Can you provide a line-item breakdown of all fees included in this payoff?")

        return {
            "immediate": immediate,
            "short_term": short_term,
            "questions_to_ask": questions,
        }

    # ---------------- RISK FLAG MESSAGES ----------------

    def _risk_flag_messages(self, flags: Dict[str, Any], timing: Dict[str, Any]) -> List[str]:
        msgs: List[str] = []
        if flags.get("expired_quote"):
            msgs.append("Payoff quote has expired — request an updated payoff statement.")
        if flags.get("short_window") or timing.get("urgency_level") in ("critical", "high"):
            msgs.append("Payoff quote has a short validity window — coordinate funding carefully.")
        if flags.get("high_fee_ratio"):
            msgs.append("Fees are a significant portion of the payoff — review fee details.")
        if flags.get("missing_wiring"):
            msgs.append("Wiring instructions are missing — confirm before sending funds.")
        if flags.get("ambiguous_instructions"):
            msgs.append("Payoff instructions appear ambiguous — clarify applicable amount and date.")
        return msgs
