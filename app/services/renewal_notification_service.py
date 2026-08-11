"""
renewal_notification_service.py
================================
Sends renewal email reminders and creates Google Calendar events.

Features:
- Single SMTP connection for bulk sends (efficient)
- 90/30/14/7 day thresholds
- Dedup — never sends twice per threshold
- .ics calendar files saved locally
- Activity logged in renewal_activities table
"""

import logging
import smtplib
import uuid
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL TEMPLATE
# ─────────────────────────────────────────────────────────────────────────────

def _build_renewal_email(client_name: str, policy_number: str,
                          carrier: str, expiry_date: str,
                          days: int, agent_name: str = "Your Agent") -> tuple:
    """Returns (subject, html_body)"""
    urgency_word = "URGENT: " if days <= 14 else ""
    subject = f"{urgency_word}Your {carrier} policy expires in {days} days — {policy_number}"

    html = f"""
<div style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto;color:#0f172a">
  <div style="background:#4f46e5;padding:20px 24px;border-radius:12px 12px 0 0">
    <h2 style="color:white;margin:0;font-size:20px">Policy Renewal Reminder</h2>
  </div>
  <div style="background:#f8fafc;padding:24px;border-radius:0 0 12px 12px;border:1px solid #e2e8f0">
    <p style="font-size:15px">Dear {client_name},</p>
    <p>Your insurance policy is coming up for renewal. Here are the details:</p>
    <div style="background:white;border-radius:8px;padding:16px;margin:16px 0;border:1px solid #e2e8f0">
      <table style="width:100%;font-size:14px">
        <tr><td style="color:#64748b;padding:4px 0">Policy Number</td><td style="font-weight:600">{policy_number}</td></tr>
        <tr><td style="color:#64748b;padding:4px 0">Carrier</td><td style="font-weight:600">{carrier}</td></tr>
        <tr><td style="color:#64748b;padding:4px 0">Expiry Date</td><td style="font-weight:600;color:{'#dc2626' if days <= 14 else '#d97706'}">{expiry_date}</td></tr>
        <tr><td style="color:#64748b;padding:4px 0">Days Remaining</td><td style="font-weight:700;color:{'#dc2626' if days <= 14 else '#d97706'}">{days} days</td></tr>
      </table>
    </div>
    <p>Please contact us to review your renewal options.</p>
    <p style="margin-top:24px">Best regards,<br><strong>{agent_name}</strong></p>
    <hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0">
    <p style="font-size:11px;color:#94a3b8">
      Automated reminder from UniDocVerse. Your data is stored locally.
    </p>
  </div>
</div>
"""
    return subject, html


# ─────────────────────────────────────────────────────────────────────────────
# SMTP SENDER
# ─────────────────────────────────────────────────────────────────────────────

