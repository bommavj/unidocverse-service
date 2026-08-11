"""
Emergency password reset — run from terminal when admin forgot password.
Usage: python3 -m app.scripts.reset_password --email admin@jdshield.com --password newpass123
"""
import argparse
import bcrypt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import SessionLocal
from sqlalchemy import text

def reset_password(email: str, password: str):
    if len(password) < 8:
        print("❌ Password must be at least 8 characters")
        return

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
    db = SessionLocal()
    try:
        result = db.execute(text(
            "UPDATE users SET password_hash = :hash, updated_at = NOW() WHERE email = :email"
        ), {"hash": hashed, "email": email.lower()})
        db.commit()
        if result.rowcount == 0:
            print(f"❌ No user found with email: {email}")
        else:
            print(f"✅ Password reset for {email}")
    finally:
        db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email",    required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    reset_password(args.email, args.password)
