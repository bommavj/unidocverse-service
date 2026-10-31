"""
post_processing_registry.py
============================
Hook registry that fires domain-specific workflows after
document processing completes.

FinalizeNode stays clean — only saves to DB.
This registry runs AFTER the pipeline, in processing_service.py.

Adding a new domain:
    1. Write a service class with a .run(doc_id, doc_type, fields, analysis) method
    2. Register it here — one line
    3. Done — no other files touched

Usage in processing_service.py:
    from app.services.post_processing_registry import PostProcessingRegistry
    PostProcessingRegistry.run(document_id, db)
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Type
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

class PostProcessingRegistry:
    """
    Runs registered hooks after document processing completes.
    Each hook is a callable that receives (document, db) and returns a result dict.
    Hooks are keyed by doc_type — one type can have multiple hooks.
    Use "*" to register a hook for ALL doc types.
    """

    # ── Register hooks here ───────────────────────────────────────────────────
    # Format: { "doc_type": [HookClass, ...], "*": [GlobalHook, ...] }
    #
    # To add a new domain hook, just add it to the list below.
    # Order matters — hooks run in list order.

    _HOOKS: Dict[str, List] = {

        # ── Insurance AMS ─────────────────────────────────────────────────────
        "declarations_page":        ["ams"],
        "insurance_policy":         ["ams"],
        "renewal_notice":           ["ams"],
        "nonrenewal_notice":        ["ams"],
        "cancellation_notice":      ["ams"],
        "endorsement":              ["ams"],
        "binder":                   ["ams"],
        "certificate_of_insurance": ["ams"],
        "commission_statement":     ["ams"],
        "commission_report":        ["ams"],
        "loss_run":                 ["ams"],
        "loss_run_report":          ["ams"],
        "claims_history":           ["ams"],
        "insurance_claim":          ["ams"],
        "auto_insurance_claim":     ["ams"],

        # ── Immigration: person on the notice → client (after approval) ─────
        "uscis_receipt":     ["immigration"],
        "uscis_notice":      ["immigration"],
        "immigration_receipt":["immigration"],
        "i797":              ["immigration"],
        "i94":               ["immigration"],
        "i20":               ["immigration"],
        "ds2019":            ["immigration"],
        "i130":              ["immigration"],
        "i140":              ["immigration"],
        "i485":              ["immigration"],
        "i751":              ["immigration"],
        "i765":              ["immigration"],
        "i90":               ["immigration"],
        "i129f":             ["immigration"],
        "i131":              ["immigration"],
        "i539":              ["immigration"],
        "i821d":             ["immigration"],
        "i589":              ["immigration"],
        "i864":              ["immigration"],
        "i360":              ["immigration"],
        "i526":              ["immigration"],
        "marriage_certificate": ["immigration"],
        "birth_certificate":    ["immigration"],
        "n400":              ["immigration"],
        "ead":               ["immigration"],
        "ead_card":          ["immigration"],
        "green_card":        ["immigration"],
        "permanent_resident_card":["immigration"],
        "naturalization_certificate":["immigration"],
        "passport":          ["immigration"],
        "passport_document": ["immigration"],
        "visa":              ["immigration"],
        "visa_document":     ["immigration"],
        "immigration":       ["immigration"],

        # ── Future domains — uncomment when ready ─────────────────────────────
        "contract":           ["legal"],
        "nda":                ["legal"],
        "lease_agreement":    ["legal", "real_estate"],
        "regulatory_filing":  ["legal"],
        "medical_chart":      ["healthcare"],
        "intake_form":        ["healthcare"],
        "lab_report":         ["healthcare"],
        "bank_statement":     ["finance"],
        "tax_form":           ["finance"],
        "invoice":            ["finance"],
        "appraisal":          ["real_estate"],
        "title_deed":         ["real_estate"],
        "closing_disclosure": ["real_estate"],
        "resume":             ["hr"],
        "handbook":           ["hr"],
        "performance_review": ["hr"],
        "purchase_order":     ["supply_chain", "finance"],
        "bill_of_lading":     ["supply_chain"],
        "customs_manifest":   ["supply_chain"],

        # ── Global hooks — run for every completed document ───────────────────
        # "*": ["notification"],
    }

    # ── Hook resolver ─────────────────────────────────────────────────────────
    # Maps hook name → loader function (lazy import — no circular deps)

    @staticmethod
    def _load_hook(name: str):
        if name == "ams":
            from app.services.ams_workflow_service import AMSWorkflowService
            return AMSWorkflowService
        if name == "legal":
            from app.services.legal_workflow_service import LegalWorkflowService
            return LegalWorkflowService
        if name == "healthcare":
            from app.services.healthcare_workflow_service import HealthcareWorkflowService
            return HealthcareWorkflowService
        if name == "finance":
            from app.services.finance_workflow_service import FinanceWorkflowService
            return FinanceWorkflowService
        if name == "real_estate":
            from app.services.real_estate_workflow_service import RealEstateWorkflowService
            return RealEstateWorkflowService
        if name == "hr":
            from app.services.hr_workflow_service import HRWorkflowService
            return HRWorkflowService
        if name == "immigration":
            from app.services.immigration_workflow_service import ImmigrationWorkflowService
            return ImmigrationWorkflowService
        if name == "supply_chain":
            from app.services.supply_chain_workflow_service import SupplyChainWorkflowService
            return SupplyChainWorkflowService

        raise ValueError(f"Unknown hook: '{name}'")

    # ── Main entry point ──────────────────────────────────────────────────────

    @classmethod
    def run(cls, document_id: str, db: Session) -> Dict[str, Any]:
        """
        Load the document from DB, determine its type,
        and fire all registered hooks for that type.

        Called from processing_service.py after pipeline completes.
        Never raises — all hook errors are caught individually.
        """
        results = {}

        try:
            # Load document + its analysis result from DB
            from app.core.database import SessionLocal
            from app.models.document import Document

            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc:
                logger.warning(f"⚠ [PostProc] Document {document_id} not found")
                return {"error": "document_not_found"}

            doc_type = (doc.doc_type or "").lower().strip()
            if not doc_type or doc_type == "unknown":
                logger.debug(f"⏭ [PostProc] Skipping unknown doc_type for {document_id[:8]}…")
                return {"skipped": True}

            # Collect hooks for this doc_type + global hooks
            hook_names = (
                cls._HOOKS.get(doc_type, []) +
                cls._HOOKS.get("*", [])
            )

            if not hook_names:
                logger.debug(f"⏭ [PostProc] No hooks for doc_type='{doc_type}'")
                return {"skipped": True}

            logger.info(
                f"⚙ [PostProc] doc={document_id[:8]}… "
                f"type={doc_type} → hooks={hook_names}"
            )

            # Build state from doc_metadata
            state = cls._build_state_from_doc(doc)

            # Fire each hook
            for hook_name in hook_names:
                try:
                    HookClass = cls._load_hook(hook_name)
                    result    = HookClass(db).process(doc, state)
                    results[hook_name] = result

                    if not result.get("skipped") and not result.get("error"):
                        logger.info(
                            f"  ✅ [{hook_name}] {result.get('action','done')} | "
                            f"route={result.get('route','?')}"
                        )
                    elif result.get("skipped"):
                        logger.debug(f"  ⏭ [{hook_name}] skipped: {result.get('reason','')}")
                    elif result.get("error"):
                        logger.warning(f"  ⚠ [{hook_name}] error: {result['error']}")

                except Exception as hook_err:
                    logger.error(
                        f"  ❌ [{hook_name}] failed (non-fatal): {hook_err}",
                        exc_info=True
                    )
                    results[hook_name] = {"error": str(hook_err)}

        except Exception as e:
            logger.error(f"❌ [PostProc] Registry failed: {e}", exc_info=True)
            results["registry_error"] = str(e)

        return results

    @staticmethod
    def _build_state_from_doc(doc) -> Dict[str, Any]:
        """
        Reconstruct the pipeline state from what was saved in doc_metadata.
        This is what the hooks expect — same shape as LangGraph state.
        """
        meta = doc.doc_metadata or {}
        return {
            "document_id":    str(doc.id),
            "filename":       doc.filename or "",
            "doc_type":       doc.doc_type or "",
            "raw_text":       doc.raw_text or "",
            "summary":        doc.summary or "",
            "classification": meta.get("classification", {}),
            "analyzed_data":  meta.get("analyzed_data", {}),
            # analysis_result is where DeclarationsAnalyzer result lives
            "analysis_result": (
                meta.get("analyzed_data") or
                meta.get("analysis_result") or
                {}
            ),
            "insights":       meta.get("insights", []),
            "keywords":       meta.get("keywords", []),
            "doc_metadata":   meta,
        }