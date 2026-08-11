from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import base64
import json

def verify_rsa_signature(license_data: dict, public_key_pem: str) -> bool:
    """
    Verifies RSA signature of the license file.
    The signature covers all fields EXCEPT the signature itself.
    """

    if "signature" not in license_data:
        return False

    try:
        signature = base64.b64decode(license_data["signature"])
    except Exception:
        return False

    payload = canonical_payload(license_data)

    try:
        public_key_pem.verify(
            signature,
            payload,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except Exception:
        return False


    # Extract signature (base64 → bytes)
    # signature = base64.b64decode(license_data["signature"])
    #
    # # Remove signature field for hashing
    # payload = {k: v for k, v in license_data.items() if k != "signature"}
    #
    # # Canonical JSON encoding (sorted keys, no whitespace)
    # canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    #
    # # Load public key
    # public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    #
    # try:
    #     public_key.verify(
    #         signature,
    #         canonical,
    #         padding.PKCS1v15(),
    #         hashes.SHA256()
    #     )
    #     return True
    # except Exception:
    #     return False


def canonical_payload(license_data: dict) -> bytes:
    data = {k: v for k, v in license_data.items() if k != "signature"}
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")