"""
ams_workflow_service.py
=======================
Document-type-aware AMS workflow router.

Single entry point:
    AMSWorkflowService(db).process(document, state)

Routes to the correct handler based on doc_type:
    declarations_page / insurance_policy / renewal_notice / endorsement / ...
        → PolicyWorkflow
    commission_statement / commission_report
        → CommissionWorkflow
    loss_run / loss_run_report / claims_history
        → LossRunWorkflow
    insurance_claim / auto_insurance_claim / ...
        → ClaimWorkflow
    certificate_of_insurance
        → COIWorkflow
    (everything else)
        → skipped

Add to FinalizeNode in langgraph_agents.py (after document saved):

    from app.services.ams_workflow_service import AMSWorkflowService
    AMSWorkflowService(db).process(document, state)
"""

import json, logging, re, uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ROUTING TABLE  —  doc_type → workflow class
# Add new doc types here without touching any other code.
# ─────────────────────────────────────────────────────────────────────────────

_ROUTES: Dict[str, str] = {
    # ── Policy documents → PolicyWorkflow ────────────────────────────────────
    "declarations_page":        "policy",
    "insurance_policy":         "policy",
    "renewal_notice":           "policy",
    "nonrenewal_notice":        "policy",
    "cancellation_notice":      "policy",
    "endorsement":              "policy",
    "binder":                   "policy",
    "policy_schedule":          "policy",
    "policy_summary":           "policy",

    # ── Commission → CommissionWorkflow ───────────────────────────────────────
    "commission_statement":     "commission",
    "commission_report":        "commission",
    "producer_statement":       "commission",
    "carrier_statement":        "commission",

    # ── Loss run → LossRunWorkflow ────────────────────────────────────────────
    "loss_run":                 "loss_run",
    "loss_run_report":          "loss_run",
    "claims_history":           "loss_run",
    "loss_history":             "loss_run",

    # ── Claims → ClaimWorkflow ────────────────────────────────────────────────
    "insurance_claim":          "claim",
    "auto_insurance_claim":     "claim",
    "health_insurance_claim":   "claim",
    "claim_form":               "claim",
    "proof_of_loss":            "claim",
    "adjuster_report":          "claim",

    # ── COI → COIWorkflow ─────────────────────────────────────────────────────
    "certificate_of_insurance": "coi",
    "acord_25":                 "coi",
    "coi":                      "coi",
}

LOB_MAP = {
    "homeowners": "homeowners", "home owners": "homeowners",
    "dwelling":   "homeowners", "renters":     "homeowners",
    "condo":      "homeowners", "ho-":         "homeowners",
    "auto":       "auto",       "automobile":  "auto",
    "vehicle":    "auto",       "motor":       "auto",
    "commercial property":  "commercial_property",
    "commercial building":  "commercial_property",
    "general liability":    "general_liability",
    "gl ":                  "general_liability",
    "liability":            "general_liability",
    "professional liability":"general_liability",
    "e&o":                  "general_liability",
    "errors and omissions": "general_liability",
    "d&o":                  "general_liability",
    "bop":                  "bop",
    "business owner":       "bop",
    "businessowners":       "bop",
    "workers comp":         "workers_comp",
    "workers compensation": "workers_comp",
    "workcomp":             "workers_comp",
    "umbrella":             "umbrella",
    "excess liability":     "umbrella",
    "life":                 "life",
    "health":               "health",
    "medical":              "health",
    "landlord":             "landlord",
    "rental property":      "landlord",
    "flood":                "other",
    "earthquake":           "other",
    "cyber":                "other",
}


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SERVICE — router
# ═══════════════════════════════════════════════════════════════════════════════

