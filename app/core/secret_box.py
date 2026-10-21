"""
Encryption for secrets stored in the database (cloud model API keys).

The key comes from UDV_SECRET_KEY (a Fernet key) when set — use that for Docker
and servers so it survives container rebuilds. Otherwise a key is generated once
and kept next to the app's data directory with owner-only permissions.
"""
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.paths import UPLOADS_DIR


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    env_key = os.getenv("UDV_SECRET_KEY")
    if env_key:
        return Fernet(env_key.encode())
    key_file = Path(UPLOADS_DIR).parent / "secret.key"
    if not key_file.exists():
        key_file.write_bytes(Fernet.generate_key())
        os.chmod(key_file, 0o600)
    return Fernet(key_file.read_bytes().strip())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    """Raises ValueError when the key changed since the secret was stored."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Stored secret can't be decrypted — re-enter it") from e


def mask(secret: str) -> str:
    return f"{secret[:4]}…{secret[-4:]}" if len(secret) > 12 else "••••"
