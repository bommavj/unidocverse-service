"""
Supply Chain & Logistics Agent

Specializes in: purchase orders, invoices, bills of lading, customs manifests,
vendor agreements, shipping records, inventory.

Tools:
  - search_supply_docs    : vector search on supply chain documents
  - get_po_summary        : purchase orders — vendor, amount, status
  - get_invoice_status    : invoice matching, discrepancies, payment status
  - get_shipment_details  : shipping, tracking, customs, delivery
"""
import logging
from typing import Dict, List

from sqlalchemy import text

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

SUPPLY_CHAIN_DOC_TYPES = [
    "purchase_order", "invoice", "bill_of_lading", "customs_manifest",
    "packing_list", "delivery_note", "vendor_agreement", "inventory_report",
    "order_history", "asset_register",
]


class SupplyChainAgent(BaseAgent):

    domain = "supply_chain"

    system_prompt = """You are a Supply Chain & Logistics specialist agent.
You analyze purchase orders, invoices, shipping documents, and vendor agreements.
Focus on: PO numbers, vendor names, amounts, delivery dates, shipment status,
invoice discrepancies, customs details, payment terms.


AVAILABLE TOOLS (only these exact names are valid):
- search_supply_docs
- get_po_summary
- get_invoice_status
- get_shipment_details
- finish

STRICT RULES — you MUST follow these exactly:
1. Only call tools from the AVAILABLE TOOLS list above. Never call ask_user, search, or any unlisted tool name.
2. If information is not found after 2 searches, call finish with what you know.
3. Never ask the user for clarification — infer intent and proceed.

When asked about orders or vendors, use get_po_summary.
When asked about invoices or payments, use get_invoice_status.
When asked about shipping or delivery, use get_shipment_details.
For specific terms or clauses, use search_supply_docs.

Return JSON: {"thought": "...", "tool": "tool_name", "args": {...}}
When done: {"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def register_tools(self):
        self._tool("search_supply_docs",   self._search_supply_docs)
        self._tool("get_po_summary",       self._get_po_summary)
        self._tool("get_invoice_status",   self._get_invoice_status)
        self._tool("get_shipment_details", self._get_shipment_details)

    def _search_supply_docs(self, query: str, doc_ids: List[str] = None, top_k: int = 6) -> str:
        """Vector search scoped to supply chain documents."""
        from app.services.document_react_agent import _do_execute_action
        result = _do_execute_action(
            "search_chunks",
            {"query": query, "doc_ids": doc_ids or [], "top_k": top_k,
             "doc_type_filter": SUPPLY_CHAIN_DOC_TYPES},
            self._db,
        )
        chunks = result if isinstance(result, list) else (result or {}).get("chunks", [])
        if not chunks:
            return "No relevant supply chain documents found."
        return "\n\n".join(
            f"[{c.get('filename','')}] {c.get('chunk_text','')[:500]}"
            for c in chunks[:top_k]
        )

    def _get_po_summary(self, doc_ids: List[str] = None, vendor: str = "") -> str:
        """Purchase orders — vendor, amount, delivery date, status."""
        doc_ids = doc_ids or []
        params: Dict = {}
        params.update(self._tenant_params)
        doc_filter = "AND tenant_id = :tenant_id"
        if doc_ids:
            placeholders = ", ".join(f":did_{i}" for i in range(len(doc_ids)))
            doc_filter += f" AND id IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"did_{i}"] = did

        vendor_filter = ""
        if vendor:
            vendor_filter = "AND extracted_facts->>'vendor' ILIKE :vendor"
            params["vendor"] = f"%{vendor}%"

        sql = text(f"""
            SELECT filename,
                   extracted_facts->>'po_number'       AS po_number,
                   extracted_facts->>'vendor'          AS vendor,
                   extracted_facts->>'po_date'         AS po_date,
                   extracted_facts->>'total_amount'    AS total_amount,
                   extracted_facts->>'delivery_date'   AS delivery_date,
                   extracted_facts->>'status'          AS status,
                   extracted_facts->>'payment_terms'   AS payment_terms
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{SUPPLY_CHAIN_DOC_TYPES})
              {doc_filter} {vendor_filter}
            ORDER BY extracted_facts->>'po_date' DESC NULLS LAST
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                # Fall back to raw chunks
                chunk_filter = "WHERE d.doc_type = ANY(ARRAYSUPPLY_CHAIN_DOC_TYPES)"
                chunk_params: Dict = {}
                if doc_ids:
                    placeholders = ", ".join(f":did_{{i}}" for i in range(len(doc_ids)))
                    chunk_filter += f" AND dc.document_id IN ({{placeholders}})"
                    for i, did in enumerate(doc_ids):
                        chunk_params[f"did_{{i}}"] = did
                chunks = self._db.execute(text(f"""
                    SELECT dc.chunk_text, dc.page_number, d.filename
                    FROM document_chunks dc JOIN documents d ON d.id = dc.document_id
                    {{chunk_filter}}
                    ORDER BY dc.page_number, dc.chunk_index LIMIT 25
                """), chunk_params).fetchall()
                if not chunks:
                    return "No purchase order data found."
                return "Raw document text (extract the relevant information from this):\n\n" + "\n\n".join(
                    f"[{{c.filename}} p.{{c.page_number}}] {{c.chunk_text[:500]}}" for c in chunks
                )
            lines = ["| PO # | Vendor | Amount | PO Date | Delivery | Status | Source |",
                     "|------|--------|--------|---------|----------|--------|--------|"]
            for r in rows:
                lines.append(
                    f"| {r.po_number or '-'} | {r.vendor or '-'} | "
                    f"{r.total_amount or '-'} | {r.po_date or '-'} | "
                    f"{r.delivery_date or '-'} | {r.status or '-'} | {r.filename} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[SupplyChainAgent] get_po_summary error: {e}")
            return f"PO query failed: {e}"

    def _get_invoice_status(self, doc_ids: List[str] = None) -> str:
        """Invoice amounts, due dates, payment status, and discrepancies."""
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
                   extracted_facts->>'invoice_number'  AS invoice_number,
                   extracted_facts->>'vendor'          AS vendor,
                   extracted_facts->>'invoice_date'    AS invoice_date,
                   extracted_facts->>'due_date'        AS due_date,
                   extracted_facts->>'total_amount'    AS total_amount,
                   extracted_facts->>'status'          AS status,
                   extracted_facts->>'po_reference'    AS po_reference
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{SUPPLY_CHAIN_DOC_TYPES})
              {doc_filter}
            ORDER BY extracted_facts->>'due_date' ASC NULLS LAST
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No invoice data found."
            lines = ["| Invoice # | Vendor | Amount | Invoice Date | Due Date | Status | PO Ref | Source |",
                     "|-----------|--------|--------|-------------|----------|--------|--------|--------|"]
            for r in rows:
                lines.append(
                    f"| {r.invoice_number or '-'} | {r.vendor or '-'} | "
                    f"{r.total_amount or '-'} | {r.invoice_date or '-'} | "
                    f"{r.due_date or '-'} | {r.status or '-'} | "
                    f"{r.po_reference or '-'} | {r.filename} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[SupplyChainAgent] get_invoice_status error: {e}")
            return f"Invoice query failed: {e}"

    def _get_shipment_details(self, doc_ids: List[str] = None) -> str:
        """Shipment tracking, carrier, origin/destination, customs status."""
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
                   extracted_facts->>'bl_number'        AS bl_number,
                   extracted_facts->>'carrier'          AS carrier,
                   extracted_facts->>'shipper'          AS shipper,
                   extracted_facts->>'consignee'        AS consignee,
                   extracted_facts->>'origin_port'      AS origin_port,
                   extracted_facts->>'destination_port' AS destination_port,
                   extracted_facts->>'vessel'           AS vessel,
                   extracted_facts->>'eta'              AS eta,
                   extracted_facts->>'cargo_description' AS cargo
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{SUPPLY_CHAIN_DOC_TYPES})
              {doc_filter}
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No shipment data found."
            lines = []
            for r in rows:
                lines.append(f"**{r.filename}**")
                if r.bl_number:    lines.append(f"  B/L #: {r.bl_number}")
                if r.carrier:      lines.append(f"  Carrier: {r.carrier}")
                if r.vessel:       lines.append(f"  Vessel: {r.vessel}")
                if r.shipper:      lines.append(f"  Shipper: {r.shipper}")
                if r.consignee:    lines.append(f"  Consignee: {r.consignee}")
                if r.origin_port:  lines.append(f"  Origin: {r.origin_port}")
                if r.destination_port: lines.append(f"  Destination: {r.destination_port}")
                if r.eta:          lines.append(f"  ETA: {r.eta}")
                if r.cargo:        lines.append(f"  Cargo: {r.cargo}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[SupplyChainAgent] get_shipment_details error: {e}")
            return f"Shipment query failed: {e}"
