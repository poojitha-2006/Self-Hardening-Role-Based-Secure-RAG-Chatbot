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
from passlib.context import CryptContext

from app.config import settings
from app.schemas import TokenData

# ── Password hashing ──────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Seeded user database (demo only — in prod, use a real DB) ─────────────────
# Passwords are "password" for all demo users.
_HASHED_PASSWORD = pwd_context.hash("password")

USER_DB: dict[str, dict] = {
    "alice": {
        "username": "alice",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Administrator",
        "full_name": "Alice Admin",
    },
    "bob": {
        "username": "bob",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Manager",
        "full_name": "Bob Manager",
    },
    "carol": {
        "username": "carol",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Analyst",
        "full_name": "Carol Analyst",
    },
    # Additional test users
    "dave": {
        "username": "dave",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Analyst",
        "full_name": "Dave Analyst",
    },
    "eve": {
        "username": "eve",
        "hashed_password": _HASHED_PASSWORD,
        "role": "Manager",
        "full_name": "Eve Manager",
    },
}

# ── Bearer token extractor ────────────────────────────────────────────────────
_bearer_scheme = HTTPBearer(auto_error=True)


# ── Core auth functions ───────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Return user dict if credentials are valid, else None."""
    user = USER_DB.get(username.lower())
    if not user:
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    return user


def create_access_token(username: str, role: str) -> str:
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
        return TokenData(username=username, role=role)
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
