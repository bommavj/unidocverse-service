"""
app/services/gmail_connector.py
Gmail → UniDocVerse via IMAP + App Password
- No OAuth, no Google Cloud Console
- User provides gmail address + app-specific password
- Polls every 5 min for UNSEEN emails only (never reprocesses history)
- Body → text doc, attachments → batch uploaded via /api/upload/batch
- All docs tagged with source email metadata
"""

import asyncio
import email as email_lib
import email.header
import imaplib
import json
import logging
import tempfile
import threading
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

IMAP_HOST       = "imap.gmail.com"
IMAP_PORT       = 993
POLL_INTERVAL   = 300   # 5 minutes
MAX_EMAILS_POLL = 50
APP_DATA_DIR    = Path.home() / "Library" / "Application Support" / "UniDocVerse"

SUPPORTED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".html", ".htm",
    ".xlsx", ".xls", ".xlsm", ".csv", ".tsv", ".ods", ".parquet", ".json", ".jsonl",
    ".pptx", ".ppt", ".odp",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".heif",
    ".eml", ".msg", ".mbox",
    ".zip", ".tar", ".gz", ".tgz",
    ".xml", ".yaml", ".yml",
}


# ── Connection config ─────────────────────────────────────────────────────────

class GmailConnection:
    def __init__(self, connection_id: str, label: str,
                 email_address: str, app_password: str,
                 filter_folder: str = "INBOX",
                 filter_sender: str = "",
                 filter_subject: str = ""):
        self.connection_id  = connection_id
        self.label          = label
        self.email_address  = email_address
        self.app_password   = app_password
        self.filter_folder  = filter_folder
        self.filter_sender  = filter_sender
        self.filter_subject = filter_subject
        self.state_file     = APP_DATA_DIR / f"gmail_state_{connection_id}.json"

    def load_state(self) -> dict:
        try:
            if self.state_file.exists():
                with open(self.state_file) as fh:
                    return json.load(fh)
        except Exception:
            pass
        return {"processed_uids": []}

    def save_state(self, state: dict):
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as fh:
            json.dump(state, fh, indent=2)

    def test_connection(self) -> tuple[bool, str]:
        """Test IMAP credentials. Returns (success, error_message)."""
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(self.email_address, self.app_password)
            mail.logout()
            return True, ""
        except imaplib.IMAP4.error as exc:
            return False, str(exc)
        except Exception as exc:
            return False, str(exc)


# ── IMAP / email helpers ──────────────────────────────────────────────────────

def _decode_header(value: str) -> str:
    parts = email.header.decode_header(value or "")
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return "".join(decoded)


def _get_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
    return body.strip()


def _get_attachments(msg) -> List[dict]:
    attachments = []
    if not msg.is_multipart():
        return attachments
    for part in msg.walk():
        cd   = str(part.get("Content-Disposition", ""))
        ct   = part.get_content_type()
        name = part.get_filename()
        if not name:
            continue
        name = _decode_header(name)
        ext  = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            attachments.append({
                "filename":  name,
                "mime_type": ct,
                "data":      payload,
                "size":      len(payload),
            })
    return attachments


def _build_search_criteria(connection: GmailConnection) -> str:
    criteria = ["UNSEEN"]
    if connection.filter_sender:
        criteria.append(f'FROM "{connection.filter_sender}"')
    if connection.filter_subject:
        criteria.append(f'SUBJECT "{connection.filter_subject}"')
    return " ".join(criteria)


# ── Poller ────────────────────────────────────────────────────────────────────

