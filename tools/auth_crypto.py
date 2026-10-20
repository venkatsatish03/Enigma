"""Cryptographic utilities for password hashing and JWT token handling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import argon2
import argon2.exceptions
import jwt


_hasher = argon2.PasswordHasher(
    time_cost=2,
    memory_cost=19456,  # 19 MiB
    parallelism=1,
    hash_len=32,
    salt_len=16,
)


class AuthTokenError(Exception):
    """Base exception for authentication token errors."""


class TokenExpiredError(AuthTokenError):
    """Raised when an access token is expired."""


class InvalidTokenError(AuthTokenError):
    """Raised when an access token is malformed, has invalid signature, or fails validation."""


def hash_password(password: str) -> str:
    """Hash a password using Argon2id without logging or persisting plaintext."""
    if not password:
        raise ValueError("Password cannot be empty")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against an Argon2 hash. Never logs passwords."""
    if not password or not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (
        argon2.exceptions.VerifyMismatchError,
        argon2.exceptions.InvalidHashError,
        argon2.exceptions.VerificationError,
    ):
        return False


def create_access_token(
    *,
    user_id: str,
    email: str,
    secret_key: str,
    expires_delta: timedelta | None = None,
    algorithm: str = "HS256",
    issuer: str | None = None,
    audience: str | None = None,
) -> tuple[str, int]:
    """Create a signed JWT access token. Returns (token, expires_in_seconds)."""
    if not secret_key:
        raise ValueError("secret_key cannot be empty")

    now = datetime.now(timezone.utc)
    delta = expires_delta if expires_delta is not None else timedelta(minutes=60)
    exp = now + delta

    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if issuer:
        payload["iss"] = issuer
    if audience:
        payload["aud"] = audience

    token = jwt.encode(payload, secret_key, algorithm=algorithm)
    expires_in_seconds = max(0, int((exp - now).total_seconds()))
    return token, expires_in_seconds


def decode_access_token(
    token: str,
    secret_key: str,
    *,
    algorithms: list[str] | None = None,
    issuer: str | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    """Decode and validate a JWT access token."""
    if not secret_key:
        raise ValueError("secret_key cannot be empty")
    if not token or not isinstance(token, str):
        raise InvalidTokenError("Missing or invalid token string")

    active_algorithms = algorithms or ["HS256"]
    options: dict[str, Any] = {
        "require": ["exp", "iat", "sub"],
        "verify_signature": True,
        "verify_exp": True,
        "verify_iat": True,
    }
    if not issuer:
        options["verify_iss"] = False
    if not audience:
        options["verify_aud"] = False

    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=active_algorithms,
            issuer=issuer,
            audience=audience,
            options=options,
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("Token has expired") from exc
    except (jwt.InvalidTokenError, jwt.PyJWTError) as exc:
        raise InvalidTokenError(f"Invalid token: {exc}") from exc
