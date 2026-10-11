# app/core/language.py
import logging
import os
from typing import Optional
from fastapi import Request
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Replicate settings/secret properties from auth_router
JWT_SECRET = os.getenv("JWT_SECRET", "default_secret")
JWT_ALGORITHM = "HS256"

def get_request_language(request: Request, db: Session) -> str:
    """
    Intelligently determines the target language for the request.
    Priority:
      1. Query param: `?lang=es`
      2. Authorization header JWT lookup
      3. Accept-Language header
      4. Fallback: 'en'
    """
    # 1. Query parameter
    lang = request.query_params.get("lang")
    if lang in ["en", "es", "fr", "de"]:
        return lang

    # 2. Authorization user lookup
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            # Decode token using local secret (read from auth_router if importable, else default)
            from app.api.auth_router import JWT_SECRET as ROUTER_SECRET, JWT_ALGORITHM as ROUTER_ALGO
            secret = ROUTER_SECRET
            algo = ROUTER_ALGO
        except ImportError:
            secret = JWT_SECRET
            algo = JWT_ALGORITHM

        try:
            payload = jwt.decode(token, secret, algorithms=[algo])
            if payload and "sub" in payload:
                user_id = str(payload["sub"])
                user = db.execute(text("""
                    SELECT language FROM users WHERE id = :uid
                """), {"uid": user_id}).fetchone()
                if user and user.language in ["en", "es", "fr", "de"]:
                    return user.language
        except Exception as e:
            # Token might be expired or invalid, fail silently to fallback
            logger.debug(f"JWT decode failed in get_request_language: {e}")

    # 3. Accept-Language Header fallback
    accept_lang = request.headers.get("Accept-Language", "")
    if accept_lang:
        parts = [p.split(";")[0].strip().split("-")[0].lower() for p in accept_lang.split(",")]
        for p in parts:
            if p in ["en", "es", "fr", "de"]:
                return p

    return "en"
