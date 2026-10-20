"""Security and authentication configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if present at project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class ConfigurationError(Exception):
    """Raised when security configuration is missing or invalid."""


@dataclass(frozen=True)
class AuthConfig:
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    issuer: str | None = None
    audience: str | None = None
    auth_db_path: Path = PROJECT_ROOT / "data" / "auth.sqlite3"
    domain_db_path: Path = PROJECT_ROOT / "data" / "domain.sqlite3"
    workflows_db_path: Path = PROJECT_ROOT / "data" / "workflows.sqlite3"


def get_auth_config() -> AuthConfig:
    """Load authentication configuration from environment.
    
    Fails safely without fallback to insecure default secrets.
    """
    secret_key = (
        os.getenv("ENIGMA_JWT_SECRET_KEY")
        or os.getenv("JWT_SECRET_KEY")
        or ""
    ).strip()

    if not secret_key:
        raise ConfigurationError(
            "ENIGMA_JWT_SECRET_KEY environment variable is missing. "
            "Configure a secure random secret key in your environment or .env file."
        )

    algorithm = (
        os.getenv("ENIGMA_JWT_ALGORITHM")
        or os.getenv("JWT_ALGORITHM")
        or "HS256"
    ).strip()

    try:
        expire_minutes = int(
            os.getenv("ENIGMA_JWT_EXPIRE_MINUTES")
            or os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
            or "60"
        )
    except ValueError as exc:
        raise ConfigurationError("ENIGMA_JWT_EXPIRE_MINUTES must be an integer") from exc

    issuer = (
        os.getenv("ENIGMA_JWT_ISSUER") or os.getenv("JWT_ISSUER") or None
    )
    if issuer:
        issuer = issuer.strip()

    audience = (
        os.getenv("ENIGMA_JWT_AUDIENCE") or os.getenv("JWT_AUDIENCE") or None
    )
    if audience:
        audience = audience.strip()

    raw_db_path = (
        os.getenv("ENIGMA_AUTH_DB")
        or os.getenv("AUTH_DB_PATH")
        or "data/auth.sqlite3"
    ).strip()
    db_path = Path(raw_db_path)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    raw_domain_db_path = (
        os.getenv("ENIGMA_DOMAIN_DB")
        or os.getenv("DOMAIN_DB_PATH")
        or "data/domain.sqlite3"
    ).strip()
    domain_db_path = Path(raw_domain_db_path)
    if not domain_db_path.is_absolute():
        domain_db_path = PROJECT_ROOT / domain_db_path

    raw_workflows_db_path = (
        os.getenv("ENIGMA_WORKFLOWS_DB")
        or os.getenv("WORKFLOWS_DB_PATH")
        or "data/workflows.sqlite3"
    ).strip()
    workflows_db_path = Path(raw_workflows_db_path)
    if not workflows_db_path.is_absolute():
        workflows_db_path = PROJECT_ROOT / workflows_db_path

    return AuthConfig(
        secret_key=secret_key,
        algorithm=algorithm,
        access_token_expire_minutes=expire_minutes,
        issuer=issuer,
        audience=audience,
        auth_db_path=db_path,
        domain_db_path=domain_db_path,
        workflows_db_path=workflows_db_path,
    )
