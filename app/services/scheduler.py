"""
scheduler.py
============
Background schedulers for UniDocVerse.
All scheduled jobs live here — main.py just calls scheduler.start().
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


async def daily_renewal_notifier():
    """Sends renewal email reminders daily at 8 AM."""
    while True:
        now    = datetime.now()
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        logger.info(f"📅 Next renewal check in {wait/3600:.1f}h")
        await asyncio.sleep(wait)
        await _run_renewal_notifications()


async def _run_renewal_notifications():
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.services.renewal_notification_service import (
        RenewalNotificationService, RenewalEmailSender
    )
    try:
        db   = SessionLocal()
        rows = db.execute(text(
            "SELECT key, value FROM settings WHERE key IN ("
            "'smtp_host','smtp_port','smtp_user','smtp_pass',"
            "'smtp_from_name','renewal_notify_enabled')"
        )).fetchall()
        cfg  = {r.key: r.value for r in rows}

        smtp_user = os.getenv("RENEWAL_SMTP_USER") or cfg.get("smtp_user")
        smtp_pass = os.getenv("RENEWAL_SMTP_PASS") or cfg.get("smtp_pass")
        smtp_host = cfg.get("smtp_host") or "smtp.gmail.com"
        smtp_port = int(cfg.get("smtp_port") or 465)
        from_name = cfg.get("smtp_from_name") or "UniDocVerse"
        enabled   = cfg.get("renewal_notify_enabled", "true").lower() == "true"

        if not enabled:
            logger.info("⏭ Renewal emails disabled in Agency Settings")
            return

        if smtp_user and smtp_pass:
            sender = RenewalEmailSender(smtp_host, smtp_port,
                                        smtp_user, smtp_pass, from_name)
            svc    = RenewalNotificationService(db, sender)
            result = svc.run()
            logger.info(f"📧 Renewal notifications: {result}")
        else:
            logger.info("⏭ Renewal emails skipped — SMTP not configured")
        db.close()
    except Exception as e:
        logger.error(f"❌ Renewal notifier failed: {e}")


async def daily_immigration_scanner():
    """Runs once daily at 8:05 AM — scans immigration expiry dates and logs alerts."""
    while True:
        now    = datetime.now()
        target = now.replace(hour=8, minute=5, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        await _run_immigration_scan()


async def _run_immigration_scan():
    from app.core.database import SessionLocal
    from app.services.immigration_service import scan_expiry_alerts
    db = SessionLocal()
    try:
        alerts = scan_expiry_alerts(db)
        for a in alerts:
            logger.warning(
                f"🛂 Immigration alert: {a['message']} "
                f"[client={a['client_id']} days={a['days_left']}]"
            )
        if not alerts:
            logger.info("🛂 Immigration scan: no expiry alerts today")
    except Exception as e:
        logger.error(f"❌ Immigration scanner failed: {e}")
    finally:
        db.close()


async def _run_single_agent(sid: str, agent_type: str, task_name: str, tenant_id: str = None):
    """Run one domain agent for one organisation in its own DB session and update last_run_at."""
    if tenant_id:
        from app.core.tenant import set_current_tenant_id
        set_current_tenant_id(tenant_id)
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.services.domain_agents.registry import get_agent_class

    agent_cls = get_agent_class(agent_type)
    if not agent_cls:
        logger.warning(f"⚠️ [Scheduler] No agent class for type '{agent_type}'")
        return

    db = SessionLocal()
    try:
        logger.info(f"🚀 [Scheduler] Starting {task_name}")
        agent  = agent_cls(db)
        run_id = agent.run()
        db.execute(text("""
            UPDATE scheduled_agents
            SET last_run_at = NOW(), last_run_id = :run_id, updated_at = NOW()
            WHERE id = :id
        """), {"run_id": run_id, "id": sid})
        db.commit()
        logger.info(f"✅ [Scheduler] {task_name} done")
    except Exception as e:
        logger.error(f"❌ [Scheduler] {task_name} failed: {e}")
    finally:
        db.close()


async def domain_agent_scheduler():
    """
    Polls every minute. At the scheduled hour:minute, fires ALL enabled agents
    concurrently (asyncio.gather) so every domain report is generated at the same time.
    """
    while True:
        await asyncio.sleep(60)
        try:
            from sqlalchemy import text
            from app.core.database import SessionLocal

            now = datetime.now()
            db  = SessionLocal()

            rows = db.execute(text("""
                SELECT id, agent_type, task_name, cron_hour, cron_minute, last_run_at
                FROM scheduled_agents
                WHERE enabled = TRUE
            """)).fetchall()
            db.close()

            # Collect all agents due right now that haven't run today
            due = [
                (str(r[0]), r[1], r[2])
                for r in rows
                if now.hour == r[3]
                and now.minute == r[4]
                and (r[5] is None or r[5].date() < now.date())
            ]

            if not due:
                continue

            # Each organisation gets the reports for the modules it has switched on — nothing else
            from app.api.features_router import enabled_domains
            db = SessionLocal()
            try:
                tenants = [r[0] for r in db.execute(text("SELECT id::text FROM tenants WHERE is_active"))]
                jobs = [(sid, agent_type, task_name, tid) for tid in tenants
                        for sid, agent_type, task_name in due if agent_type in enabled_domains(db, tid)]
            finally:
                db.close()
            logger.info(f"🚀 [Scheduler] Firing {len(jobs)} domain reports across {len(tenants)} organisations")
            await asyncio.gather(*[_run_single_agent(*job) for job in jobs])

        except Exception as e:
            logger.error(f"❌ [domain_agent_scheduler] error: {e}")


async def daily_morning_brief():
    """Builds and delivers every organisation's brief once a day at 07:00."""
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        if now.hour == 7 and now.minute == 0:
            from app.services.morning_brief import build_all
            await asyncio.to_thread(build_all)


def start(loop=None):
    """Start all background schedulers. Called once from main.py lifespan."""
    asyncio.create_task(daily_renewal_notifier())
    asyncio.create_task(daily_immigration_scanner())
    asyncio.create_task(domain_agent_scheduler())
    asyncio.create_task(daily_morning_brief())
    logger.info("✅ Schedulers started")
