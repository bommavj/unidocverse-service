"""
Healthcare & Medical Admin Agent

Specializes in: medical records, lab reports, EOBs, prescriptions,
diagnoses, treatment plans, HIPAA-compliant document analysis.

Tools:
  - search_medical_records  : vector search on medical documents
  - get_patient_summary     : demographics, diagnoses, medications
  - get_billing_summary     : charges, insurance payments, patient responsibility
"""
import logging
from typing import Dict, List

from sqlalchemy import text

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

MEDICAL_DOC_TYPES = [
    "medical_record", "medical", "eob", "lab_report",
    "prescription", "radiology_report", "discharge_summary",
]


class MedicalAgent(BaseAgent):

    domain = "medical"

    system_prompt = """You are a Healthcare & Medical Records specialist agent.
You analyze medical records, lab reports, EOBs, and clinical documents.
Focus on: diagnoses, treatments, medications, lab values, billing charges, insurance payments.

IMPORTANT: Never speculate on medical advice. Report only what is documented.
When asked about clinical content, use search_medical_records.
When asked about patient info or diagnoses, use get_patient_summary.
When asked about bills or insurance payments, use get_billing_summary.

Return JSON: {"thought": "...", "tool": "tool_name", "args": {...}}
When done: {"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def register_tools(self):
        self._tool("search_medical_records", self._search_medical_records)
        self._tool("get_patient_summary",    self._get_patient_summary)
        self._tool("get_billing_summary",    self._get_billing_summary)

    def _search_medical_records(self, query: str, doc_ids: List[str] = None, top_k: int = 6) -> str:
        """Vector search scoped to medical and clinical documents."""
        from app.services.document_react_agent import _do_execute_action
        result = _do_execute_action(
            "search_chunks",
            {"query": query, "doc_ids": doc_ids or [], "top_k": top_k,
             "doc_type_filter": MEDICAL_DOC_TYPES},
            self._db,
        )
        chunks = result if isinstance(result, list) else (result or {}).get("chunks", [])
        if not chunks:
            return "No relevant medical records found."
        return "\n\n".join(
            f"[{c.get('filename','')}] {c.get('chunk_text','')[:500]}"
            for c in chunks[:top_k]
        )

    def _get_patient_summary(self, doc_ids: List[str] = None) -> str:
        """Patient demographics, diagnoses, medications from extracted_facts."""
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
                   extracted_facts->>'patient_name'   AS patient_name,
                   extracted_facts->>'date_of_birth'  AS dob,
                   extracted_facts->>'diagnosis'      AS diagnosis,
                   extracted_facts->>'medications'    AS medications,
                   extracted_facts->>'physician'      AS physician,
                   extracted_facts->>'visit_date'     AS visit_date,
                   extracted_facts->>'facility'       AS facility
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{MEDICAL_DOC_TYPES})
              {doc_filter}
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No patient data found in extracted facts."
            lines = []
            for r in rows:
                lines.append(f"**{r.filename}**")
                if r.patient_name: lines.append(f"  Patient: {r.patient_name}")
                if r.dob:          lines.append(f"  DOB: {r.dob}")
                if r.visit_date:   lines.append(f"  Visit Date: {r.visit_date}")
                if r.physician:    lines.append(f"  Physician: {r.physician}")
                if r.facility:     lines.append(f"  Facility: {r.facility}")
                if r.diagnosis:    lines.append(f"  Diagnosis: {r.diagnosis}")
                if r.medications:  lines.append(f"  Medications: {r.medications}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[MedicalAgent] get_patient_summary error: {e}")
            return f"Patient query failed: {e}"

    def _get_billing_summary(self, doc_ids: List[str] = None) -> str:
        """Medical billing: charges, insurance payments, patient responsibility."""
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
                   extracted_facts->>'total_charges'         AS total_charges,
                   extracted_facts->>'insurance_paid'        AS insurance_paid,
                   extracted_facts->>'patient_responsibility' AS patient_responsibility,
                   extracted_facts->>'service_date'          AS service_date,
                   extracted_facts->>'provider'              AS provider
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{MEDICAL_DOC_TYPES})
              {doc_filter}
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No billing data found."
            lines = ["| Provider | Service Date | Total Charges | Insurance Paid | Patient Owes | Source |",
                     "|----------|-------------|---------------|----------------|-------------|--------|"]
            for r in rows:
                lines.append(
                    f"| {r.provider or '-'} | {r.service_date or '-'} | "
                    f"{r.total_charges or '-'} | {r.insurance_paid or '-'} | "
                    f"{r.patient_responsibility or '-'} | {r.filename} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[MedicalAgent] get_billing_summary error: {e}")
            return f"Billing query failed: {e}"