class AMSWorkflowService:
    """
    Routes any document to the correct AMS workflow handler.
    Never raises — all errors are caught so main pipeline never breaks.
    """

    def __init__(self, db: Session):
        self.db = db

    def process(self, document: Any, state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            doc_id   = str(document.id)
            doc_type = _get_doc_type(document, state)
            route    = _ROUTES.get(doc_type)

            if not route:
                return {"skipped": True, "reason": f"no route for '{doc_type}'"}

            analysis = _get_analysis(state)
            fields   = analysis.get("fields") or {}

            logger.info(f"🔗 [AMS] doc={doc_id[:8]}… type={doc_type} → {route}")

            handler = {
                "policy":     PolicyWorkflow,
                "commission": CommissionWorkflow,
                "loss_run":   LossRunWorkflow,
                "claim":      ClaimWorkflow,
                "coi":        COIWorkflow,
            }.get(route)

            if not handler:
                return {"skipped": True, "reason": f"no handler for route '{route}'"}

            result = handler(self.db).run(doc_id, doc_type, fields, analysis)
            result["route"]    = route
            result["doc_type"] = doc_type

            logger.info(
                f"✅ [AMS] {route} → {result.get('action','?')} | "
                f"client={str(result.get('client_id') or '')[:8] or '—'} | "
                f"policy={str(result.get('policy_id') or '')[:8] or '—'}"
            )
            return result

        except Exception as e:
            logger.error(f"❌ [AMS] Workflow error (non-fatal): {e}", exc_info=True)
            return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# BASE WORKFLOW
# ═══════════════════════════════════════════════════════════════════════════════

class _BaseWorkflow:
    """Shared client/policy helpers used by all workflows."""

    def __init__(self, db: Session):
        self.db = db

    # ── Client ────────────────────────────────────────────────────────────────

    def find_or_create_client(
        self,
        named_insured: str,
        state_code: str = "",
        address:    str = "",
        email:      str = "",
        phone:      str = "",
        policy_type:str = "personal",
    ) -> Optional[str]:
        if not named_insured:
            return None

        first, last, business = _parse_name(named_insured)
        client_type = "commercial" if business else "personal"

        # Email is the most reliable dedup key
        if email:
            row = self.db.execute(text(
                "SELECT id FROM clients WHERE LOWER(email) = LOWER(:e) AND status != 'deleted'"
            ), {"e": email}).fetchone()
            if row:
                self._enrich_client(str(row.id), state_code, address, email, phone)
                return str(row.id)

        # Name-based search
        search = last or business or named_insured
        candidates = self.db.execute(text("""
            SELECT id, first_name, last_name, business_name, email
            FROM clients
            WHERE status != 'deleted'
              AND (
                LOWER(last_name)     LIKE LOWER(:q)
               OR LOWER(business_name) LIKE LOWER(:q)
               OR LOWER(first_name || ' ' || COALESCE(last_name,'')) LIKE LOWER(:full)
              )
            LIMIT 10
        """), {"q": f"%{search}%", "full": f"%{named_insured}%"}).fetchall()

        best_id, best_score = None, 0.0
        for c in candidates:
            score = _score_name(named_insured, first, last, business, c)
            if score > best_score:
                best_score, best_id = score, str(c.id)

        if best_id and best_score >= 0.60:
            logger.info(f"  matched client {best_id[:8]}… score={best_score:.2f}")
            self._enrich_client(best_id, state_code, address, email, phone)
            return best_id

        # Create new
        client_id = str(uuid.uuid4())
        city, zip_code = _parse_address_parts(address)
        # Extract street only — remove city/state/zip from address_line1
        street = address
        m = re.search(r'^(.*?)\s+[A-Za-z][A-Za-z\s]+?\s+[A-Z]{2}\s+\d{5}', address)
        if m:
            street = m.group(1).strip()
        self.db.execute(text("""
            INSERT INTO clients
                (id, first_name, last_name, business_name, client_type,
                 email, phone, state, zip, city, address_line1,
                 status, source, created_by, tenant_id)
            VALUES
                (:id, :first, :last, :biz, :ctype,
                 :email, :phone, :state, :zip, :city, :addr,
                 'active', 'document_ai', 'document_ai', :tenant_id)
        """), {
            "id": client_id, "first": first or named_insured,
            "last": last or "", "biz": business or "",
            "ctype": client_type, "email": email or None,
            "phone": phone or None, "state": state_code or None,
            "zip": zip_code or None, "city": city or None,
            "addr": _parse_street(address) or None,
            "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID,
        })
        logger.info(f"  created client {client_id[:8]}… '{named_insured}'")
        return client_id

    def find_client(self, named_insured: str) -> Optional[str]:
        """Find only — no creation."""
        if not named_insured:
            return None
        _, last, business = _parse_name(named_insured)
        search = last or business or named_insured
        row = self.db.execute(text("""
            SELECT id FROM clients
            WHERE LOWER(last_name) LIKE LOWER(:q)
               OR LOWER(business_name) LIKE LOWER(:q)
            LIMIT 1
        """), {"q": f"%{search}%"}).fetchone()
        return str(row.id) if row else None

    def _enrich_client(self, client_id, state_code, address, email, phone):
        """Fill missing fields on existing client."""
        parts, params = [], {"id": client_id}
        if state_code:
            parts.append("state = COALESCE(NULLIF(state,''), :state)")
            params["state"] = state_code
        if email:
            parts.append("email = COALESCE(NULLIF(email,''), :email)")
            params["email"] = email
        if phone:
            parts.append("phone = COALESCE(NULLIF(phone,''), :phone)")
            params["phone"] = phone
        if address:
            # Parse full address string if city/zip embedded
            # e.g. "3328 HARVEST GATE WAY FOLSOM CA  95630"
            import re as _re
            zip_match  = _re.search(r'\b(\d{5})(?:-\d{4})?\b', address)
            state_match= _re.search(r'\b([A-Z]{2})\s+\d{5}', address)
            # Extract zip
            zip_val = zip_match.group(1) if zip_match else None
            # Extract street — everything before the city
            # Try to find city by looking for STATE ZIP pattern
            street = address
            city_val = None
            m = _re.search(r'^(.*?)\s+([A-Z][A-Za-z\s]+?)\s+[A-Z]{2}\s+\d{5}', address)
            if m:
                street   = m.group(1).strip()
                city_val = m.group(2).strip()
            parts.append("address_line1 = COALESCE(NULLIF(address_line1,''), :addr)")
            params["addr"] = street
            if zip_val and "zip" not in params:
                parts.append("zip = COALESCE(NULLIF(zip,''), :zip)")
                params["zip"] = zip_val
            if city_val:
                parts.append("city = COALESCE(NULLIF(city,''), :city)")
                params["city"] = city_val
        if parts:
            self.db.execute(
                text(f"UPDATE clients SET {', '.join(parts)} WHERE id = :id"),
                params
            )

    # ── Policy ────────────────────────────────────────────────────────────────

    def find_or_create_policy(
        self,
        client_id: str,
        policy_number: str,
        lob: str,
        carrier_name: str,
        premium: float,
        effective_date: Optional[date],
        expiry_date: Optional[date],
        coverage_summary: Dict,
    ) -> Tuple[str, str]:

        # Exact policy number match
        if policy_number:
            row = self.db.execute(text(
                "SELECT id, client_id FROM policies WHERE policy_number = :pn"
            ), {"pn": policy_number}).fetchone()
            if row:
                pol_id = str(row.id)
                # If policy belongs to a deleted client, reassign to new client
                if client_id and str(row.client_id) != client_id:
                    old_client = self.db.execute(text(
                        "SELECT status FROM clients WHERE id = :cid"
                    ), {"cid": str(row.client_id)}).fetchone()
                    if old_client and old_client.status == 'deleted':
                        self.db.execute(text(
                            "UPDATE policies SET client_id = :cid WHERE id = :id"
                        ), {"cid": client_id, "id": pol_id})
                        self.db.execute(text(
                            "UPDATE documents SET client_id = :cid WHERE policy_id = :id"
                        ), {"cid": client_id, "id": pol_id})
                self._update_policy(pol_id, premium, effective_date,
                                    expiry_date, coverage_summary, carrier_name)
                return pol_id, "policy_updated"

        # Same client + carrier + LOB (renewal scenario)
        if client_id and carrier_name and lob:
            row = self.db.execute(text("""
                SELECT id FROM policies
                WHERE client_id = :cid
                  AND LOWER(carrier_name) LIKE LOWER(:carrier)
                  AND line_of_business = :lob
                  AND status NOT IN ('cancelled','non_renewed')
                ORDER BY created_at DESC LIMIT 1
            """), {
                "cid":     client_id,
                "carrier": f"%{carrier_name.split()[0]}%",
                "lob":     lob,
            }).fetchone()
            if row:
                self._update_policy(str(row.id), premium, effective_date,
                                    expiry_date, coverage_summary, carrier_name)
                if policy_number:
                    self.db.execute(text(
                        "UPDATE policies SET policy_number=:pn WHERE id=:id"
                    ), {"pn": policy_number, "id": str(row.id)})
                return str(row.id), "policy_updated_renewal"

        # Create new
        policy_id = str(uuid.uuid4())
        pol_num   = policy_number or f"PENDING-{policy_id[:8].upper()}"
        self.db.execute(text("""
            INSERT INTO policies
                (id, client_id, policy_number, line_of_business,
                 carrier_name, premium_amount, premium_freq,
                 effective_date, expiry_date, renewal_date,
                 coverage_summary, status, source, created_by, tenant_id)
            VALUES
                (:id, :cid, :pn, :lob,
                 :carrier, :premium, 'annual',
                 :eff, :exp, :exp,
                 CAST(:cov AS jsonb), 'active', 'document_ai', 'document_ai', :tenant_id)
        """), {
            "id": policy_id, "cid": client_id, "pn": pol_num,
            "lob": lob, "carrier": carrier_name or "Unknown",
            "premium": premium or None,
            "eff": effective_date, "exp": expiry_date,
            "cov": json.dumps(coverage_summary),
            "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID,
        })
        logger.info(f"  created policy {policy_id[:8]}… {pol_num} {lob}")
        return policy_id, "policy_created"

    def _update_policy(self, policy_id, premium, eff, exp, cov, carrier):
        parts, params = ["updated_at = NOW()"], {"id": policy_id}
        if premium > 0:
            parts.append("premium_amount = :premium"); params["premium"] = premium
        if eff:
            parts.append("effective_date = :eff"); params["eff"] = eff
        if exp:
            parts.append("expiry_date = :exp, renewal_date = :exp"); params["exp"] = exp
        if cov:
            parts.append("coverage_summary = CAST(:cov AS jsonb)")
            params["cov"] = json.dumps(cov)
        if carrier:
            parts.append("carrier_name = :carrier"); params["carrier"] = carrier
        self.db.execute(
            text(f"UPDATE policies SET {', '.join(parts)} WHERE id = :id"), params
        )

    def link_document(self, doc_id, client_id, policy_id=None):
        self.db.execute(text("""
            UPDATE documents
            SET client_id=:cid, policy_id=:pid,
                client_link_status='confirmed', client_link_score=1.0,
                client_linked_at=NOW(), client_linked_by='document_ai'
            WHERE id=:doc_id
        """), {"cid": client_id, "pid": policy_id, "doc_id": doc_id})

    def check_renewal(self, policy_id, client_id, expiry_date, policy_number):
        days = (expiry_date - date.today()).days
        if days > 90:
            return

        pol_status = "lapsed" if days < 0 else "renewal_due" if days <= 30 else "active"
        self.db.execute(text("""
            UPDATE policies
            SET status=:s, renewal_status='not_started'
            WHERE id=:id
        """), {"s": pol_status, "id": policy_id})

        existing = self.db.execute(text(
            "SELECT id FROM renewal_activities WHERE policy_id=:pid LIMIT 1"
        ), {"pid": policy_id}).fetchone()

        if not existing:
            urgency = "CRITICAL" if days <= 14 else "URGENT" if days <= 30 else "Upcoming"
            self.db.execute(text("""
                INSERT INTO renewal_activities
                    (id, policy_id, client_id, activity_type, title, created_by)
                VALUES (:id, :pid, :cid, 'note', :title, 'document_ai')
            """), {
                "id":    str(uuid.uuid4()),
                "pid":   policy_id,
                "cid":   client_id,
                "title": (
                    f"[{urgency}] {policy_number} expires in {days} days"
                    if days >= 0
                    else f"[LAPSED] {policy_number} expired {abs(days)} days ago"
                ),
            })


# ═══════════════════════════════════════════════════════════════════════════════
# WORKFLOW 1: PolicyWorkflow
# Handles: declarations_page, insurance_policy, renewal_notice,
#          nonrenewal_notice, cancellation_notice, endorsement, binder
# ═══════════════════════════════════════════════════════════════════════════════

class PolicyWorkflow(_BaseWorkflow):

    def run(self, doc_id, doc_type, fields, analysis) -> Dict:
        named_insured  = _c(fields.get("named_insured"))
        policy_number  = _c(fields.get("policy_number"))
        carrier_name   = _c(
            fields.get("carrier_name") or
            analysis.get("carrier_display") or
            analysis.get("carrier")
        )
        policy_type    = _c(fields.get("policy_type") or "homeowners").lower()
        effective_date = _pd(fields.get("effective_date"))
        expiry_date    = _pd(fields.get("expiration_date"))
        premium        = _f(fields.get("total_premium"))
        state_code     = _c(fields.get("state"))
        address        = _c(fields.get("address_line1") or fields.get("insured_address"))
        email          = _c(fields.get("email"))
        phone          = _c(fields.get("phone"))
        lob            = _map_lob(policy_type, fields)
        cov_summary    = _build_coverage_summary(fields, policy_type)

        if not named_insured and not policy_number:
            return {"skipped": True, "reason": "no name or policy number"}

        # Step 1: Client
        client_id = self.find_or_create_client(
            named_insured, state_code, address, email, phone, policy_type
        )
        if not client_id:
            return {"skipped": True, "reason": "could not resolve client"}

        # Step 2: Policy
        policy_id, action = self.find_or_create_policy(
            client_id, policy_number, lob, carrier_name,
            premium, effective_date, expiry_date, cov_summary
        )

        # Step 3: Doc-type specific adjustments
        if doc_type == "cancellation_notice":
            self.db.execute(text(
                "UPDATE policies SET status='cancelled' WHERE id=:id"
            ), {"id": policy_id})

        elif doc_type == "nonrenewal_notice":
            self.db.execute(text(
                "UPDATE policies SET status='non_renewed', renewal_status='non_renewed' WHERE id=:id"
            ), {"id": policy_id})

        elif doc_type == "endorsement" and cov_summary:
            # Merge new coverage into existing — don't overwrite everything
            self.db.execute(text("""
                UPDATE policies
                SET coverage_summary = coverage_summary || CAST(:cov AS jsonb),
                    updated_at = NOW()
                WHERE id = :id
            """), {"cov": json.dumps(cov_summary), "id": policy_id})

        elif doc_type == "renewal_notice" and expiry_date:
            # Renewal notice: update dates, keep rest
            self.db.execute(text("""
                UPDATE policies
                SET expiry_date=:exp, renewal_date=:exp,
                    status='active', renewal_status='not_started'
                WHERE id=:id
            """), {"exp": expiry_date, "id": policy_id})

        # Step 4: Link document
        self.link_document(doc_id, client_id, policy_id)

        # Step 5: Renewal pipeline
        if expiry_date:
            self.check_renewal(policy_id, client_id, expiry_date, policy_number)

        # Step 6: Flag gap analysis for refresh if new policy added
        if action == "policy_created":
            self.db.execute(text("""
                UPDATE gap_analyses
                SET summary = COALESCE(summary,'') || ' [New policy added — re-run recommended]'
                WHERE client_id=:cid AND analyzed_at < NOW() - INTERVAL '7 days'
            """), {"cid": client_id})

        self.db.commit()
        return {
            "action":    action,
            "client_id": client_id,
            "policy_id": policy_id,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# WORKFLOW 2: CommissionWorkflow
# Handles: commission_statement, commission_report, producer_statement
# ═══════════════════════════════════════════════════════════════════════════════

class CommissionWorkflow(_BaseWorkflow):

    def run(self, doc_id, doc_type, fields, analysis) -> Dict:
        carrier   = _c(fields.get("carrier_name") or analysis.get("carrier") or "Unknown")
        stmt_date = _pd(fields.get("statement_date") or fields.get("effective_date"))
        total_amt = _f(fields.get("total_commission") or fields.get("total_premium"))

        stmt_id = str(uuid.uuid4())
        self.db.execute(text("""
            INSERT INTO commission_statements
                (id, carrier_name, statement_date, document_id, total_amount, status)
            VALUES (:id, :carrier, :date, :doc_id, :total, 'pending')
            ON CONFLICT DO NOTHING
        """), {
            "id":     stmt_id, "carrier": carrier,
            "date":   stmt_date, "doc_id": doc_id,
            "total":  total_amt or 0,
        })

        self.db.execute(text("""
            UPDATE documents SET client_link_status='confirmed' WHERE id=:doc_id
        """), {"doc_id": doc_id})

        self.db.commit()
        return {"action": "commission_statement_created", "statement_id": stmt_id}


# ═══════════════════════════════════════════════════════════════════════════════
# WORKFLOW 3: LossRunWorkflow
# Handles: loss_run, loss_run_report, claims_history
# ═══════════════════════════════════════════════════════════════════════════════

class LossRunWorkflow(_BaseWorkflow):

    def run(self, doc_id, doc_type, fields, analysis) -> Dict:
        named_insured  = _c(fields.get("named_insured"))
        carrier        = _c(fields.get("carrier_name") or analysis.get("carrier") or "Unknown")
        loss_ratio     = _f(fields.get("loss_ratio"))
        total_incurred = _f(fields.get("total_incurred"))
        total_claims   = int(_f(fields.get("claim_count") or fields.get("total_claims")))
        period_start   = _pd(fields.get("effective_date"))
        period_end     = _pd(fields.get("expiration_date"))

        client_id = self.find_client(named_insured) if named_insured else None
        if not client_id:
            return {"skipped": True, "reason": "no client match for loss run"}

        risk_level = (
            "critical" if loss_ratio >= 100 or total_claims >= 10 else
            "high"     if loss_ratio >= 80  or total_claims >= 8  else
            "medium"   if loss_ratio >= 60  or total_claims >= 5  else
            "low"
        )

        run_id = str(uuid.uuid4())
        self.db.execute(text("""
            INSERT INTO loss_runs
                (id, client_id, carrier_name, period_start, period_end,
                 total_claims, total_incurred, loss_ratio, risk_level)
            VALUES
                (:id, :cid, :carrier, :start, :end,
                 :claims, :incurred, :ratio, :risk)
        """), {
            "id": run_id, "cid": client_id, "carrier": carrier,
            "start": period_start, "end": period_end,
            "claims": total_claims, "incurred": total_incurred,
            "ratio": loss_ratio, "risk": risk_level,
        })

        # Update risk scores on active policies for this client
        if risk_level in ("high", "critical"):
            self.db.execute(text("""
                UPDATE policies
                SET risk_score = :score,
                    risk_flags = array_append(
                        COALESCE(risk_flags, ARRAY[]::text[]),
                        :flag
                    )
                WHERE client_id=:cid AND status='active'
            """), {
                "score": min(1.0, loss_ratio / 100),
                "flag":  f"{risk_level}_loss_ratio_{int(loss_ratio)}pct",
                "cid":   client_id,
            })

        self.link_document(doc_id, client_id)
        self.db.commit()
        return {
            "action":     "loss_run_created",
            "run_id":     run_id,
            "client_id":  client_id,
            "risk_level": risk_level,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# WORKFLOW 4: ClaimWorkflow
# Handles: insurance_claim, auto_insurance_claim, health_insurance_claim
# ═══════════════════════════════════════════════════════════════════════════════

class ClaimWorkflow(_BaseWorkflow):

    def run(self, doc_id, doc_type, fields, analysis) -> Dict:
        named_insured = _c(fields.get("named_insured"))
        policy_number = _c(fields.get("policy_number"))

        client_id = policy_id = None

        # Find via policy number first (most reliable)
        if policy_number:
            row = self.db.execute(text(
                "SELECT id, client_id FROM policies WHERE policy_number=:pn"
            ), {"pn": policy_number}).fetchone()
            if row:
                policy_id = str(row.id)
                client_id = str(row.client_id)

        # Fallback: name match
        if not client_id and named_insured:
            client_id = self.find_client(named_insured)

        if client_id:
            self.link_document(doc_id, client_id, policy_id)
            self.db.commit()

        return {
            "action":    "claim_linked",
            "client_id": client_id,
            "policy_id": policy_id,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# WORKFLOW 5: COIWorkflow
# Handles: certificate_of_insurance, acord_25
# ═══════════════════════════════════════════════════════════════════════════════

class COIWorkflow(_BaseWorkflow):

    def run(self, doc_id, doc_type, fields, analysis) -> Dict:
        named_insured = _c(fields.get("named_insured"))
        policy_number = _c(fields.get("policy_number"))
        holder_name   = _c(fields.get("certificate_holder") or fields.get("holder_name") or "")
        expiry_date   = _pd(fields.get("expiration_date"))

        client_id = self.find_client(named_insured) if named_insured else None

        policy_id = None
        if policy_number:
            row = self.db.execute(text(
                "SELECT id FROM policies WHERE policy_number=:pn"
            ), {"pn": policy_number}).fetchone()
            if row:
                policy_id = str(row.id)

        if client_id:
            self.link_document(doc_id, client_id, policy_id)

            # Record the COI in coi_certificates if holder known
            if holder_name and policy_id:
                # Create a COI request record
                req_id = str(uuid.uuid4())
                self.db.execute(text("""
                    INSERT INTO coi_requests
                        (id, client_id, holder_name, policy_ids, status,
                         requested_by)
                    VALUES
                        (:id, :cid, :holder, :pids, 'completed',
                         'document_ai')
                    ON CONFLICT DO NOTHING
                """), {
                    "id":     req_id,
                    "cid":    client_id,
                    "holder": holder_name,
                    "pids":   [policy_id] if policy_id else [],
                })

            self.db.commit()

        return {
            "action":    "coi_linked",
            "client_id": client_id,
            "policy_id": policy_id,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PURE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_doc_type(document: Any, state: Dict) -> str:
    return _c(
        getattr(document, "doc_type", None) or
        state.get("doc_type") or
        state.get("document_type") or
        (state.get("analysis_result") or {}).get("type") or
        ""
    ).lower()


def _get_analysis(state: Dict) -> Dict:
    return (
        state.get("analysis_result") or
        state.get("analysis_results") or
        state.get("specialist_result") or
        {}
    )


def _parse_name(named_insured: str) -> Tuple[str, str, str]:
    """Returns (first, last, business)."""
    if not named_insured:
        return "", "", ""
    name = re.sub(r"\s+(?:AND|&)\s+.*$", "", named_insured, flags=re.I).strip()
    name = re.sub(r"^(?:DR\.?|MR\.?|MRS\.?|MS\.?|PROF\.?)\s+", "", name, flags=re.I).strip()

    BIZ = ["LLC","INC","CORP","CO.","LTD","COMPANY","ENTERPRISES","SERVICES",
           "GROUP","ASSOCIATES","SOLUTIONS","PARTNERS","CONSULTING","CONSTRUCTION",
           "HOLDINGS","PROPERTIES","MANAGEMENT","SYSTEMS","TECHNOLOGIES","INDUSTRIES"]
    if any(b in name.upper() for b in BIZ):
        return "", "", name.title()

    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        rest  = parts[1].split() if len(parts) > 1 else []
        return (rest[0].title() if rest else ""), parts[0].title(), ""

    # FIXED — skip middle initials (single letters)
    parts = name.split()
    if len(parts) == 1:
        return parts[0].title(), "", ""
    if len(parts) == 2:
        return parts[0].title(), parts[1].title(), ""
    # 3+ parts — first + last, skip middle initials/names
    first = parts[0].title()
    last = parts[-1].title()
    # If last part looks like initial (single letter), take second-to-last
    if len(last) == 1 or (len(last) == 2 and last.endswith('.')):
        last = parts[-2].title()
    return first, last, ""


def _score_name(insured, first, last, business, c) -> float:
    score = 0.0
    il = insured.lower()
    cl = (c.last_name or "").lower()
    cf = (c.first_name or "").lower()
    cb = (c.business_name or "").lower()
    if cl and last and cl == last.lower():   score += 0.50
    elif cl and cl in il:                    score += 0.30
    if cf and first and cf == first.lower(): score += 0.30
    elif cf and cf in il:                    score += 0.15
    if cb and business:
        b1 = set(cb.split()) - {"llc","inc","corp","co","ltd","the","and","of"}
        b2 = set(business.lower().split()) - {"llc","inc","corp","co","ltd","the","and","of"}
        if b1 and b2:
            score += 0.60 * len(b1 & b2) / max(len(b1), len(b2))
    return min(score, 1.0)


def _map_lob(policy_type: str, fields: Dict) -> str:
    t = policy_type.lower()
    for k, v in LOB_MAP.items():
        if k in t:
            return v
    if fields.get("coverage_a_dwelling") or fields.get("coverage_a"):
        return "homeowners"
    if fields.get("vin"):
        return "auto"
    return "other"


def _build_coverage_summary(fields: Dict, policy_type: str) -> Dict:
    summary = {}
    if policy_type == "auto":
        mapping = [
            ("bodily_injury_per_person",      "bodily_injury_pp"),
            ("bodily_injury_per_occurrence",  "bodily_injury_po"),
            ("property_damage_liability",     "property_damage"),
            ("collision_deductible",          "collision_ded"),
            ("comprehensive_deductible",      "comprehensive_ded"),
            ("uninsured_motorist_per_person", "uninsured_pp"),
        ]
    else:
        mapping = [
            ("coverage_a_dwelling",           "dwelling"),
            ("coverage_b_other_structures",   "other_structures"),
            ("coverage_c_personal_property",  "personal_property"),
            ("coverage_d_loss_of_use",        "loss_of_use"),
            ("coverage_e_personal_liability", "liability"),
            ("coverage_f_med_pay",            "med_pay"),
            ("deductible",                    "deductible"),
        ]
    for fk, sk in mapping:
        v = _f(fields.get(fk))
        if v > 0:
            summary[sk] = v
    return summary


def _parse_address_parts(address: str) -> Tuple[str, str]:
    """
    Returns (city, zip) from address string.
    Handles: '3328 HARVEST GATE WAY FOLSOM CA 95630'
    Returns: ('FOLSOM', '95630')
    Street is everything before the city — caller must extract separately.
    """
    if not address:
        return "", ""
    # Pattern: STREET CITY STATE ZIP
    # State is 2 uppercase letters followed by 5-digit zip
    m = re.search(r'\b([A-Z][A-Z\s]{1,20}?)\s+([A-Z]{2})\s+(\d{5})\b', address)
    if m:
        city = m.group(1).strip()
        zip_ = m.group(3)
        return city, zip_
    return "", ""


def _parse_street(address: str) -> str:
    """Extract street portion only — strip city/state/zip."""
    if not address:
        return ""
    # Remove trailing CITY STATE ZIP
    street = re.sub(r'\s+[A-Z][A-Z\s]{1,20}\s+[A-Z]{2}\s+\d{5}.*$', '', address).strip()
    return street


# Short aliases used inside workflow methods
def _c(v) -> str:
    if not v: return ""
    return re.sub(r"\s+", " ", str(v).replace("&amp;","&")).strip()

def _f(v) -> float:
    if v is None: return 0.0
    try:
        s = re.sub(r"[$,\s]","",str(v))
        s = re.sub(r"[^\d.\-].*$","",s)
        return float(s) if s else 0.0
    except: return 0.0

def _pd(v) -> Optional[date]:
    if not v: return None
    s = str(v).strip()
    for fmt in ("%m/%d/%Y","%m-%d-%Y","%m/%d/%y",
                "%B %d, %Y","%B %d %Y","%b %d, %Y","%Y-%m-%d"):
        try: return datetime.strptime(s, fmt).date()
        except: continue
    return None