"""CLI utility and helper for bootstrapping the initial administrator and property.

Usage:
    python -m tools.auth_bootstrap --email admin@example.com --password YourSecurePassword123!
    python -m tools.auth_bootstrap --email admin@example.com --password YourPass --property-id prop-01 --property-name "Sunset Towers"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from schemas.auth import UserRole
from tools.auth_config import get_auth_config
from tools.auth_crypto import hash_password
from tools.auth_repository import AuthRepository


def bootstrap_admin(
    *,
    email: str,
    password: str,
    property_id: str | None = None,
    property_name: str | None = None,
    role: UserRole | str = UserRole.ADMIN,
    is_superuser: bool = True,
    reset_password: bool = False,
    auth_repo: AuthRepository | None = None,
) -> dict[str, str]:
    """Bootstrap an initial administrator and assign them to an initial property."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid email address is required")
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")

    if auth_repo is None:
        config = get_auth_config()
        auth_repo = AuthRepository(config.auth_db_path)

    existing = auth_repo.get_user_by_email(email)
    password_updated = False
    if existing:
        user = existing[0]
        user_created = False
        if reset_password:
            pwd_hash = hash_password(password)
            auth_repo.update_user_password(user.user_id, pwd_hash)
            password_updated = True
    else:
        pwd_hash = hash_password(password)
        user = auth_repo.create_user(
            email=email,
            password_hash=pwd_hash,
            is_active=True,
            is_superuser=is_superuser,
        )
        user_created = True

    prop_id = property_id or "prop-default"
    prop_name = property_name or "Default Property"

    existing_prop = auth_repo.get_property(prop_id)
    if not existing_prop:
        auth_repo.create_property(name=prop_name, property_id=prop_id)

    existing_membership = auth_repo.get_membership(prop_id, user.user_id)
    if not existing_membership:
        auth_repo.create_membership(
            property_id=prop_id,
            user_id=user.user_id,
            role=role,
        )

    return {
        "user_id": user.user_id,
        "email": user.email,
        "property_id": prop_id,
        "property_name": prop_name,
        "role": str(role),
        "user_created": "yes" if user_created else "already existed",
        "password_updated": "yes" if password_updated else "no",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap initial administrator and property in Enigma."
    )
    parser.add_argument("--email", required=True, help="Administrator email address")
    parser.add_argument("--password", required=True, help="Administrator password (min 8 chars)")
    parser.add_argument("--property-id", default="prop-default", help="Initial property ID")
    parser.add_argument("--property-name", default="Default Property", help="Initial property name")
    parser.add_argument(
        "--role",
        default="admin",
        choices=[r.value for r in UserRole],
        help="Initial property membership role",
    )
    parser.add_argument(
        "--superuser",
        action="store_true",
        default=True,
        help="Mark user as superuser with global admin rights",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        default=False,
        help="Update password if user already exists",
    )

    args = parser.parse_args()

    try:
        result = bootstrap_admin(
            email=args.email,
            password=args.password,
            property_id=args.property_id,
            property_name=args.property_name,
            role=UserRole(args.role),
            is_superuser=args.superuser,
            reset_password=args.reset_password,
        )
        print("Successfully bootstrapped initial administrator:")
        for k, v in result.items():
            print(f"  {k}: {v}")
    except Exception as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