class GmailPoller:
    def __init__(self, connection: GmailConnection, api_url: str,
                 on_event: Callable = None):
        self.connection       = connection
        self.api_url          = api_url.rstrip("/")
        self.on_event         = on_event
        self._stop_evt        = threading.Event()
        self._thread          = None
        self.status           = "idle"
        self.emails_processed = 0
        self.last_poll_at     = None
        self.last_error       = None

    def start(self):
        self._stop_evt.clear()
        self.status  = "active"
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"📧 Gmail poller started: {self.connection.label} ({self.connection.email_address})")
        self._emit("status_change", {"status": "active"})

    def stop(self):
        self._stop_evt.set()
        self.status = "idle"
        self._emit("status_change", {"status": "idle"})

    def pause(self):
        self._stop_evt.set()
        self.status = "paused"
        self._emit("status_change", {"status": "paused"})

    def _poll_loop(self):
        while not self._stop_evt.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                self.status     = "error"
                self.last_error = str(exc)
                logger.error(f"Gmail poll error [{self.connection.label}]: {exc}")
                self._emit("status_change", {"status": "error", "error": str(exc)})
            self._stop_evt.wait(POLL_INTERVAL)

    def _poll_once(self):
        logger.info(f"📧 Polling: {self.connection.email_address} / {self.connection.filter_folder}")
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(self.connection.email_address, self.connection.app_password)
        except imaplib.IMAP4.error as exc:
            raise RuntimeError(f"IMAP login failed: {exc}")

        try:
            mail.select(self.connection.filter_folder)
            criteria = _build_search_criteria(self.connection)
            status, data = mail.search(None, criteria)
            if status != "OK":
                raise RuntimeError(f"IMAP SEARCH failed: {status}")

            uid_list = data[0].split()
            if not uid_list:
                logger.info("  📭 No new emails")
                self.last_poll_at = datetime.now().isoformat()
                return

            state         = self.connection.load_state()
            processed_set = set(state.get("processed_uids", []))
            new_uids      = [u.decode() for u in uid_list
                             if u.decode() not in processed_set][-MAX_EMAILS_POLL:]

            if not new_uids:
                logger.info("  📭 No unprocessed emails")
                self.last_poll_at = datetime.now().isoformat()
                return

            logger.info(f"  📬 {len(new_uids)} new email(s)")
            self._emit("poll_found", {"count": len(new_uids)})

            for uid in new_uids:
                try:
                    self._fetch_and_ingest(mail, uid)
                    processed_set.add(uid)
                    self.emails_processed += 1
                except Exception as exc:
                    logger.warning(f"  ⚠ Failed UID {uid}: {exc}")

            state["processed_uids"] = list(processed_set)[-2000:]
            self.connection.save_state(state)
            self.last_poll_at = datetime.now().isoformat()
            self._emit("poll_complete", {"processed": len(new_uids)})

        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def _fetch_and_ingest(self, mail: imaplib.IMAP4_SSL, uid: str):
        status, data = mail.fetch(uid, "(RFC822)")
        if status != "OK" or not data or not data[0]:
            raise RuntimeError(f"IMAP fetch failed for UID {uid}")

        msg     = email_lib.message_from_bytes(data[0][1])
        subject = _decode_header(msg.get("Subject", "(no subject)"))
        sender  = _decode_header(msg.get("From", "unknown"))
        date    = msg.get("Date", "")
        msg_id  = msg.get("Message-ID", uid)

        try:
            date_parsed = parsedate_to_datetime(date).isoformat() if date else datetime.now().isoformat()
        except Exception:
            date_parsed = datetime.now().isoformat()

        body        = _get_body(msg)
        attachments = _get_attachments(msg)

        logger.info(f"  📨 '{subject}' from {sender} ({len(attachments)} attachment(s))")

        email_meta = {
            "source":          "gmail",
            "msg_id":          msg_id,
            "subject":         subject,
            "sender":          sender,
            "email_date":      date_parsed,
            "connection_id":   self.connection.connection_id,
            "has_attachments": len(attachments) > 0,
        }

        # Upload email body as text doc
        body_content = (
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Date: {date_parsed}\n"
            f"Message-ID: {msg_id}\n"
            f"\n{'=' * 60}\n\n"
            f"{body}"
        )
        body_filename = f"{_safe_filename(subject)} - {_safe_filename(sender[:30])} - {_short_id(msg_id)}.txt"
        email_doc_id  = self._upload_text(body_content, body_filename, email_meta)

        # Upload attachments
        if attachments:
            self._upload_attachments(attachments, {**email_meta, "parent_email_doc_id": email_doc_id})

        self._emit("email_processed", {
            "subject":      subject,
            "sender":       sender,
            "msg_id":       msg_id,
            "attachments":  len(attachments),
            "email_doc_id": email_doc_id,
        })

    def _upload_text(self, content: str, filename: str, metadata: dict) -> Optional[str]:
        try:
            resp = requests.post(
                f"{self.api_url}/api/upload",
                files=[("file", (filename, content.encode("utf-8"), "text/plain"))],
                timeout=60,
            )
            if resp.status_code == 409:      # same email body already imported
                existing = (resp.json().get("detail") or {}).get("existing") or {}
                logger.info(f"    ⏭ Already imported as {existing.get('filename')} — skipped")
                return existing.get("id")
            resp.raise_for_status()
            data   = resp.json()
            doc_id = data.get("id") or data.get("document_id")
            if doc_id:
                self._patch_metadata(doc_id, metadata)
            logger.info(f"    ✓ Body uploaded → doc_id={doc_id}")
            return doc_id
        except Exception as exc:
            logger.warning(f"    ⚠ Body upload failed: {exc}")
            return None

    def _upload_attachments(self, attachments: List[dict], metadata: dict):
        with tempfile.TemporaryDirectory() as tmp:
            files   = []
            handles = []
            for att in attachments:
                dest = Path(tmp) / att["filename"]
                dest.write_bytes(att["data"])
                fh = open(str(dest), "rb")
                handles.append(fh)
                files.append(("files", (att["filename"], fh, att["mime_type"])))
            try:
                resp = requests.post(
                    f"{self.api_url}/api/upload/batch",
                    files=files,
                    timeout=300,
                )
                for fh in handles:
                    fh.close()
                resp.raise_for_status()
                for r in resp.json().get("results", []):
                    doc_id = r.get("id") or r.get("document_id")
                    if doc_id and r.get("status") == "success":
                        self._patch_metadata(doc_id, metadata)
                        logger.info(f"    ✓ Attachment → doc_id={doc_id}")
            except Exception as exc:
                for fh in handles:
                    try: fh.close()
                    except: pass
                logger.warning(f"    ⚠ Attachment upload failed: {exc}")

    def _patch_metadata(self, doc_id: str, metadata: dict):
        try:
            requests.patch(
                f"{self.api_url}/api/documents/{doc_id}/metadata",
                json={"metadata": metadata},
                timeout=10,
            )
        except Exception:
            pass

    def _emit(self, event_type: str, data: dict):
        if not self.on_event:
            return
        payload = {"type": event_type, "connection_id": self.connection.connection_id,
                   "ts": datetime.now().isoformat(), **data}
        try:
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(
                self.on_event(self.connection.connection_id, payload), loop
            )
        except Exception:
            pass

    def get_status(self) -> dict:
        return {
            "connection_id":    self.connection.connection_id,
            "label":            self.connection.label,
            "email":            self.connection.email_address,
            "status":           self.status,
            "emails_processed": self.emails_processed,
            "last_poll_at":     self.last_poll_at,
            "last_error":       self.last_error,
            "filter_folder":    self.connection.filter_folder,
            "filter_sender":    self.connection.filter_sender,
            "filter_subject":   self.connection.filter_subject,
        }


