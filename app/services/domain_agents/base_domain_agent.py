"""
base_domain_agent.py
────────────────────
Base class for all feature-flag-driven scheduled domain agents.
Each domain agent collects data, builds a structured report, and stores it
in agent_reports + agent_runs.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)


class BaseDomainAgent:
    agent_type: str = "base"
    task_name: str = "Domain Report"

    def __init__(self, db: Session):
        self.db = db

    def collect(self) -> dict[str, Any]:
        """Override in each domain agent — return raw data dict."""
        raise NotImplementedError

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        """Override to transform raw data into structured report sections."""
        raise NotImplementedError

    def run(self) -> str:
        """Execute collect → build_report → persist. Returns run_id."""
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        # Create agent_run record
        self.db.execute(text("""
            INSERT INTO agent_runs
                (id, sector, trigger_type, task_name, status, started_at, updated_at, tenant_id)
            VALUES
                (:id, :sector, 'scheduled', :task, 'thinking', :started, :started, :tenant_id)
        """), {"id": run_id, "sector": self.agent_type,
               "task": self.task_name, "started": started_at,
               "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID})
        self.db.commit()

        try:
            logger.info(f"🤖 [{self.agent_type}] Starting scheduled run {run_id[:8]}")

            data = self.collect()
            report = self.build_report(data)

            report_id = str(uuid.uuid4())
            self.db.execute(text("""
                INSERT INTO agent_reports
                    (id, run_id, agent_type, report_date, title, summary, body)
                VALUES
                    (:id, :run_id, :agent_type, CURRENT_DATE, :title, :summary, :body::jsonb)
            """), {
                "id": report_id,
                "run_id": run_id,
                "agent_type": self.agent_type,
                "title": report.get("title", self.task_name),
                "summary": report.get("summary", ""),
                "body": __import__("json").dumps(report),
            })

            self.db.execute(text("""
                UPDATE agent_runs
                SET status = 'completed',
                    outcome = :outcome::jsonb,
                    ended_at = :now,
                    updated_at = :now
                WHERE id = :id
            """), {
                "id": run_id,
                "outcome": __import__("json").dumps({"report_id": report_id, "summary": report.get("summary", "")}),
                "now": datetime.now(timezone.utc),
            })
            self.db.commit()

            logger.info(f"✅ [{self.agent_type}] Run {run_id[:8]} complete — report {report_id[:8]}")
            return run_id

        except Exception as e:
            logger.error(f"❌ [{self.agent_type}] Run {run_id[:8]} failed: {e}")
            self.db.execute(text("""
                UPDATE agent_runs
                SET status = 'failed', error_message = :err,
                    ended_at = :now, updated_at = :now
                WHERE id = :id
            """), {"id": run_id, "err": str(e), "now": datetime.now(timezone.utc)})
            self.db.commit()
            raise