class RenewalEmailSender:
    """
    SMTP email sender with two modes:
    1. send()      — single email, fresh connection each time (good for test)
    2. send_bulk() — multiple emails, one connection reused (good for daily run)
    """

    def __init__(self, smtp_host: str, smtp_port: int,
                 username: str, password: str,
                 from_name: str = "UniDocVerse"):
        self.smtp_host = smtp_host
        self.smtp_port = int(smtp_port)
        self.username  = username
        # Clean password — strip non-breaking spaces from copy-paste
        self.password  = password.replace('\xa0', '').replace('\u00a0', '').strip()
        self.from_name = from_name

    def _build_msg(self, to_email: str, subject: str, html_body: str):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{self.from_name} <{self.username}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html"))
        return msg

    def send(self, to_email: str, subject: str, html_body: str) -> bool:
        """
        Send a single email.
        Opens a fresh SMTP connection — safe for one-off sends (test email).
        """
        try:
            msg = self._build_msg(to_email, subject, html_body)
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as server:
                #server.connect(self.smtp_host, self.smtp_port)
                server.ehlo()
                server.login(self.username, self.password)
                server.sendmail(self.username, [to_email], msg.as_string())
            logger.info(f"✅ Email sent to {to_email}")
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("❌ SMTP auth failed — check Gmail app password")
            return False
        except Exception as e:
            logger.error(f"❌ Email failed to {to_email}: {e}")
            return False

    def send_bulk(self, messages: list) -> dict:
        """
        Send multiple emails over ONE reused SMTP connection.
        Efficient for daily renewal batch sends.

        messages = [
            {"to": "client@email.com", "subject": "...", "html": "..."},
            ...
        ]
        Returns {"sent": N, "failed": N}
        """
        results = {"sent": 0, "failed": 0}
        if not messages:
            return results

        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.login(self.username, self.password)

                for m in messages:
                    try:
                        msg = self._build_msg(m["to"], m["subject"], m["html"])
                        server.sendmail(self.username, [m["to"]], msg.as_string())
                        logger.info(f"✅ Bulk sent to {m['to']}")
                        results["sent"] += 1
                    except Exception as e:
                        logger.error(f"❌ Failed to {m['to']}: {e}")
                        results["failed"] += 1

        except smtplib.SMTPAuthenticationError:
            logger.error("❌ SMTP auth failed — check Gmail app password")
        except Exception as e:
            logger.error(f"❌ SMTP connection failed: {e}")

        return results


# ─────────────────────────────────────────────────────────────────────────────
# CALENDAR (.ics file)
# ─────────────────────────────────────────────────────────────────────────────

def _build_ics_event(client_name: str, policy_number: str,
                      carrier: str, expiry_date: date, days: int) -> str:
    uid     = str(uuid.uuid4())
    dtstart = expiry_date.strftime("%Y%m%d")
    dtend   = (expiry_date + timedelta(days=1)).strftime("%Y%m%d")
    now     = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    summary = f"Policy Renewal: {policy_number} ({carrier}) — {client_name}"
    desc    = f"Policy {policy_number} for {client_name} expires. Carrier: {carrier}."

    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//UniDocVerse//Renewal Reminder//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now}
