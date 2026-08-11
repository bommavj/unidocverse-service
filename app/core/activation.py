import hashlib
import json
import platform
import uuid
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

from app.core.rsa_utils import verify_rsa_signature

ACTIVATION_URL = "https://unidocverse-activation.vijay-bomma.workers.dev/api/activate"
APP_NAME = "unidocverse"

TRIAL_DURATION_DAYS = 90  # ← change this to adjust trial length


def get_support_dir() -> Path:
    base = Path.home() / "Library" / "Application Support" / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_activation_path() -> Path:
    return get_support_dir() / ".activation"


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def get_device_fingerprint() -> str:
    parts = [
        platform.node(),
        platform.machine(),
        str(uuid.getnode()),
    ]
    raw = "|".join(parts)
    salt = "UDV_LOCAL_SALT_2026"
    return sha256_hex(raw + salt)


def call_cloudflare_activation(
    license_key: str,
    device_fp: str,
    expiry: str,
    version: int
) -> dict:
    try:
        resp = requests.post(
            ACTIVATION_URL,
            json={
                "license_key": license_key,
                "device_id": device_fp,
                "expiry": expiry,
                "version": version
            },
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    except requests.exceptions.HTTPError as e:
        try:
            return resp.json()
        except Exception:
            return {"allowed": False, "error": f"HTTP error: {str(e)}"}

    except Exception as e:
        return {"allowed": False, "error": f"Activation request failed: {str(e)}"}


def write_activation_file(payload: dict):
    path = get_activation_path()
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def read_activation_file() -> dict:
    path = get_activation_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────
# NEW: Trial helpers
# ─────────────────────────────────────────────────────────────

def create_trial_activation() -> dict:
    """
    Generate a 30-day local trial tied to this device.
    No network call — purely local.
    Called on first launch automatically.
    """
    device_fp = get_device_fingerprint()
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(days=TRIAL_DURATION_DAYS)

    payload = {
        "license_type": "trial",
        "license_key": f"TRIAL-{device_fp[:16]}",
        "device_id": device_fp,
        "issued_at": now.isoformat(),
        "expiry_at": expiry.isoformat(),
    }
    write_activation_file(payload)
    return payload


def get_trial_status(activation: dict) -> dict:
    """
    Returns trial status dict with days_remaining.
    Used by /api/license/status and /api/license/activate-trial.
    """
    now = datetime.now(timezone.utc)
    expiry = datetime.fromisoformat(activation["expiry_at"])
    days_remaining = max(0, (expiry - now).days)
    is_valid = now < expiry

    return {
        "license_type": "trial",
        "is_valid": is_valid,
        "is_active": is_valid,
        "days_remaining": days_remaining,
        "expiry_at": activation["expiry_at"],
        "message": (
            f"Trial active — {days_remaining} day{'s' if days_remaining != 1 else ''} remaining"
            if is_valid else
            "Trial expired. Please request a free license."
        )
    }


# ─────────────────────────────────────────────────────────────
# ensure_activated — unchanged signature, updated logic
# ─────────────────────────────────────────────────────────────

def ensure_activated(license_path: Path, public_key_pem: str):
    """
    Main entry point. Called on app startup.

    Trial flow:
      - No activation → raise (caller should hit /activate-trial first)
      - Trial active   → return activation
      - Trial expired  → raise with clear message
      - Full license   → validate device + expiry as before
    """
    device_fp = get_device_fingerprint()
    activation = read_activation_file()

    if activation:
        license_type = activation.get("license_type", "full")

        # ── Trial ────────────────────────────────────────────
        if license_type == "trial":
            if activation.get("device_id") != device_fp:
                raise RuntimeError("Activation mismatch: wrong device.")
            expiry = datetime.fromisoformat(activation["expiry_at"])
            if datetime.now(timezone.utc) > expiry:
                raise RuntimeError("Trial expired. Please request a free license.")
            return activation

        # ── Full license (original logic, unchanged) ─────────
        if activation["device_id"] != device_fp:
            raise RuntimeError("Activation mismatch: wrong device.")
        expiry = datetime.fromisoformat(activation["expiry_at"])
        if datetime.now(timezone.utc) > expiry:
            raise RuntimeError("Activation expired. Please upload a new license.")
        return activation

    # ── No activation at all ─────────────────────────────────
    if license_path is None:
        raise RuntimeError("No activation found. Please upload your license file.")

    # ── License file provided → activate (original flow) ─────
    lic = json.loads(Path(license_path).read_text(encoding="utf-8"))
    license_key = lic["signature"][:32]

    if not verify_rsa_signature(lic, public_key_pem):
        raise RuntimeError("Invalid license signature.")

    expiry = lic["expiry"]
    result = call_cloudflare_activation(license_key, device_fp, expiry, version=1)

    if not result.get("allowed"):
        raise RuntimeError(result.get("error", "Activation denied."))

    activation_payload = {
        "license_type": "full",
        "license_key": license_key,
        "device_id": device_fp,
        "expiry_at": result["expiry_at"],
    }
    write_activation_file(activation_payload)
    return activation_payload