"""
insurance_agent.py
──────────────────
Daily intelligence report for insurance agency users.

Sections (in priority order):
  1. Book snapshot       — active policies, lapsed count, annualized premium, premium at risk
  2. Lapsed policies     — list with client, carrier, LOB, premium, days lapsed (urgent)
  3. Stalled renewals    — expiring ≤90 days with renewal_status = 'not_started'
  4. Renewal pipeline    — expiring policies with client details + renewal status
  5. Quote follow-ups    — open quotes by stage with client names
  6. COI expirations     — certificates expiring with client names
"""

import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

FREQ_MULTIPLIER = {"annual": 1, "semi_annual": 2, "quarterly": 4, "monthly": 12}


def _annualize(amount, freq):
    if not amount:
        return 0
    try:
        return round(float(amount) * FREQ_MULTIPLIER.get(freq or "annual", 1), 2)
    except Exception:
        return 0


class InsuranceAgent(BaseDomainAgent):
    agent_type = "insurance"
    task_name  = "Insurance Agency Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Book snapshot ─────────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'active')                          AS active_count,
                    COUNT(*) FILTER (WHERE status IN ('lapsed','cancelled','expired'))  AS lapsed_count,
                    COUNT(*)                                                             AS total_count,
                    SUM(CASE WHEN status = 'active' THEN
                        premium_amount * CASE premium_freq
                            WHEN 'annual'      THEN 1
                            WHEN 'semi_annual' THEN 2
                            WHEN 'quarterly'   THEN 4
                            WHEN 'monthly'     THEN 12
                            ELSE 1 END
                    ELSE 0 END)                                                         AS active_premium,
                    SUM(CASE WHEN status IN ('lapsed','cancelled','expired') THEN
                        premium_amount * CASE premium_freq
                            WHEN 'annual'      THEN 1
                            WHEN 'semi_annual' THEN 2
                            WHEN 'quarterly'   THEN 4
                            WHEN 'monthly'     THEN 12
                            ELSE 1 END
                    ELSE 0 END)                                                         AS at_risk_premium
                FROM (SELECT * FROM policies WHERE tenant_id::text = :tenant_id) policies
            """), self.tp).fetchone()
            data["book"] = {
                "active_policies":    int(rows[0] or 0),
                "lapsed_policies":    int(rows[1] or 0),
                "total_policies":     int(rows[2] or 0),
                "active_premium_yr":  round(float(rows[3] or 0), 2),
                "at_risk_premium_yr": round(float(rows[4] or 0), 2),
            }
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] book snapshot failed: {e}")
            db.rollback()
            data["book"] = {}

        # ── 2. Lapsed policies (need immediate outreach) ──────────────────────
        try:
            rows = db.execute(text("""
                SELECT
                    TRIM(c.first_name || ' ' || COALESCE(c.last_name,'')) AS client,
                    c.client_number,
                    COALESCE(c.phone, c.mobile, c.email, '—')             AS contact,
                    p.policy_number,
                    p.line_of_business,
                    p.carrier_name,
                    p.premium_amount,
                    p.premium_freq,
                    p.expiry_date,
                    CURRENT_DATE - p.expiry_date                          AS days_lapsed
                FROM (SELECT * FROM policies WHERE tenant_id::text = :tenant_id) p
                JOIN (SELECT * FROM clients WHERE tenant_id::text = :tenant_id) c ON c.id = p.client_id
                WHERE p.status IN ('lapsed','cancelled','expired')
                ORDER BY p.expiry_date DESC
            """), self.tp).fetchall()
            data["lapsed"] = [{
                "client":         r[0],
                "client_number":  r[1],
                "contact":        r[2],
                "policy_number":  r[3],
                "lob":            r[4],
                "carrier":        r[5],
                "annual_premium": _annualize(r[6], r[7]),
                "expired_on":     r[8].isoformat() if r[8] else "—",
                "days_lapsed":    int(r[9] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] lapsed query failed: {e}")
            db.rollback()
            data["lapsed"] = []

        # ── 3. Stalled renewals (expiring ≤90d, renewal not started) ─────────
        try:
            rows = db.execute(text("""
                SELECT
                    TRIM(c.first_name || ' ' || COALESCE(c.last_name,'')) AS client,
                    c.client_number,
                    COALESCE(c.phone, c.mobile, c.email, '—')             AS contact,
                    p.policy_number,
                    p.line_of_business,
                    p.carrier_name,
                    p.premium_amount,
                    p.premium_freq,
                    p.expiry_date,
                    p.expiry_date - CURRENT_DATE                          AS days_left,
                    p.renewal_status
                FROM (SELECT * FROM policies WHERE tenant_id::text = :tenant_id) p
                JOIN (SELECT * FROM clients WHERE tenant_id::text = :tenant_id) c ON c.id = p.client_id
                WHERE p.status = 'active'
                  AND p.expiry_date <= CURRENT_DATE + 90
                  AND p.renewal_status = 'not_started'
                ORDER BY p.expiry_date
            """), self.tp).fetchall()
            data["stalled_renewals"] = [{
                "client":         r[0],
                "client_number":  r[1],
                "contact":        r[2],
                "policy_number":  r[3],
                "lob":            r[4],
                "carrier":        r[5],
                "annual_premium": _annualize(r[6], r[7]),
                "expires_on":     r[8].isoformat() if r[8] else "—",
                "days_left":      int(r[9] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] stalled renewals query failed: {e}")
            db.rollback()
            data["stalled_renewals"] = []

        # ── 4. Full renewal pipeline (active, expiring ≤90d) ─────────────────
        try:
            rows = db.execute(text("""
                SELECT
                    TRIM(c.first_name || ' ' || COALESCE(c.last_name,'')) AS client,
                    c.client_number,
                    p.policy_number,
                    p.line_of_business,
                    p.carrier_name,
                    p.premium_amount,
                    p.premium_freq,
                    p.expiry_date,
                    p.expiry_date - CURRENT_DATE                          AS days_left,
                    p.renewal_status
                FROM (SELECT * FROM policies WHERE tenant_id::text = :tenant_id) p
                JOIN (SELECT * FROM clients WHERE tenant_id::text = :tenant_id) c ON c.id = p.client_id
                WHERE p.status = 'active'
                  AND p.expiry_date <= CURRENT_DATE + 90
                ORDER BY p.expiry_date
            """), self.tp).fetchall()
            data["renewals"] = [{
                "client":         r[0],
                "client_number":  r[1],
                "policy_number":  r[2],
                "lob":            r[3],
                "carrier":        r[4],
                "annual_premium": _annualize(r[5], r[6]),
                "expires_on":     r[7].isoformat() if r[7] else "—",
                "days_left":      int(r[8] or 0),
                "renewal_status": r[9],
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] renewals query failed: {e}")
            db.rollback()
            data["renewals"] = []

        # ── 5. Quote follow-ups ───────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT
                    COALESCE(q.client_name,
                        TRIM(c.first_name || ' ' || COALESCE(c.last_name,'')),
                        '—')                              AS client,
                    q.quote_number,
                    q.lob,
                    q.stage,
                    q.created_at::date                    AS created_date,
                    CURRENT_DATE - q.created_at::date     AS age_days
                FROM (SELECT * FROM quotes WHERE tenant_id::text = :tenant_id) q
                LEFT JOIN (SELECT * FROM clients WHERE tenant_id::text = :tenant_id) c ON c.id = q.client_id
                WHERE q.stage NOT IN ('bound','lost','declined')
                ORDER BY q.created_at DESC
            """), self.tp).fetchall()
            data["quotes"] = [{
                "client":       r[0],
                "quote_number": r[1],
                "lob":          r[2],
                "stage":        r[3],
                "created":      r[4].isoformat() if r[4] else "—",
                "age_days":     int(r[5] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] quotes query failed: {e}")
            db.rollback()
            data["quotes"] = []

        # ── 6. COI expirations with client names ─────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT
                    TRIM(c.first_name || ' ' || COALESCE(c.last_name,'')) AS client,
                    c.client_number,
                    cc.certificate_number,
                    cc.valid_until,
                    cc.valid_until - CURRENT_DATE AS days_left
                FROM (SELECT * FROM coi_certificates WHERE client_id IN (SELECT id FROM clients WHERE tenant_id::text = :tenant_id)) cc
                JOIN (SELECT * FROM clients WHERE tenant_id::text = :tenant_id) c ON c.id = cc.client_id
                WHERE cc.valid_until IS NOT NULL
                  AND cc.valid_until >= CURRENT_DATE
                  AND cc.valid_until <= CURRENT_DATE + 60
                ORDER BY cc.valid_until
            """), self.tp).fetchall()
            data["coi_expirations"] = [{
                "client":      r[0],
                "client_number": r[1],
                "cert_number": r[2],
                "expires_on":  r[3].isoformat() if r[3] else "—",
                "days_left":   int(r[4] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] COI query failed: {e}")
            db.rollback()
            data["coi_expirations"] = []

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today         = date.today().isoformat()
        book          = data.get("book", {})
        lapsed        = data.get("lapsed", [])
        stalled       = data.get("stalled_renewals", [])
        renewals      = data.get("renewals", [])
        quotes        = data.get("quotes", [])
        coi           = data.get("coi_expirations", [])

        at_risk = book.get("at_risk_premium_yr", 0)
        stalled_count = len(stalled)
        lapsed_count  = len(lapsed)

        summary = (
            f"{lapsed_count} lapsed {'policy' if lapsed_count == 1 else 'policies'} "
            f"(${at_risk:,.0f}/yr at risk) | "
            f"{stalled_count} renewals expiring ≤90d with no action | "
            f"{len(quotes)} open quotes"
        )

        return {
            "title":   f"Insurance Agency Report — {today}",
            "summary": summary,
            "sections": {
                "book_of_business": book,
                "lapsed_policies_need_outreach": lapsed,
                "stalled_renewals_no_action_yet": stalled,
                "renewal_pipeline_next_90_days": renewals,
                "open_quotes": quotes,
                "coi_expiring_60_days": coi,
            },
        }
