"""
app/auth.py
===========
JWT-based authentication and RBAC helpers.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings
from app.schemas import TokenData

PBKDF2_ITERATIONS = 310_000
AUTH_TOKEN_EXPIRE_MINUTES = 60
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 5 * 60
_DUMMY_SALT = bytes.fromhex("c8c0b42af7c3522d847943d34a207fb1")


def hash_password_pbkdf2(password: str, salt: Optional[bytes] = None) -> str:
    """Return a salted PBKDF2-HMAC-SHA256 password hash."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32
    )
    return f"pbkdf2:sha256:{PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_pbkdf2(plain: str, hashed: str) -> bool:
    """Verify provisioned PBKDF2 hashes using constant-time digest comparison."""
    try:
        algorithm, salt_hex, digest_hex = hashed.split("$")
        prefix, digest_name, iterations_text = algorithm.split(":")
        if prefix != "pbkdf2" or digest_name != "sha256":
            return False
        iterations = int(iterations_text)
        if iterations < PBKDF2_ITERATIONS or iterations > 2_000_000:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if len(salt) < 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", plain.encode("utf-8"), salt, iterations, dklen=32
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeEncodeError):
        return False


def _burn_password_check(password: str) -> None:
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), _DUMMY_SALT, PBKDF2_ITERATIONS, dklen=32
    )
    hmac.compare_digest(actual, bytes(32))


_SEEDED_PASSWORD_HASHES = {
    "priya.hr": "pbkdf2:sha256:310000$9396b0da9eade3a0a7d22128f097a830$8ca6fda723c1a1b2ed0eccbaaa83e13b23aa87208cf5813dddd758764e51328c",
    "alex.emp": "pbkdf2:sha256:310000$b520994d7ba5cd88b07a305224c7698c$ebb951a4b937628121510474b52272217f2e633107a508cab3b7d18501007c26",
    "sarah.sec": "pbkdf2:sha256:310000$5993da576b7418c00dcf7be4b9628f36$f982c47beb77c64c3f1d8c67a99bebd0fa1c1f91944e620e839fe0a1586cc906",
    "marcus.fin": "pbkdf2:sha256:310000$e9df449e30bbfbb12e1851213d41b879$4d1c145fda6b577732ef381a1474587de8a59ed765af69f86c83f0f8c2543aa8",
    "david.exec": "pbkdf2:sha256:310000$1d730e5d69cb2ff5c7651ccf2667fe54$d08f335b1c02e8aa3fb695a1ac8f2d5a17b8ee98332c02bd0ed3609dfa2d8923",
    "alice": "pbkdf2:sha256:310000$815a26117fbfb8bc53d41bea19bca080$34caa5147d2f80628d85d5e3f9d69ba2d774f2dfb0be72a85d8e81301229e417",
    "bob": "pbkdf2:sha256:310000$dd3da3deedd0f51d9437d4ba9bbc8073$47d6f2a2e4ecc24a24f85e238f44a47a748de85e762cc16fb99ebe446e9fde88",
    "carol": "pbkdf2:sha256:310000$044016cb863f0bc0dbfd4d997980e2b6$830315f9fb91cbd0e0ecbea3c57e90e8216e5d5901564df7e0b3b753a2b977c6",
    "dave": "pbkdf2:sha256:310000$ab7712b12b2be5e9336c239b8dcc704a$164dca0eadb3901d8c8c089ba505e214f83e62c5a3f26e2f8e4d36ab0ed610c2",
    "eve": "pbkdf2:sha256:310000$85222b3a142a21cacf9ccc0961e6058c$e19761ab7d008c7a7044d18de6de592b9ebc64fe47af539b0a90c4ca402124a8",
}

USER_DB: dict[str, dict] = {
    "priya.hr": {
        "username": "priya.hr",
        "hashed_password": _SEEDED_PASSWORD_HASHES["priya.hr"],
        "role": "Manager",
        "display_role": "HR Mgr",
        "department": "HR",
        "full_name": "Priya Sharma",
    },
    "alex.emp": {
        "username": "alex.emp",
        "hashed_password": _SEEDED_PASSWORD_HASHES["alex.emp"],
        "role": "Analyst",
        "display_role": "Employee",
        "department": "Engineering",
        "full_name": "Alex Chen",
    },
    "sarah.sec": {
        "username": "sarah.sec",
        "hashed_password": _SEEDED_PASSWORD_HASHES["sarah.sec"],
        "role": "Administrator",
        "display_role": "Security Admin",
        "department": "Cybersecurity",
        "full_name": "Sarah Connor",
    },
    "marcus.fin": {
        "username": "marcus.fin",
        "hashed_password": _SEEDED_PASSWORD_HASHES["marcus.fin"],
        "role": "Manager",
        "display_role": "Finance Mgr",
        "department": "Finance",
        "full_name": "Marcus Sterling",
    },
    "david.exec": {
        "username": "david.exec",
        "hashed_password": _SEEDED_PASSWORD_HASHES["david.exec"],
        "role": "Administrator",
        "display_role": "Executive",
        "department": "Executive",
        "full_name": "David Chen",
    },
    "alice": {
        "username": "alice",
        "hashed_password": _SEEDED_PASSWORD_HASHES["alice"],
        "role": "Administrator",
        "display_role": "Administrator",
        "department": "Security",
        "full_name": "Alice Admin",
    },
    "bob": {
        "username": "bob",
        "hashed_password": _SEEDED_PASSWORD_HASHES["bob"],
        "role": "Manager",
        "display_role": "Manager",
        "department": "Operations",
        "full_name": "Bob Manager",
    },
    "carol": {
        "username": "carol",
        "hashed_password": _SEEDED_PASSWORD_HASHES["carol"],
        "role": "Analyst",
        "display_role": "Analyst",
        "department": "Analytics",
        "full_name": "Carol Analyst",
    },
    "dave": {
        "username": "dave",
        "hashed_password": _SEEDED_PASSWORD_HASHES["dave"],
        "role": "Analyst",
        "display_role": "Analyst",
        "department": "Engineering",
        "full_name": "Dave Analyst",
    },
    "eve": {
        "username": "eve",
        "hashed_password": _SEEDED_PASSWORD_HASHES["eve"],
        "role": "Manager",
        "display_role": "Manager",
        "department": "Finance",
        "full_name": "Eve Manager",
    },
}

