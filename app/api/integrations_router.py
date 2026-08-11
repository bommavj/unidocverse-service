"""
app/api/integrations_router.py
──────────────────────────────
CRUD + test endpoints for chat/messaging integrations.

Supported providers:
  - whatsapp   (Twilio WhatsApp API or CallMeBot)
  - telegram   (Bot Token + Chat ID)
  - discord    (Incoming Webhook URL)
  - slack      (Incoming Webhook URL)
  - teams      (Incoming Webhook URL)

DB: stores config in a JSON column in `app_settings`
"""

import json
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/integrations", tags=["integrations"])

# ── Storage key in app_settings ──────────────────────────────────────────────
SETTINGS_KEY = "messaging_integrations"

PROVIDER_LABELS = {
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "discord":  "Discord",
    "slack":    "Slack",
    "teams":    "Microsoft Teams",
}

# ── Models ────────────────────────────────────────────────────────────────────

class IntegrationConfig(BaseModel):
    provider: str                   # whatsapp | telegram | discord | slack | teams
    enabled:  bool = True
    # provider-specific fields (all optional, validated per provider)
    webhook_url:  Optional[str] = None   # discord, slack, teams
    bot_token:    Optional[str] = None   # telegram
    chat_id:      Optional[str] = None   # telegram
    phone_number: Optional[str] = None   # whatsapp (Twilio To number, e.g. whatsapp:+1234567890)
    account_sid:  Optional[str] = None   # whatsapp (Twilio)
    auth_token:   Optional[str] = None   # whatsapp (Twilio)
    from_number:  Optional[str] = None   # whatsapp (Twilio From number)

class TestPayload(BaseModel):
    provider: str

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_configs(db: Session) -> dict:
    """Load all integration configs from app_settings."""
    try:
        row = db.execute(
            text("SELECT value FROM app_settings WHERE key = :k"),
            {"k": SETTINGS_KEY}
        ).fetchone()
        return json.loads(row.value) if row else {}
    except Exception:
        return {}

def _save_configs(db: Session, configs: dict):
    """Upsert all integration configs into app_settings."""
    db.execute(
        text("""
            INSERT INTO app_settings (key, value)
            VALUES (:k, :v)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """),
        {"k": SETTINGS_KEY, "v": json.dumps(configs)}
    )
    db.commit()

# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
def list_integrations(db: Session = Depends(get_db)):
    """Return current config for all providers (tokens masked)."""
    configs = _load_configs(db)
    result = []
    for provider in PROVIDER_LABELS:
        cfg = configs.get(provider, {})
        entry = {
            "provider": provider,
            "label":    PROVIDER_LABELS[provider],
            "enabled":  cfg.get("enabled", False),
            "connected": bool(cfg.get("enabled", False)),
        }
        # Expose non-secret fields for display
        if provider == "telegram":
            entry["chat_id"]   = cfg.get("chat_id", "")
            entry["has_token"] = bool(cfg.get("bot_token", ""))
        elif provider in ("discord", "slack", "teams"):
            entry["has_webhook"] = bool(cfg.get("webhook_url", ""))
        elif provider == "whatsapp":
            entry["phone_number"] = cfg.get("phone_number", "")
            entry["from_number"]  = cfg.get("from_number", "")
            entry["has_token"]    = bool(cfg.get("auth_token", ""))
        result.append(entry)
    return {"integrations": result}


@router.post("/save")
def save_integration(cfg: IntegrationConfig, db: Session = Depends(get_db)):
    """Save or update an integration config."""
    if cfg.provider not in PROVIDER_LABELS:
        raise HTTPException(400, f"Unknown provider: {cfg.provider}")

    configs = _load_configs(db)
    configs[cfg.provider] = cfg.model_dump(exclude_none=True)
    _save_configs(db, configs)
    return {"success": True, "provider": cfg.provider}


@router.post("/disconnect")
def disconnect_integration(payload: dict, db: Session = Depends(get_db)):
    """Remove / disable an integration."""
    provider = payload.get("provider")
    if not provider or provider not in PROVIDER_LABELS:
        raise HTTPException(400, "Invalid provider")
    configs = _load_configs(db)
    configs.pop(provider, None)
    _save_configs(db, configs)
    return {"success": True}


