# app/api/auth_router.py
import logging, os, secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

JWT_SECRET     = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM  = "HS256"
ACCESS_EXPIRE  = timedelta(hours=8)
REFRESH_EXPIRE = timedelta(days=30)

PROTECTED_ADMIN = "admin"  # this username can never be deleted


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    full_name: str
    password: str
    role: str = "user"

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class ResetPasswordRequest(BaseModel):
    username: str
    new_password: str

class PreferencesRequest(BaseModel):
    language: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt(12)).decode()

def _verify_password(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode(), h.encode())
    except Exception:
        return False

def _create_token(user_id: int, username: str, role: str, expires: timedelta) -> str:
    return jwt.encode({
        "sub":      str(user_id),
        "username": username,
        "role":     role,
        "exp":      datetime.utcnow() + expires,
        "iat":      datetime.utcnow(),
    }, JWT_SECRET, algorithm=JWT_ALGORITHM)

def _decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = _decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.execute(text("""
        SELECT id, username, full_name, role, is_active, avatar_color, language
        FROM users WHERE id = :uid AND is_active = TRUE
    """), {"uid": int(payload["sub"])}).fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return {
        "id":           user.id,
        "username":     user.username,
        "full_name":    user.full_name,
        "role":         user.role,
        "avatar_color": user.avatar_color,
        "language":     user.language,
    }

def get_current_user_or_session(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    session_token: Optional[str] = Query(None),
    db: Session = Depends(get_db)
) -> dict:
    """Accepts JWT bearer token OR a valid chatbot session token (for EventSource clients)."""
    if credentials:
        payload = _decode_token(credentials.credentials)
        if payload:
            user = db.execute(text("""
                SELECT id, username, full_name, role, is_active
                FROM users WHERE id = :uid AND is_active = TRUE
            """), {"uid": int(payload["sub"])}).fetchone()
            if user:
                return {"id": user.id, "username": user.username, "role": user.role}
    if session_token:
        row = db.execute(text("""
            SELECT token FROM chatbot_sessions
            WHERE token = :t AND expires_at > now()
        """), {"t": session_token}).fetchone()
        if row:
            return {"id": None, "username": "chatbot_session", "role": "user"}
    raise HTTPException(status_code=401, detail="Not authenticated")

def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    return current_user


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    # return {
    #     "access_token": "abc",
    #     "refresh_token": "xyz",
    #     "token_type": "bearer",
    #     "user": {
    #         "id": 1,
    #         "username": "admin",
    #         "full_name": "Test",
    #         "role": "admin",
    #         "avatar_color": "#6366f1"
    #     }
    # }
    user = db.execute(text("""
        SELECT id, username, full_name, password_hash, role, is_active, avatar_color, language
        FROM users WHERE username = :username
    """), {"username": req.username.lower().strip()}).fetchone()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not _verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    db.execute(text("UPDATE users SET last_login = NOW() WHERE id = :id"), {"id": user.id})
    db.commit()

    logger.info(f"✅ Login: {user.username} ({user.role})")
    return {
        "access_token":  _create_token(user.id, user.username, user.role, ACCESS_EXPIRE),
        "refresh_token": _create_token(user.id, user.username, user.role, REFRESH_EXPIRE),
        "token_type":    "bearer",
        "user": {
            "id":           user.id,
            "username":     user.username,
            "full_name":    user.full_name,
            "role":         user.role,
            "avatar_color": user.avatar_color,
            "language":     user.language,
        }
    }

@router.post("/refresh")
def refresh_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Refresh token required")
    payload = _decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return {
        "access_token": _create_token(
            int(payload["sub"]), payload["username"], payload["role"], ACCESS_EXPIRE
        ),
        "token_type": "bearer"
    }

@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

@router.put("/me/preferences")
def update_preferences(
    req: PreferencesRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if req.language not in ["en", "es", "fr", "de"]:
        raise HTTPException(status_code=400, detail="Invalid language. Supported: en, es, fr, de")
    db.execute(text("""
        UPDATE users SET language = :lang, updated_at = NOW() WHERE id = :id
    """), {"lang": req.language, "id": current_user["id"]})
    db.commit()
    return {"success": True, "language": req.language}

@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user = db.execute(text(
        "SELECT password_hash FROM users WHERE id = :id"
    ), {"id": current_user["id"]}).fetchone()
    if not _verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    db.execute(text("""
        UPDATE users SET password_hash = :hash, updated_at = NOW() WHERE id = :id
    """), {"hash": _hash_password(req.new_password), "id": current_user["id"]})
    db.commit()
    return {"success": True, "message": "Password changed successfully"}

@router.post("/register")
def register_user(req: RegisterRequest, db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    existing = db.execute(text(
        "SELECT id FROM users WHERE username = :username"
    ), {"username": req.username.lower()}).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    colors = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6"]
    color  = colors[sum(ord(c) for c in req.full_name) % len(colors)]
    db.execute(text("""
        INSERT INTO users (username, email, full_name, password_hash, role, avatar_color, tenant_id)
        VALUES (:username, :email, :name, :hash, :role, :color, :tenant_id)
    """), {
        "username":  req.username.lower(),
        "email":     req.username.lower() + "@local",
        "name":      req.full_name,
        "hash":      _hash_password(req.password),
        "role":      req.role,
        "color":     color,
        "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID,
    })
    db.commit()
    logger.info(f"✅ User created: {req.username} ({req.role})")
    return {"success": True, "message": f"User {req.full_name} created successfully"}

@router.post("/reset-password")
def admin_reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    user = db.execute(text(
        "SELECT id FROM users WHERE username = :username"
    ), {"username": req.username.lower()}).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.execute(text("""
        UPDATE users SET password_hash = :hash, updated_at = NOW() WHERE id = :id
    """), {"hash": _hash_password(req.new_password), "id": user.id})
    db.commit()
    return {"success": True, "message": "Password reset successfully"}

@router.get("/users")
def list_users(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    rows = db.execute(text("""
        SELECT id, username, full_name, role, is_active, avatar_color, language, created_at, last_login
        FROM users ORDER BY created_at DESC
    """)).fetchall()
    return {"users": [
        {
            "id":           r.id,
            "username":     r.username,
            "full_name":    r.full_name,
            "role":         r.role,
            "is_active":    r.is_active,
            "avatar_color": r.avatar_color,
            "language":     r.language,
            "created_at":   r.created_at.isoformat() if r.created_at else None,
            "last_login":   r.last_login.isoformat() if r.last_login else None,
        }
        for r in rows
    ]}

@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    user = db.execute(text(
        "SELECT id, username, full_name FROM users WHERE id = :id"
    ), {"id": user_id}).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.username.lower() == PROTECTED_ADMIN:
        raise HTTPException(status_code=403, detail="The admin account cannot be deleted")
    db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    db.commit()
    logger.info(f"🗑️ Deleted: {user.full_name}")
    return {"success": True, "message": f"User {user.full_name} deleted"}