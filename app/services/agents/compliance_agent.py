"""
Compliance & Risk Agent

Specializes in: audit reports, risk assessments, regulatory filings,
compliance deadlines, SOC reports, policy violations, audit trails.

Tools:
  - search_compliance_docs  : vector search on compliance/audit documents
  - get_risk_summary        : risk scores, findings, open items
  - get_audit_findings      : audit observations, recommendations, status
  - get_deadlines           : compliance deadlines and filing dates
"""
import logging
from typing import Dict, List

from sqlalchemy import text

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

COMPLIANCE_DOC_TYPES = [
    "audit_report", "risk_assessment", "compliance_report",
    "regulatory_filing", "soc_report", "policy_document",
    "terms_of_service", "privacy_policy",
]


class ComplianceAgent(BaseAgent):

    domain = "compliance"

    system_prompt = """You are a Compliance & Risk specialist agent.
You analyze audit reports, risk assessments, regulatory filings, and compliance documents.
Focus on: risk ratings, audit findings, open action items, compliance deadlines,
regulatory requirements, control gaps, remediation status.

When asked about risk or findings, use get_risk_summary.
When asked about specific audit observations, use get_audit_findings.
When asked about deadlines or filing dates, use get_deadlines.
For specific policy or regulatory text, use search_compliance_docs.

IMPORTANT — vague queries: if the user does not specify a report or audit period,
answer based on ALL available compliance documents and state the scope clearly.
Example: "Based on all audit reports on file:"

Return JSON: {"thought": "...", "tool": "tool_name", "args": {...}}
When done: {"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def register_tools(self):
        self._tool("search_compliance_docs", self._search_compliance_docs)
        self._tool("get_risk_summary",       self._get_risk_summary)
        self._tool("get_audit_findings",     self._get_audit_findings)
        self._tool("get_deadlines",          self._get_deadlines)

    def _search_compliance_docs(self, query: str, doc_ids: List[str] = None, top_k: int = 6) -> str:
        """Vector search scoped to compliance and audit documents."""
        from app.services.document_react_agent import _do_execute_action
        result = _do_execute_action(
            "search_chunks",
            {"query": query, "doc_ids": doc_ids or [], "top_k": top_k,
             "doc_type_filter": COMPLIANCE_DOC_TYPES},
            self._db,
        )
        chunks = result if isinstance(result, list) else (result or {}).get("chunks", [])
        if not chunks:
            return "No relevant compliance documents found."
        return "\n\n".join(
            f"[{c.get('filename','')}] {c.get('chunk_text','')[:500]}"
            for c in chunks[:top_k]
        )

    def _get_risk_summary(self, doc_ids: List[str] = None) -> str:
        """Overall risk ratings and open findings from audit/risk documents."""
        doc_ids = doc_ids or []
        params: Dict = {}
        params.update(self._tenant_params)
        doc_filter = "AND tenant_id = :tenant_id"
        if doc_ids:
            placeholders = ", ".join(f":did_{i}" for i in range(len(doc_ids)))
            doc_filter += f" AND id IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"did_{i}"] = did

        sql = text(f"""
            SELECT filename,
                   extracted_facts->>'risk_rating'       AS risk_rating,
                   extracted_facts->>'overall_score'     AS overall_score,
                   extracted_facts->>'open_findings'     AS open_findings,
                   extracted_facts->>'critical_count'    AS critical_count,
                   extracted_facts->>'high_count'        AS high_count,
                   extracted_facts->>'medium_count'      AS medium_count,
                   extracted_facts->>'audit_period'      AS audit_period,
                   extracted_facts->>'auditor'           AS auditor
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{COMPLIANCE_DOC_TYPES})
              {doc_filter}
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No risk data found."
            lines = []
            for r in rows:
                lines.append(f"**{r.filename}**")
                if r.audit_period:   lines.append(f"  Audit Period: {r.audit_period}")
                if r.auditor:        lines.append(f"  Auditor: {r.auditor}")
                if r.risk_rating:    lines.append(f"  Overall Risk: {r.risk_rating}")
                if r.overall_score:  lines.append(f"  Score: {r.overall_score}")
                if r.critical_count: lines.append(f"  Critical Findings: {r.critical_count}")
                if r.high_count:     lines.append(f"  High Findings: {r.high_count}")
                if r.medium_count:   lines.append(f"  Medium Findings: {r.medium_count}")
                if r.open_findings:  lines.append(f"  Open Items: {r.open_findings}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[ComplianceAgent] get_risk_summary error: {e}")
            return f"Risk query failed: {e}"

    def _get_audit_findings(self, doc_ids: List[str] = None, severity: str = "") -> str:
        """Individual audit findings, observations, and remediation status."""
        doc_ids = doc_ids or []
        params: Dict = {}
        params.update(self._tenant_params)
        doc_filter = "AND tenant_id = :tenant_id"
        if doc_ids:
            placeholders = ", ".join(f":did_{i}" for i in range(len(doc_ids)))
            doc_filter += f" AND id IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"did_{i}"] = did

        severity_filter = ""
        if severity:
            severity_filter = "AND f->>'severity' ILIKE :severity"
            params["severity"] = f"%{severity}%"

        sql = text(f"""
            SELECT filename,
                   f->>'finding_id'    AS finding_id,
                   f->>'severity'      AS severity,
                   f->>'title'         AS title,
                   f->>'status'        AS status,
                   f->>'due_date'      AS due_date,
                   f->>'owner'         AS owner
            FROM documents,
                 jsonb_array_elements(extracted_facts->'findings') f
            WHERE extracted_facts IS NOT NULL
              AND extracted_facts->'findings' IS NOT NULL
              {doc_filter} {severity_filter}
            ORDER BY f->>'severity', f->>'due_date'
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No audit findings found."
            lines = ["| ID | Severity | Title | Status | Due Date | Owner | Source |",
                     "|----|----------|-------|--------|----------|-------|--------|"]
            for r in rows:
                lines.append(
                    f"| {r.finding_id or '-'} | {r.severity or '-'} | "
                    f"{(r.title or '-')[:40]} | {r.status or '-'} | "
                    f"{r.due_date or '-'} | {r.owner or '-'} | {r.filename} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[ComplianceAgent] get_audit_findings error: {e}")
            return f"Findings query failed: {e}"

    def _get_deadlines(self, doc_ids: List[str] = None) -> str:
        """Upcoming compliance deadlines and regulatory filing dates."""
        doc_ids = doc_ids or []
        params: Dict = {}
        params.update(self._tenant_params)
        doc_filter = "AND tenant_id = :tenant_id"
        if doc_ids:
            placeholders = ", ".join(f":did_{i}" for i in range(len(doc_ids)))
            doc_filter += f" AND id IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"did_{i}"] = did

        sql = text(f"""
            SELECT filename,
                   dl->>'requirement'  AS requirement,
                   dl->>'deadline'     AS deadline,
                   dl->>'status'       AS status,
                   dl->>'regulator'    AS regulator,
                   dl->>'penalty'      AS penalty
            FROM documents,
                 jsonb_array_elements(extracted_facts->'deadlines') dl
            WHERE extracted_facts IS NOT NULL
              AND extracted_facts->'deadlines' IS NOT NULL
              {doc_filter}
            ORDER BY dl->>'deadline' ASC NULLS LAST
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No compliance deadline data found."
            lines = ["| Requirement | Deadline | Status | Regulator | Penalty | Source |",
                     "|-------------|----------|--------|-----------|---------|--------|"]
            for r in rows:
                lines.append(
                    f"| {(r.requirement or '-')[:40]} | {r.deadline or '-'} | "
                    f"{r.status or '-'} | {r.regulator or '-'} | "
                    f"{r.penalty or '-'} | {r.filename} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[ComplianceAgent] get_deadlines error: {e}")
            return f"Deadlines query failed: {e}"
