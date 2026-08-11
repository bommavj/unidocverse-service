# license_verify.py
import json
import base64
from datetime import datetime
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from .license_paths import get_license_path

PUBLIC_KEY_PATH = Path(__file__).parent / "public_key.pem"


class LicenseError(Exception):
    pass


def load_public_key():
    with open(PUBLIC_KEY_PATH, "rb") as f:
        return serialization.load_pem_public_key(f.read())


def canonical_payload(license_data: dict) -> bytes:
    data = {k: v for k, v in license_data.items() if k != "signature"}
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_signature(license_data: dict):
    if "signature" not in license_data:
        raise LicenseError("Missing signature")

    try:
        signature = base64.b64decode(license_data["signature"])
    except Exception:
        raise LicenseError("Invalid signature encoding")

    public_key = load_public_key()
    payload = canonical_payload(license_data)

    try:
        public_key.verify(
            signature,
            payload,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except Exception:
        raise LicenseError("Signature verification failed")

    return True



def check_expiry(license_data: dict):
    expiry = license_data.get("expiry")
    if not expiry:
        return
    try:
        expiry_date = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except Exception:
        raise LicenseError("Invalid expiry format")
    if datetime.utcnow() > expiry_date.replace(tzinfo=None):
        raise LicenseError("License expired")


def load_and_verify_license() -> dict:
    path = get_license_path()
    if not path.exists():
        raise LicenseError("License file not found")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    verify_signature(data)
    check_expiry(data)
    return data
