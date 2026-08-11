"""
insight_extractor_service.py
============================
Universal Actionable Insights Engine for UniDocVerse.

Works across ALL document domains:
  insurance, bank, tax, legal, medical, hr, loan, invoice,
  real_estate, payroll, shipping, compliance, sales, education

Each domain has its own rule set. Rules inspect doc_metadata,
raw_text, summary, and filename to produce structured Insight objects.

Usage (call after doc analysis completes in LangGraph pipeline):
    service = InsightExtractorService()
    insights = service.extract(doc)
    # insights stored back into doc_metadata["insights"]
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Any, Dict, List, Optional
from app.core.pii_redactor import redact_metadata

logger = logging.getLogger(__name__)


# =============================================================================
# UNIVERSAL INSIGHT SCHEMA
# =============================================================================

@dataclass
class Insight:
    """A single actionable insight surfaced from a document."""

    # Severity: critical | warning | info | positive
    severity: str

    # Short machine-readable code for filtering/grouping
    code: str

    # Human-readable title shown in UI
    title: str

    # Detailed explanation shown on hover / in panel
    detail: str

    # Recommended action for the agent/user
    action: str

    # Domain this insight belongs to
    domain: str

    # Optional: numeric value extracted (e.g. deductible amount)
    value: Optional[float] = None
    value_label: Optional[str] = None

    # Optional: ISO date string if time-sensitive
    due_date: Optional[str] = None

    # Tags for cross-doc portfolio queries
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# HELPER UTILITIES
# =============================================================================

def _text(doc) -> str:
    """Safe combined text blob from doc fields."""
    parts = [
        doc.summary or "",
        doc.raw_text[:8000] if doc.raw_text else "",
        json.dumps(doc.doc_metadata or {}),
    ]
    return " ".join(parts).lower()


def _meta(doc) -> Dict[str, Any]:
    return doc.doc_metadata or {}


def _find_amount(text: str, patterns: List[str]) -> Optional[float]:
    """Extract first dollar amount near any pattern keyword."""
    for pat in patterns:
        m = re.search(
            rf"{pat}[^${{0,80}}]*\$?([\d,]+(?:\.\d{{2}})?)",
            text, re.I
        )
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return None


def _find_date(text: str, patterns: List[str]) -> Optional[str]:
    """Extract first date near any pattern keyword."""
    date_rx = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+ \d{1,2},? \d{4})"
    for pat in patterns:
        m = re.search(rf"{pat}.{{0,60}}{date_rx}", text, re.I)
        if m:
            return m.group(1)
    return None


def _days_until(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            d = datetime.strptime(date_str.strip(), fmt).date()
            return (d - date.today()).days
        except ValueError:
            pass
    return None


# =============================================================================
# DOMAIN RULE SETS
# =============================================================================

class InsuranceRules:
    DOMAIN = "insurance"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        # ── Coverage gap: no umbrella / excess liability ──
        has_umbrella = any(w in text for w in ["umbrella", "excess liability", "excess policy"])
        if not has_umbrella:
            insights.append(Insight(
                severity="warning",
                code="NO_UMBRELLA",
                title="No Umbrella Policy Detected",
                detail="Document does not reference an umbrella or excess liability policy.",
                action="Contact client to discuss umbrella coverage options.",
                domain=self.DOMAIN,
                tags=["coverage_gap", "umbrella"],
            ))

        # ── High deductible ──
        deductible = _find_amount(text, ["deductible", "deduct"])
        if deductible and deductible >= 5000:
            insights.append(Insight(
                severity="warning",
                code="HIGH_DEDUCTIBLE",
                title=f"High Deductible: ${deductible:,.0f}",
                detail=f"Deductible of ${deductible:,.0f} may leave client significantly out-of-pocket.",
                action="Review with client if deductible aligns with their financial capacity.",
                domain=self.DOMAIN,
                value=deductible,
                value_label="Deductible",
                tags=["coverage_gap", "deductible"],
            ))

        # ── Policy expiry / renewal ──
        expiry_date = _find_date(text, ["expir", "renewal date", "policy period end", "effective through"])
        days = _days_until(expiry_date)
        if days is not None:
            if days < 0:
                insights.append(Insight(
                    severity="critical",
                    code="POLICY_EXPIRED",
                    title="Policy May Be Expired",
                    detail=f"Expiry date {expiry_date} appears to be in the past.",
                    action="Verify policy status with carrier immediately.",
                    domain=self.DOMAIN,
                    due_date=expiry_date,
                    tags=["renewal", "expired"],
                ))
            elif days <= 30:
                insights.append(Insight(
                    severity="critical",
                    code="EXPIRING_SOON",
                    title=f"Policy Expiring in {days} Days",
                    detail=f"Policy expires on {expiry_date}. Renewal action required.",
                    action="Initiate renewal process and contact client now.",
                    domain=self.DOMAIN,
                    due_date=expiry_date,
                    tags=["renewal", "urgent"],
                ))
            elif days <= 60:
                insights.append(Insight(
                    severity="warning",
                    code="EXPIRING_60_DAYS",
                    title=f"Policy Expiring in {days} Days",
                    detail=f"Policy expires on {expiry_date}.",
                    action="Begin renewal outreach within 2 weeks.",
                    domain=self.DOMAIN,
                    due_date=expiry_date,
                    tags=["renewal"],
                ))

        # ── Exclusion clauses ──
        exclusion_keywords = ["exclud", "not covered", "exclusion", "does not cover", "exempt"]
        exclusion_count = sum(1 for w in exclusion_keywords if w in text)
        if exclusion_count >= 3:
            insights.append(Insight(
                severity="warning",
                code="MULTIPLE_EXCLUSIONS",
                title="Multiple Exclusion Clauses Detected",
                detail=f"Document contains {exclusion_count}+ exclusion-related terms. Review carefully.",
                action="Highlight key exclusions for client in plain language.",
                domain=self.DOMAIN,
                tags=["risk_flag", "exclusions"],
            ))

        # ── Low liability limit ──
        liability = _find_amount(text, ["liability limit", "bodily injury", "property damage limit"])
        if liability and liability < 100000:
            insights.append(Insight(
                severity="warning",
                code="LOW_LIABILITY_LIMIT",
                title=f"Low Liability Limit: ${liability:,.0f}",
                detail=f"Liability limit of ${liability:,.0f} may be insufficient.",
                action="Discuss increasing liability coverage with client.",
                domain=self.DOMAIN,
                value=liability,
                value_label="Liability Limit",
                tags=["coverage_gap", "liability"],
            ))

        # ── Positive: good coverage ──
        if has_umbrella and not any(i.code == "HIGH_DEDUCTIBLE" for i in insights):
            insights.append(Insight(
                severity="positive",
                code="GOOD_COVERAGE",
                title="Comprehensive Coverage Detected",
                detail="Policy includes umbrella coverage and appears well-structured.",
                action="No immediate action needed. Schedule annual review.",
                domain=self.DOMAIN,
                tags=["positive"],
            ))

        return insights


class BankRules:
    DOMAIN = "bank"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        balance = _find_amount(text, ["closing balance", "ending balance", "available balance", "current balance"])
        if balance is not None and balance < 500:
            insights.append(Insight(
                severity="critical",
                code="LOW_BALANCE",
                title=f"Very Low Balance: ${balance:,.2f}",
                detail="Account balance is critically low and may risk overdraft.",
                action="Review cash flow and transfer funds if needed.",
                domain=self.DOMAIN,
                value=balance,
                value_label="Balance",
                tags=["risk_flag", "balance"],
            ))
        elif balance is not None and balance < 2000:
            insights.append(Insight(
                severity="warning",
                code="LOW_BALANCE_WARNING",
                title=f"Low Balance: ${balance:,.2f}",
                detail="Account balance is below recommended minimum.",
                action="Monitor closely and review upcoming expenses.",
                domain=self.DOMAIN,
                value=balance,
                value_label="Balance",
                tags=["balance"],
            ))

        overdraft_count = text.count("overdraft") + text.count("nsf") + text.count("insufficient funds")
        if overdraft_count >= 2:
            insights.append(Insight(
                severity="critical",
                code="OVERDRAFT_PATTERN",
                title="Overdraft Pattern Detected",
                detail=f"Multiple overdraft/NSF references found ({overdraft_count}).",
                action="Review spending habits and consider overdraft protection.",
                domain=self.DOMAIN,
                tags=["risk_flag", "overdraft"],
            ))

        if "unusual" in text or "suspicious" in text or "fraud" in text:
            insights.append(Insight(
                severity="critical",
                code="SUSPICIOUS_ACTIVITY",
                title="Suspicious Activity Keywords Found",
                detail="Document references unusual or suspicious transactions.",
                action="Review flagged transactions and contact bank if needed.",
                domain=self.DOMAIN,
                tags=["risk_flag", "fraud"],
            ))

        return insights


class TaxRules:
    DOMAIN = "tax"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        if "penalty" in text or "late filing" in text or "failure to file" in text:
            insights.append(Insight(
                severity="critical",
                code="TAX_PENALTY",
                title="Tax Penalty Reference Detected",
                detail="Document references IRS penalties or late filing.",
                action="Consult tax professional immediately to resolve penalties.",
                domain=self.DOMAIN,
                tags=["risk_flag", "penalty"],
            ))

        refund = _find_amount(text, ["refund amount", "refund due", "overpayment"])
        if refund and refund > 0:
            insights.append(Insight(
                severity="positive",
                code="TAX_REFUND",
                title=f"Tax Refund: ${refund:,.2f}",
                detail=f"Document shows a refund of ${refund:,.2f}.",
                action="Verify refund has been received or track status.",
                domain=self.DOMAIN,
                value=refund,
                value_label="Refund",
                tags=["positive", "refund"],
            ))

        balance_due = _find_amount(text, ["balance due", "amount owed", "tax due"])
        if balance_due and balance_due > 1000:
            insights.append(Insight(
                severity="warning",
                code="TAX_BALANCE_DUE",
                title=f"Tax Balance Due: ${balance_due:,.2f}",
                detail=f"Outstanding tax liability of ${balance_due:,.2f} detected.",
                action="Arrange payment or payment plan before deadline.",
                domain=self.DOMAIN,
                value=balance_due,
                value_label="Balance Due",
                tags=["risk_flag", "balance_due"],
            ))

        deadline = _find_date(text, ["due date", "filing deadline", "must be filed by"])
        days = _days_until(deadline)
        if days is not None and 0 <= days <= 30:
            insights.append(Insight(
                severity="critical",
                code="TAX_DEADLINE",
                title=f"Tax Deadline in {days} Days",
                detail=f"Filing or payment deadline: {deadline}",
                action="File or pay immediately to avoid penalties.",
                domain=self.DOMAIN,
                due_date=deadline,
                tags=["renewal", "deadline"],
            ))

        return insights


class LegalRules:
    DOMAIN = "legal"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        if "indemnif" not in text and "hold harmless" not in text:
            insights.append(Insight(
                severity="warning",
                code="NO_INDEMNITY",
                title="No Indemnification Clause Detected",
                detail="Contract does not appear to contain indemnification or hold harmless language.",
                action="Consult legal counsel to add appropriate indemnification.",
                domain=self.DOMAIN,
                tags=["coverage_gap", "indemnity"],
            ))

        if "terminat" in text:
            term_date = _find_date(text, ["terminat", "contract end", "agreement end", "expires"])
            days = _days_until(term_date)
            if days is not None and 0 <= days <= 60:
                insights.append(Insight(
                    severity="warning",
                    code="CONTRACT_EXPIRING",
                    title=f"Contract Expiring in {days} Days",
                    detail=f"Contract termination date: {term_date}",
                    action="Initiate renewal or renegotiation discussions.",
                    domain=self.DOMAIN,
                    due_date=term_date,
                    tags=["renewal"],
                ))

        liability_cap = _find_amount(text, ["liability cap", "liability limit", "maximum liability"])
        if liability_cap and liability_cap < 50000:
            insights.append(Insight(
                severity="warning",
                code="LOW_LIABILITY_CAP",
                title=f"Low Liability Cap: ${liability_cap:,.0f}",
                detail="Contract liability cap may be insufficient for business risk.",
                action="Review and negotiate higher liability cap.",
                domain=self.DOMAIN,
                value=liability_cap,
                value_label="Liability Cap",
                tags=["risk_flag", "liability"],
            ))

        high_risk = ["arbitration only", "no jury trial", "waiver of class action", "liquidated damages"]
        found = [kw for kw in high_risk if kw in text]
        if found:
            insights.append(Insight(
                severity="warning",
                code="HIGH_RISK_CLAUSES",
                title=f"High-Risk Clauses: {len(found)} Found",
                detail=f"Detected: {', '.join(found)}",
                action="Have legal counsel review these clauses before signing.",
                domain=self.DOMAIN,
                tags=["risk_flag", "clauses"],
            ))

        return insights


class MedicalRules:
    DOMAIN = "medical"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        if "denied" in text or "denial" in text:
            insights.append(Insight(
                severity="critical",
                code="CLAIM_DENIED",
                title="Claim Denial Detected",
                detail="Document references a denied insurance claim.",
                action="File appeal within the deadline. Review denial reason carefully.",
                domain=self.DOMAIN,
                tags=["risk_flag", "denial"],
            ))

        patient_resp = _find_amount(text, ["patient responsibility", "amount you owe", "your share"])
        if patient_resp and patient_resp > 500:
            insights.append(Insight(
                severity="warning",
                code="HIGH_PATIENT_RESPONSIBILITY",
                title=f"High Patient Responsibility: ${patient_resp:,.2f}",
                detail=f"Patient owes ${patient_resp:,.2f} after insurance.",
                action="Review for billing errors. Check if secondary insurance applies.",
                domain=self.DOMAIN,
                value=patient_resp,
                value_label="Patient Responsibility",
                tags=["coverage_gap", "patient_cost"],
            ))

        if "prior authorization" in text or "prior auth" in text:
            auth_date = _find_date(text, ["authorization expires", "auth valid through", "prior auth"])
            days = _days_until(auth_date)
            if days is not None and days <= 30:
                insights.append(Insight(
                    severity="warning",
                    code="PRIOR_AUTH_EXPIRING",
                    title=f"Prior Authorization Expiring in {days} Days",
                    detail=f"Authorization expires: {auth_date}",
                    action="Renew prior authorization before expiry.",
                    domain=self.DOMAIN,
                    due_date=auth_date,
                    tags=["renewal", "authorization"],
                ))

        return insights


class PayrollRules:
    DOMAIN = "payroll"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        if "garnish" in text:
            insights.append(Insight(
                severity="warning",
                code="WAGE_GARNISHMENT",
                title="Wage Garnishment Detected",
                detail="Payroll document references wage garnishment.",
                action="Verify garnishment compliance and legal requirements.",
                domain=self.DOMAIN,
                tags=["risk_flag", "garnishment"],
            ))

        net_pay = _find_amount(text, ["net pay", "net wages", "take home"])
        gross_pay = _find_amount(text, ["gross pay", "gross wages", "gross earnings"])
        if net_pay and gross_pay and gross_pay > 0:
            ratio = net_pay / gross_pay
            if ratio < 0.55:
                insights.append(Insight(
                    severity="warning",
                    code="HIGH_DEDUCTION_RATIO",
                    title=f"High Deduction Rate: {(1-ratio)*100:.0f}%",
                    detail=f"Net pay is only {ratio*100:.0f}% of gross. Unusually high deductions.",
                    action="Review deduction breakdown for accuracy.",
                    domain=self.DOMAIN,
                    tags=["risk_flag", "deductions"],
                ))

        return insights


class LoanRules:
    DOMAIN = "loan"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        if "arm" in text or "adjustable rate" in text or "variable rate" in text:
            rate_date = _find_date(text, ["rate adjust", "first adjustment", "rate change"])
            days = _days_until(rate_date)
            if days is not None and days <= 90:
                insights.append(Insight(
                    severity="critical",
                    code="ARM_ADJUSTMENT_SOON",
                    title=f"ARM Rate Adjustment in {days} Days",
                    detail=f"Adjustable rate mortgage resets on {rate_date}.",
                    action="Review new rate impact and consider refinancing options.",
                    domain=self.DOMAIN,
                    due_date=rate_date,
                    tags=["renewal", "rate_change"],
                ))

        if "escrow shortage" in text or "escrow deficiency" in text:
            shortage = _find_amount(text, ["escrow shortage", "escrow deficiency"])
            insights.append(Insight(
                severity="warning",
                code="ESCROW_SHORTAGE",
                title=f"Escrow Shortage{f': ${shortage:,.2f}' if shortage else ''}",
                detail="Escrow account has a shortage that may increase monthly payment.",
                action="Plan for escrow payment increase or lump-sum payment.",
                domain=self.DOMAIN,
                value=shortage,
                value_label="Shortage",
                tags=["risk_flag", "escrow"],
            ))

        apr = _find_amount(text, ["annual percentage rate", "apr"])
        if apr and apr > 20:
            insights.append(Insight(
                severity="warning",
                code="HIGH_APR",
                title=f"High APR: {apr:.1f}%",
                detail=f"Loan APR of {apr:.1f}% is significantly above market rate.",
                action="Explore refinancing options to reduce interest burden.",
                domain=self.DOMAIN,
                value=apr,
                value_label="APR %",
                tags=["risk_flag", "apr"],
            ))

        return insights


class InvoiceRules:
    DOMAIN = "invoice"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        due_date = _find_date(text, ["due date", "payment due", "pay by", "due on"])
        days = _days_until(due_date)
        if days is not None:
            if days < 0:
                insights.append(Insight(
                    severity="critical",
                    code="INVOICE_OVERDUE",
                    title=f"Invoice Overdue by {abs(days)} Days",
                    detail=f"Payment was due on {due_date}.",
                    action="Contact vendor/client immediately to resolve overdue payment.",
                    domain=self.DOMAIN,
                    due_date=due_date,
                    tags=["risk_flag", "overdue"],
                ))
            elif days <= 7:
                insights.append(Insight(
                    severity="critical",
                    code="INVOICE_DUE_SOON",
                    title=f"Invoice Due in {days} Days",
                    detail=f"Payment deadline: {due_date}",
                    action="Process payment to avoid late fees.",
                    domain=self.DOMAIN,
                    due_date=due_date,
                    tags=["renewal", "urgent"],
                ))

        amount = _find_amount(text, ["total due", "amount due", "balance due", "total amount"])
        if amount and amount > 10000:
            insights.append(Insight(
                severity="info",
                code="LARGE_INVOICE",
                title=f"Large Invoice: ${amount:,.2f}",
                detail=f"Invoice amount of ${amount:,.2f} requires approval workflow.",
                action="Ensure proper approval before payment.",
                domain=self.DOMAIN,
                value=amount,
                value_label="Invoice Amount",
                tags=["info", "large_amount"],
            ))

        return insights


class SalesRules:
    DOMAIN = "sales"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        records = meta.get("records", [])
        if records:
            try:
                amounts = [float(r.get("amount", r.get("total", r.get("revenue", 0)))) for r in records if r]
                amounts = [a for a in amounts if a > 0]
                if amounts:
                    total = sum(amounts)
                    avg = total / len(amounts)
                    low_performers = [a for a in amounts if a < avg * 0.3]
                    if len(low_performers) >= 3:
                        insights.append(Insight(
                            severity="warning",
                            code="LOW_PERFORMERS",
                            title=f"{len(low_performers)} Low-Performing Entries",
                            detail=f"Entries below 30% of average (${avg:,.0f}).",
                            action="Investigate underperforming products/regions.",
                            domain=self.DOMAIN,
                            tags=["performance", "low_sales"],
                        ))
                    insights.append(Insight(
                        severity="positive",
                        code="TOTAL_REVENUE",
                        title=f"Total Revenue: ${total:,.2f}",
                        detail=f"Across {len(amounts)} records. Average: ${avg:,.2f}",
                        action="Compare against targets and prior periods.",
                        domain=self.DOMAIN,
                        value=total,
                        value_label="Total Revenue",
                        tags=["positive", "revenue"],
                    ))
            except Exception:
                pass

        return insights


class HRRules:
    DOMAIN = "hr"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        if "performance improvement plan" in text or "pip" in text:
            insights.append(Insight(
                severity="warning",
                code="PIP_DETECTED",
                title="Performance Improvement Plan Reference",
                detail="Document references a PIP or performance action.",
                action="Ensure proper HR documentation and follow-up.",
                domain=self.DOMAIN,
                tags=["risk_flag", "performance"],
            ))

        review_date = _find_date(text, ["review date", "performance review", "evaluation date"])
        days = _days_until(review_date)
        if days is not None and 0 <= days <= 30:
            insights.append(Insight(
                severity="info",
                code="REVIEW_DUE",
                title=f"Performance Review Due in {days} Days",
                detail=f"Review scheduled: {review_date}",
                action="Prepare review documentation.",
                domain=self.DOMAIN,
                due_date=review_date,
                tags=["renewal", "review"],
            ))

        return insights


class ComplianceRules:
    DOMAIN = "compliance"

    def run(self, doc, text: str, meta: Dict) -> List[Insight]:
        insights = []

        violation_kws = ["violation", "non-compliance", "breach", "infraction", "penalty"]
        found = [kw for kw in violation_kws if kw in text]
        if found:
            insights.append(Insight(
                severity="critical",
                code="COMPLIANCE_VIOLATION",
                title=f"Compliance Violation: {len(found)} Indicators",
                detail=f"Keywords found: {', '.join(found)}",
                action="Escalate to compliance officer immediately.",
                domain=self.DOMAIN,
                tags=["risk_flag", "violation"],
            ))

        audit_date = _find_date(text, ["audit date", "audit scheduled", "review by"])
        days = _days_until(audit_date)
        if days is not None and 0 <= days <= 30:
            insights.append(Insight(
                severity="warning",
                code="AUDIT_APPROACHING",
                title=f"Audit in {days} Days",
                detail=f"Audit scheduled: {audit_date}",
                action="Ensure all documentation is audit-ready.",
                domain=self.DOMAIN,
                due_date=audit_date,
                tags=["renewal", "audit"],
            ))

        return insights


# =============================================================================
# DOMAIN REGISTRY
# =============================================================================

DOMAIN_RULES = {
    "insurance":  InsuranceRules(),
    "bank":       BankRules(),
    "tax":        TaxRules(),
    "legal":      LegalRules(),
    "medical":    MedicalRules(),
    "payroll":    PayrollRules(),
    "loan":       LoanRules(),
    "invoice":    InvoiceRules(),
    "sales":      SalesRules(),
    "hr":         HRRules(),
    "compliance": ComplianceRules(),
}

# Aliases for doc_type variations
DOMAIN_ALIASES = {
    "bank_statement":       "bank",
    "tax_form":             "tax",
    "w2":                   "tax",
    "1099":                 "tax",
    "legal_contract":       "legal",
    "contract":             "legal",
    "medical_eob":          "medical",
    "eob":                  "medical",
    "pay_stub":             "payroll",
    "paystub":              "payroll",
    "mortgage":             "loan",
    "sales_report":         "sales",
    "hr_document":          "hr",
    "resume":               "hr",
    "government_form":      "compliance",
    "shipping":             "compliance",
}


# =============================================================================
# MAIN SERVICE
# =============================================================================

class InsightExtractorService:
    """
    Extract actionable insights from any analyzed document.
    Call after LangGraph analysis pipeline completes.
    """

    def extract(self, doc) -> List[Dict[str, Any]]:
        """
        Returns list of insight dicts ready to store in doc_metadata["insights"].
        """
        doc_type = (doc.doc_type or "unknown").lower().strip()
        domain = DOMAIN_ALIASES.get(doc_type, doc_type)

        text = _text(doc)
        meta = _meta(doc)

        insights: List[Insight] = []

        rule_set = DOMAIN_RULES.get(domain)
        if rule_set:
            try:
                insights = rule_set.run(doc, text, meta)
                logger.info(
                    f"✅ [InsightExtractor] {doc.filename} → "
                    f"domain={domain}, insights={len(insights)}"
                )
            except Exception as e:
                logger.error(f"❌ [InsightExtractor] {doc.filename}: {e}", exc_info=True)
        else:
            logger.info(f"ℹ️ [InsightExtractor] No rules for domain '{domain}' — skipping")

        return [i.to_dict() for i in insights]

    def extract_and_save(self, doc, db) -> int:
        """
        Extract insights and persist them into doc.doc_metadata["insights"].
        Returns count of insights saved.
        """
        insights = self.extract(doc)
        if not insights:
            return 0

        try:
            meta = dict(doc.doc_metadata or {})
            meta["insights"] = insights
            meta["insights_generated_at"] = datetime.utcnow().isoformat()
            doc.doc_metadata = redact_metadata(meta)
            db.add(doc)
            db.commit()
            logger.info(f"💾 Saved {len(insights)} insights for {doc.filename}")
            return len(insights)
        except Exception as e:
            db.rollback()
            logger.error(f"❌ Failed to save insights: {e}", exc_info=True)
            return 0

    @staticmethod
    def severity_order(s: str) -> int:
        return {"critical": 0, "warning": 1, "info": 2, "positive": 3}.get(s, 4)

    @staticmethod
    def portfolio_summary(all_insights: List[Dict]) -> Dict[str, Any]:
        """
        Aggregate insights across multiple documents for portfolio view.
        """
        counts = {"critical": 0, "warning": 0, "info": 0, "positive": 0}
        codes: Dict[str, int] = {}
        tags: Dict[str, int] = {}

        for ins in all_insights:
            sev = ins.get("severity", "info")
            counts[sev] = counts.get(sev, 0) + 1
            code = ins.get("code", "")
            codes[code] = codes.get(code, 0) + 1
            for tag in ins.get("tags", []):
                tags[tag] = tags.get(tag, 0) + 1

        top_issues = sorted(codes.items(), key=lambda x: x[1], reverse=True)[:5]
        top_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)[:8]

        risk_score = min(100, counts["critical"] * 20 + counts["warning"] * 8 + counts["info"] * 2)

        return {
            "severity_counts": counts,
            "risk_score": risk_score,
            "risk_level": "high" if risk_score >= 60 else "medium" if risk_score >= 30 else "low",
            "top_issues": [{"code": c, "count": n} for c, n in top_issues],
            "top_tags": [{"tag": t, "count": n} for t, n in top_tags],
            "total_insights": len(all_insights),
        }