"""
app/api/whatsapp_qr_router.py
──────────────────────────────
Proxy router for the WhatsApp Web QR service (Node.js on port 3001).
Manages the Node.js child process lifecycle and exposes simple endpoints
for the Angular frontend.

Endpoints:
  POST /api/whatsapp/start    → start / restart the Node.js WA service
  GET  /api/whatsapp/status   → { state, qr }  (qr = base64 PNG)
  POST /api/whatsapp/send     → { to, message }
  POST /api/whatsapp/logout   → destroy session
"""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp-qr"])

WA_SERVICE_URL  = "http://127.0.0.1:3001"
SERVICE_DIR     = Path(__file__).parent.parent.parent / "whatsapp_service"
_wa_process: Optional[subprocess.Popen] = None


# ── Process management ────────────────────────────────────────────────────────

def _start_wa_process() -> None:
    """Spawn the Node.js WhatsApp service if not already running."""
    global _wa_process

    if _wa_process is not None and _wa_process.poll() is None:
        return  # already running

    node_script = SERVICE_DIR / "server.js"
    if not node_script.exists():
        raise RuntimeError(f"WhatsApp service not found at {node_script}")

    # Use absolute node path so it's found regardless of PATH in the uvicorn env
    node_bin = "/opt/homebrew/bin/node"
    if not Path(node_bin).exists():
        import shutil
        node_bin = shutil.which("node") or "node"

    log_file = SERVICE_DIR / "wa_service.log"
    log_fh   = open(log_file, "a")

    env = dict(__import__("os").environ)
    env["NODE_PATH"] = str(SERVICE_DIR / "node_modules")

    _wa_process = subprocess.Popen(
        [node_bin, str(node_script)],
        cwd=str(SERVICE_DIR),
        stdout=log_fh,
        stderr=log_fh,
        env=env,
    )
    logger.info(f"✅ WhatsApp service started (pid={_wa_process.pid}), log={log_file}")


async def _wa_get(path: str, timeout: float = 8.0) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(f"{WA_SERVICE_URL}{path}")
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "WhatsApp service is not running. Call /start first.")
    except Exception as e:
        raise HTTPException(502, str(e))


async def _wa_post(path: str, body: dict = {}, timeout: float = 10.0) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{WA_SERVICE_URL}{path}", json=body)
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "WhatsApp service is not running. Call /start first.")
    except httpx.HTTPStatusError as e:
        detail = {}
        try:
            detail = e.response.json()
        except Exception:
            pass
        raise HTTPException(e.response.status_code, detail.get("error", str(e)))
    except Exception as e:
        raise HTTPException(502, str(e))


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_session():
    """Start or restart the WhatsApp Web session."""
    try:
        _start_wa_process()
    except Exception as e:
        raise HTTPException(500, f"Could not start WhatsApp service: {e}")

    # Chromium takes ~10-15s to boot — return immediately, let frontend poll
    return {"ok": True, "state": "starting"}


@router.get("/status")
async def get_status():
    """
    Poll this endpoint every 2-3 s from the frontend.
    Returns:
      { state: "idle"|"starting"|"qr"|"authenticated"|"disconnected", qr?: "data:image/png;base64,..." }
    """
    if _wa_process is None:
        return {"state": "idle", "qr": None}

    # Process exited unexpectedly
    if _wa_process.poll() is not None:
        return {"state": "disconnected", "qr": None}

    # Process is alive — try to reach the Node.js HTTP server
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{WA_SERVICE_URL}/status")
            return r.json()
    except httpx.ConnectError:
        # Node HTTP server not ready yet (still booting Chromium) — that's fine
        return {"state": "starting", "qr": None}
    except Exception:
        return {"state": "starting", "qr": None}



class SendPayload(BaseModel):
    to: str       # phone number, e.g. +15551234567
    message: str


@router.post("/send")
async def send_message(payload: SendPayload):
    """Send a WhatsApp message via the active session."""
    return await _wa_post("/send", {"to": payload.to, "message": payload.message})


@router.post("/logout")
async def logout():
    """Destroy the WhatsApp session and stop the service."""
    global _wa_process
    try:
        await _wa_post("/logout")
    except Exception:
        pass
    if _wa_process and _wa_process.poll() is None:
        _wa_process.terminate()
        _wa_process = None
    return {"ok": True}