# ── GmailService singleton ────────────────────────────────────────────────────

class GmailService:
    CONFIG_FILE = APP_DATA_DIR / "gmail_connections.json"

    def __init__(self, api_url: str = "http://localhost:7979"):
        self.api_url   = api_url
        self._configs: Dict[str, dict]        = {}
        self._pollers: Dict[str, GmailPoller] = {}
        self._load_config()

    def _load_config(self):
        try:
            if self.CONFIG_FILE.exists():
                with open(self.CONFIG_FILE) as fh:
                    self._configs = json.load(fh)
        except Exception:
            self._configs = {}

    def _save_config(self):
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.CONFIG_FILE, "w") as fh:
            json.dump(self._configs, fh, indent=2)

    def add_connection(self, connection_id: str, label: str,
                       email_address: str, app_password: str,
                       filter_folder: str = "INBOX",
                       filter_sender: str = "",
                       filter_subject: str = "") -> dict:
        config = {
            "connection_id":    connection_id,
            "label":            label,
            "email_address":    email_address,
            "app_password":     app_password,
            "filter_folder":    filter_folder,
            "filter_sender":    filter_sender,
            "filter_subject":   filter_subject,
            "added_at":         datetime.now().isoformat(),
            "status":           "idle",
            "emails_processed": 0,
            "last_poll_at":     None,
            "last_error":       None,
        }
        self._configs[connection_id] = config
        self._save_config()
        return self._safe_config(config)

    def remove_connection(self, connection_id: str):
        self.stop_poller(connection_id)
        self._configs.pop(connection_id, None)
        self._pollers.pop(connection_id, None)
        (APP_DATA_DIR / f"gmail_state_{connection_id}.json").unlink(missing_ok=True)
        self._save_config()

    def list_connections(self) -> List[dict]:
        result = []
        for cid, cfg in self._configs.items():
            p     = self._pollers.get(cid)
            entry = self._safe_config(cfg)
            if p:
                entry.update(p.get_status())
            result.append(entry)
        return result

    def get_status(self, connection_id: str) -> Optional[dict]:
        p = self._pollers.get(connection_id)
        if p:
            return p.get_status()
        cfg = self._configs.get(connection_id)
        return self._safe_config(cfg) if cfg else None

    def test_connection(self, connection_id: str) -> tuple[bool, str]:
        conn = self._build_connection(connection_id)
        if not conn:
            return False, "Connection not found"
        return conn.test_connection()

    async def start_poller(self, connection_id: str):
        from app.api.gmail_api import broadcast_gmail_event
        conn = self._build_connection(connection_id)
        if not conn:
            raise ValueError("Connection not found")
        if connection_id in self._pollers:
            self._pollers[connection_id].stop()
        poller = GmailPoller(conn, self.api_url, on_event=broadcast_gmail_event)
        self._pollers[connection_id] = poller
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, poller.start)

    def stop_poller(self, connection_id: str):
        p = self._pollers.get(connection_id)
        if p: p.stop()

    def pause_poller(self, connection_id: str):
        p = self._pollers.get(connection_id)
        if p: p.pause()

    def _build_connection(self, connection_id: str) -> Optional[GmailConnection]:
        cfg = self._configs.get(connection_id)
        if not cfg:
            return None
        return GmailConnection(
            connection_id  = cfg["connection_id"],
            label          = cfg["label"],
            email_address  = cfg["email_address"],
            app_password   = cfg["app_password"],
            filter_folder  = cfg.get("filter_folder", "INBOX"),
            filter_sender  = cfg.get("filter_sender", ""),
            filter_subject = cfg.get("filter_subject", ""),
        )

    @staticmethod
    def _safe_config(cfg: dict) -> dict:
        return {k: v for k, v in cfg.items() if k != "app_password"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_filename(s: str, maxlen: int = 60) -> str:
    import re
    return re.sub(r'[\\/:*?"<>|\r\n]', "_", s)[:maxlen].strip()


def _short_id(s: str) -> str:
    import hashlib
    return hashlib.md5(s.encode()).hexdigest()[:8]