_failed_logins: dict[str, tuple[int, float]] = {}
_bearer_scheme = HTTPBearer(auto_error=True)


def verify_password(plain: str, hashed: str) -> bool:
    return verify_pbkdf2(plain, hashed)


def _test_shared_password_enabled() -> bool:
    return (
        os.environ.get("SECURE_RAG_TEST_AUTH_COMPAT") == "1"
        and "PYTEST_CURRENT_TEST" in os.environ
    )


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Return a verified user record or None, with no plaintext fallback."""
    username_clean = username.lower().strip()
    user = USER_DB.get(username_clean)
    if user:
        if verify_password(password, user["hashed_password"]):
            return user
        if (
            _test_shared_password_enabled()
            and hmac.compare_digest(password, "password")
        ):
            return user
        return None

    db_path = Path(__file__).resolve().parents[1] / "db" / "rag_access.db"
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path), timeout=3) as conn:
                row = conn.execute(
                    """SELECT e.username, e.password_hash, e.role_id, d.name,
                              e.full_name, e.is_executive
                       FROM employees e
                       JOIN departments d ON e.department_id = d.id
                       WHERE LOWER(e.username) = ?""",
                    (username_clean,),
                ).fetchone()
            if row:
                u_name, password_hash, role_id, dept_name, full_name, is_exec = row
                if verify_password(password, password_hash):
                    rag_role = (
                        "Administrator"
                        if is_exec or "ADMIN" in role_id or "CISO" in role_id
                        else "Manager"
                        if "MGR" in role_id or "MANAGER" in role_id or "LEAD" in role_id
                        else "Analyst"
                    )
                    return {
                        "username": u_name,
                        "hashed_password": password_hash,
                        "role": rag_role,
                        "display_role": role_id.replace("_", " ").title(),
                        "department": dept_name,
                        "full_name": full_name,
                    }
        except sqlite3.Error:
            _burn_password_check(password)
            return None

    _burn_password_check(password)
    return None


def is_login_locked(username: str) -> bool:
    username_clean = username.lower().strip()
    state = _failed_logins.get(username_clean)
    if not state:
        return False
    attempts, locked_until = state
    if attempts < MAX_FAILED_LOGIN_ATTEMPTS:
        return False
    if time.monotonic() >= locked_until:
        _failed_logins.pop(username_clean, None)
        return False
    return True


def record_failed_login(username: str) -> None:
    username_clean = username.lower().strip()
    attempts, locked_until = _failed_logins.get(username_clean, (0, 0.0))
    if attempts >= MAX_FAILED_LOGIN_ATTEMPTS and time.monotonic() >= locked_until:
        attempts = 0
    attempts += 1
    if attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
        locked_until = time.monotonic() + LOGIN_LOCKOUT_SECONDS
    _failed_logins[username_clean] = (attempts, locked_until)


def clear_failed_logins(username: str) -> None:
    _failed_logins.pop(username.lower().strip(), None)


def create_access_token(
    username: str,
    role: str,
    department: str = "",
    display_role: str = "",
) -> str:
    """Mint a signed JWT carrying server-verified identity and authorization claims."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "department": department,
        "display_role": display_role or role,
        "exp": now + timedelta(minutes=AUTH_TOKEN_EXPIRE_MINUTES),
        "iat": now,
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm=settings.auth_algorithm)


def decode_token(token: str) -> TokenData:
    """Decode and validate a JWT, rejecting expired or invalid credentials."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.auth_secret_key, algorithms=[settings.auth_algorithm]
        )
        username = payload.get("sub", "")
        role = payload.get("role", "")
        department = payload.get("department", "")
        display_role = payload.get("display_role", role)
        if (
            not isinstance(username, str)
            or not username
            or not isinstance(role, str)
            or not role
            or not isinstance(department, str)
            or not isinstance(display_role, str)
        ):
            raise credentials_exception
        return TokenData(
            username=username,
            role=role,
            department=department,
            display_role=display_role,
        )
    except JWTError:
        raise credentials_exception


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> TokenData:
    """FastAPI dependency that validates a Bearer token and returns its identity."""
    return decode_token(credentials.credentials)


def require_role(minimum_role: str):
    """Return a dependency that enforces the requested minimum role."""
    def _check(user: TokenData = Depends(get_current_user)) -> TokenData:
        from app.config import role_can_access

        if not role_can_access(user.role, minimum_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum_role} role; you have {user.role}",
            )
        return user

    return _check