@router.post("/test")
async def test_integration(payload: TestPayload, db: Session = Depends(get_db)):
    """Send a test message to the configured provider."""
    provider = payload.provider
    configs  = _load_configs(db)
    cfg      = configs.get(provider, {})

    if not cfg:
        raise HTTPException(400, f"{PROVIDER_LABELS.get(provider, provider)} is not configured.")

    test_msg = f"✅ UniDocVerse test message from {PROVIDER_LABELS.get(provider, provider)} — your integration is working!"

    try:
        if provider == "telegram":
            token   = cfg.get("bot_token", "")
            chat_id = cfg.get("chat_id", "")
            if not token or not chat_id:
                raise HTTPException(400, "Missing bot_token or chat_id")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": test_msg}
                )
            if r.status_code != 200:
                raise HTTPException(502, f"Telegram error: {r.text}")

        elif provider in ("discord", "slack", "teams"):
            url = cfg.get("webhook_url", "")
            if not url:
                raise HTTPException(400, "Missing webhook_url")
            body = {}
            if provider == "discord":
                body = {"content": test_msg}
            elif provider == "slack":
                body = {"text": test_msg}
            else:  # teams
                body = {
                    "@type": "MessageCard",
                    "@context": "http://schema.org/extensions",
                    "text": test_msg
                }
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json=body)
            if r.status_code not in (200, 204):
                raise HTTPException(502, f"{provider} webhook error: {r.text}")

        elif provider == "whatsapp":
            account_sid = cfg.get("account_sid", "")
            auth_token  = cfg.get("auth_token", "")
            from_num    = cfg.get("from_number", "")
            to_num      = cfg.get("phone_number", "")
            if not all([account_sid, auth_token, from_num, to_num]):
                raise HTTPException(400, "Missing Twilio credentials")
            async with httpx.AsyncClient(
                auth=(account_sid, auth_token), timeout=10
            ) as client:
                r = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                    data={"From": from_num, "To": to_num, "Body": test_msg}
                )
            if r.status_code not in (200, 201):
                try:
                    twilio_err = r.json()
                    code = twilio_err.get("code", "")
                    msg  = twilio_err.get("message", r.text)
                    if r.status_code == 404:
                        msg = "Twilio Account SID not found — check your Account SID."
                    elif r.status_code == 401:
                        msg = "Twilio authentication failed — check your Auth Token."
                    elif code == 21211:
                        msg = "Invalid 'To' phone number — use format whatsapp:+15551234567"
                    elif code == 21606:
                        msg = "The From number is not WhatsApp-enabled in your Twilio account."
                    raise HTTPException(502, msg)
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(502, f"Twilio error ({r.status_code}): {r.text[:200]}")


        else:
            raise HTTPException(400, f"Provider '{provider}' test not implemented")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Integration test failed ({provider}): {e}")
        raise HTTPException(502, str(e))

    return {"success": True, "message": f"Test message sent via {PROVIDER_LABELS[provider]}"}


@router.post("/notify")
async def send_notification(payload: dict, db: Session = Depends(get_db)):
    """
    Internal: send a message to all enabled integrations.
    Called by the scheduler or other backend services.
    Body: { "message": "...", "providers": ["slack", "discord"] }  (providers optional = all)
    """
    message   = payload.get("message", "UniDocVerse notification")
    providers = payload.get("providers") or list(PROVIDER_LABELS.keys())
    configs   = _load_configs(db)

    results = {}
    for provider in providers:
        cfg = configs.get(provider, {})
        if not cfg.get("enabled", False):
            results[provider] = "disabled"
            continue
        try:
            test_payload = TestPayload(provider=provider)
            # Reuse test logic but with real message
            _cfg_backup = configs.copy()
            # Override message inline — just call the real send:
            if provider == "telegram":
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.post(
                        f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
                        json={"chat_id": cfg["chat_id"], "text": message}
                    )
                results[provider] = "ok" if r.status_code == 200 else r.text
            elif provider in ("discord", "slack", "teams"):
                body = {"content": message} if provider == "discord" else {"text": message}
                if provider == "teams":
                    body = {"@type": "MessageCard", "@context": "http://schema.org/extensions", "text": message}
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.post(cfg["webhook_url"], json=body)
                results[provider] = "ok" if r.status_code in (200, 204) else r.text
            elif provider == "whatsapp":
                async with httpx.AsyncClient(auth=(cfg["account_sid"], cfg["auth_token"]), timeout=8) as c:
                    r = await c.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}/Messages.json",
                        data={"From": cfg["from_number"], "To": cfg["phone_number"], "Body": message}
                    )
                results[provider] = "ok" if r.status_code in (200, 201) else r.text
        except Exception as e:
            results[provider] = str(e)

    return {"results": results}
