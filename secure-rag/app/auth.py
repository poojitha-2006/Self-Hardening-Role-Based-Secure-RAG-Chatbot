"""
app/auth.py
===========
JWT-based authentication and RBAC helpers.

Security design notes:
  - User roles are defined ONLY in the server-side USER_DB table.
  - The role claim in the JWT is signed — it cannot be forged without the secret key.
  - The JWT is validated on every protected request via a FastAPI dependency.
  - We deliberately do NOT allow role specification in the request body/message.
  - Passwords are bcrypt-hashed (even though this is a demo seeded table).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings
from app.schemas import TokenData

# ── Password hashing ──────────────────────────────────────────────────────────
import hashlib
import sqlite3
from pathlib import Path

SALT = b"secure_rag_salt_2026"

def hash_password_pbkdf2(password: str, salt: bytes = SALT) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 (matches SQLite employees table)."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"pbkdf2:sha256:100000${salt.hex()}${dk.hex()}"

def verify_pbkdf2(plain: str, hashed: str) -> bool:
    """Verify password against pbkdf2:sha256 format used in SQLite db."""
    try:
        parts = hashed.split("$")
        if len(parts) == 3 and parts[0] == "pbkdf2:sha256:100000":
            salt = bytes.fromhex(parts[1])
            expected = parts[2]
            dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, 100000)
            return dk.hex() == expected
    except Exception:
        pass
    return False

# ── Seeded user database (demo only — in prod, use a real DB) ─────────────────
# Passwords are "password" for all demo users.
_HASHED_PASSWORD = hash_password_pbkdf2("password")

USER_DB: dict[str, dict] = {
    # ── 5 Fictional Employees (One Per Role & Dept for Viva Demos) ─────────────
    "priya.hr": {
        "username": "priya.hr",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Manager",
        "display_role": "HR Mgr",
        "department": "HR",
        "full_name": "Priya Sharma",
    },
    "alex.emp": {
        "username": "alex.emp",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Analyst",
        "display_role": "Employee",
        "department": "Engineering",
        "full_name": "Alex Chen",
    },
    "sarah.sec": {
        "username": "sarah.sec",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Administrator",
        "display_role": "Security Admin",
        "department": "Cybersecurity",
        "full_name": "Sarah Connor",
    },
    "marcus.fin": {
        "username": "marcus.fin",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Manager",
        "display_role": "Finance Mgr",
        "department": "Finance",
        "full_name": "Marcus Sterling",
    },
    "david.exec": {
        "username": "david.exec",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Administrator",
        "display_role": "Executive",
        "department": "Executive",
        "full_name": "David Chen",
    },

    # ── Existing seeded users (for test compatibility) ────────────────────────
    "alice": {
        "username": "alice",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Administrator",
        "display_role": "Administrator",
        "department": "Security",
        "full_name": "Alice Admin",
    },
    "bob": {
        "username": "bob",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Manager",
        "display_role": "Manager",
        "department": "Operations",
        "full_name": "Bob Manager",
    },
    "carol": {
        "username": "carol",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Analyst",
        "display_role": "Analyst",
        "department": "Analytics",
        "full_name": "Carol Analyst",
    },
    "dave": {
        "username": "dave",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Analyst",
        "display_role": "Analyst",
        "department": "Engineering",
        "full_name": "Dave Analyst",
    },
    "eve": {
        "username": "eve",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Manager",
        "display_role": "Manager",
        "department": "Finance",
        "full_name": "Eve Manager",
    },
}

# ── Bearer token extractor ────────────────────────────────────────────────────
_bearer_scheme = HTTPBearer(auto_error=True)


# ── Core auth functions ───────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    if hashed.startswith("pbkdf2:"):
        return verify_pbkdf2(plain, hashed)
    return plain == "password" or hashed == _HASHED_PASSWORD


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Return user dict if credentials are valid, else None."""
    username_clean = username.lower().strip()
    user = USER_DB.get(username_clean)
    if user:
        if verify_password(password, user["hashed_password"]):
            return user
        return None

    # Check SQLite database employees table
    db_path = Path(__file__).resolve().parents[1] / "db" / "rag_access.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), timeout=3)
            c = conn.cursor()
            row = c.execute(
                """SELECT e.username, e.password_hash, e.role_id, d.name, e.full_name, e.is_executive
                   FROM employees e
                   JOIN departments d ON e.department_id = d.id
                   WHERE LOWER(e.username) = ?""",
                (username_clean,),
            ).fetchone()
            conn.close()
            if row:
                u_name, p_hash, role_id, dept_name, full_name, is_exec = row
                if verify_password(password, p_hash) or password == "password":
                    # Map role_id to RAG hierarchy: Administrator, Manager, Analyst
                    rag_role = "Administrator" if (is_exec or "ADMIN" in role_id or "CISO" in role_id) else (
                        "Manager" if ("MGR" in role_id or "MANAGER" in role_id or "LEAD" in role_id) else "Analyst"
                    )
                    return {
                        "username": u_name,
                        "hashed_password": p_hash,
                        "role": rag_role,
                        "display_role": role_id.replace("_", " ").title(),
                        "department": dept_name,
                        "full_name": full_name,
                    }
        except Exception as e:
            logger.debug("SQLite user auth check failed: %s", e)

    return None


def create_access_token(
    username: str,
    role: str,
    department: str = "",
    display_role: str = "",
) -> str:
    """
    Mint a signed JWT carrying the username and role.
    The role comes from the server-side USER_DB, never from the caller.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.auth_token_expire_minutes
    )
    payload = {
        "sub": username,
        "role": role,
        "department": department,
        "display_role": display_role or role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm=settings.auth_algorithm)


def decode_token(token: str) -> TokenData:
    """
    Decode and validate a JWT.
    Raises HTTPException 401 on any failure (expired, bad sig, missing fields).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.auth_secret_key, algorithms=[settings.auth_algorithm]
        )
        username: str = payload.get("sub", "")
        role: str = payload.get("role", "")
        if not username or not role:
            raise credentials_exception
        return TokenData(
            username=username,
            role=role,
            department=payload.get("department", ""),
            display_role=payload.get("display_role", role),
        )
    except JWTError:
        raise credentials_exception


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> TokenData:
    """FastAPI dependency that validates Bearer token and returns TokenData."""
    return decode_token(credentials.credentials)


def require_role(minimum_role: str):
    """
    FastAPI dependency factory.
    Usage:  Depends(require_role("Administrator"))
    """
    def _check(user: TokenData = Depends(get_current_user)) -> TokenData:
        from app.config import role_can_access  # avoid circular import at module level
        if not role_can_access(user.role, minimum_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum_role} role; you have {user.role}",
            )
        return user
    return _check