DTSTART;VALUE=DATE:{dtstart}
DTEND;VALUE=DATE:{dtend}
SUMMARY:{summary}
DESCRIPTION:{desc}
BEGIN:VALARM
TRIGGER:-P14D
ACTION:DISPLAY
DESCRIPTION:14 days until policy expiry — {policy_number}
END:VALARM
BEGIN:VALARM
TRIGGER:-P7D
ACTION:DISPLAY
DESCRIPTION:7 days until policy expiry — {policy_number}
END:VALARM
BEGIN:VALARM
TRIGGER:-P1D
ACTION:DISPLAY
DESCRIPTION:Tomorrow: policy expiry — {policy_number}
END:VALARM
END:VEVENT
END:VCALENDAR"""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SERVICE
# ─────────────────────────────────────────────────────────────────────────────

class RenewalNotificationService:
    """
    Runs daily — finds policies expiring at 90/30/14/7 days
    and sends email reminders + creates .ics calendar events.

    Usage:
        sender  = RenewalEmailSender("smtp.gmail.com", 465, "you@gmail.com", "app-pass")
        service = RenewalNotificationService(db, sender)
        results = service.run()
        # {"sent": 3, "skipped": 1, "errors": 0, "calendar_created": 4}
    """

    NOTIFY_AT_DAYS = [90, 30, 14, 7]

    def __init__(self, db: Session, sender: Optional[RenewalEmailSender] = None):
        self.db     = db
        self.sender = sender

    def run(self) -> dict:
        today   = date.today()
        results = {"sent": 0, "skipped": 0, "errors": 0, "calendar_created": 0}

        for threshold in self.NOTIFY_AT_DAYS:
            target_date = today + timedelta(days=threshold)

            rows = self.db.execute(text("""
                SELECT
                    p.id              as policy_id,
                    p.policy_number,
                    p.carrier_name,
                    p.expiry_date,
                    p.expiry_date - CURRENT_DATE as days_remaining,
                    c.id              as client_id,
                    c.first_name || ' ' || COALESCE(c.last_name,'') as client_name,
                    c.email           as client_email,
                    c.client_number
                FROM policies p
                JOIN clients c ON c.id = p.client_id
                WHERE p.expiry_date BETWEEN :start AND :end
                  AND p.status IN ('active', 'renewal_due')
                  AND c.status != 'deleted'
                  AND c.email IS NOT NULL
                  AND c.email != ''
            """), {
                "start": target_date - timedelta(days=1),
                "end":   target_date + timedelta(days=1),
            }).fetchall()

            # Build batch for this threshold
            pending = []
            for row in rows:
                r = dict(row._mapping)

                if self._already_notified(r["policy_id"], threshold):
                    results["skipped"] += 1
                    continue

                subject, html = _build_renewal_email(
                    client_name   = r["client_name"].strip(),
                    policy_number = r["policy_number"],
                    carrier       = r["carrier_name"] or "your carrier",
                    expiry_date   = str(r["expiry_date"]),
                    days          = int(r["days_remaining"]),
                )
                pending.append({
                    "to":        r["client_email"],
                    "subject":   subject,
                    "html":      html,
                    "policy_id": r["policy_id"],
                    "client_id": r["client_id"],
                })

                # Always create calendar event
                self._save_calendar_event(r, int(r["days_remaining"]))
                results["calendar_created"] += 1

            # Send all emails for this threshold in one connection
            if self.sender and pending:
                bulk = self.sender.send_bulk(pending)
                results["sent"]   += bulk["sent"]
                results["errors"] += bulk["failed"]

                # Log activity for each sent email
                for m in pending:
                    self._log_activity(
                        m["policy_id"], m["client_id"],
                        f"Renewal email sent ({threshold}d reminder) to {m['to']}",
                        threshold,
                    )

        self.db.commit()
        logger.info(f"✅ Renewal notifications complete: {results}")
        return results

    def _already_notified(self, policy_id: str, threshold: int) -> bool:
        row = self.db.execute(text("""
            SELECT id FROM renewal_activities
            WHERE policy_id = :pid
              AND title LIKE :pattern
              AND created_at >= CURRENT_DATE - INTERVAL '2 days'
        """), {
            "pid":     policy_id,
            "pattern": f"%{threshold}d reminder%",
        }).fetchone()
        return row is not None

    def _log_activity(self, policy_id: str, client_id: str,
                       title: str, threshold: int) -> None:
        try:
            self.db.execute(text("""
                INSERT INTO renewal_activities
                    (id, policy_id, client_id, activity_type, title, created_by)
                VALUES
                    (:id, :pid, :cid, 'email', :title, 'renewal_notification_service')
            """), {
                "id":    str(uuid.uuid4()),
                "pid":   policy_id,
                "cid":   client_id,
                "title": title,
            })
        except Exception as e:
            logger.warning(f"⚠ log_activity failed: {e}")

    def _save_calendar_event(self, row: dict, days: int) -> None:
        try:
            import os
            cal_dir = os.path.expanduser(
                "~/Library/Application Support/UniDocVerse/calendar"
            )
            os.makedirs(cal_dir, exist_ok=True)
            ics = _build_ics_event(
                client_name   = row["client_name"].strip(),
                policy_number = row["policy_number"],
                carrier       = row["carrier_name"] or "Carrier",
                expiry_date   = row["expiry_date"],
                days          = days,
            )
            filename = f"{row['policy_number'].replace('/', '-')}_renewal.ics"
            with open(os.path.join(cal_dir, filename), "w") as f:
                f.write(ics)
            logger.info(f"📅 Calendar event saved: {filename}")
        except Exception as e:
            logger.warning(f"⚠ Calendar save failed: {e}")