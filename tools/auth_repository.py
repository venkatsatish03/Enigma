"""SQLite repository for users, properties, and property memberships."""

from __future__ import annotations

import sqlite3
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from schemas.auth import Property, PropertyMembership, User, UserRole


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuthRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.database_path,
            timeout=10,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        if self.database_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_superuser INTEGER NOT NULL DEFAULT 0,
                    google_sub TEXT,
                    google_email_verified INTEGER NOT NULL DEFAULT 0,
                    password_setup_required INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    nonce TEXT NOT NULL,
                    code_verifier TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_action_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    purpose TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_auth_action_user ON auth_action_tokens(user_id, purpose);
                CREATE TABLE IF NOT EXISTS auth_exchange_codes (
                    code_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS user_resident_links (
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    property_id TEXT NOT NULL,
                    resident_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, property_id, resident_id),
                    UNIQUE(property_id, resident_id)
                );

                CREATE TABLE IF NOT EXISTS properties (
                    property_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS property_memberships (
                    membership_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(property_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_memberships_user ON property_memberships(user_id);
                CREATE INDEX IF NOT EXISTS idx_memberships_property ON property_memberships(property_id);
                """
            )

            # Upgrade existing auth databases without dropping any accounts.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "google_sub" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")
            if "google_email_verified" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN google_email_verified INTEGER NOT NULL DEFAULT 0")
            if "password_setup_required" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN password_setup_required INTEGER NOT NULL DEFAULT 0")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL")

    # User Management
    def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        is_active: bool = True,
        is_superuser: bool = False,
        user_id: str | None = None,
    ) -> User:
        norm_email = email.strip().lower()
        uid = user_id or str(uuid.uuid4())
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    user_id, email, password_hash, is_active, is_superuser, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    norm_email,
                    password_hash,
                    1 if is_active else 0,
                    1 if is_superuser else 0,
                    now,
                    now,
                ),
            )

        return User(
            user_id=uid,
            email=norm_email,
            is_active=is_active,
            is_superuser=is_superuser,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_user_by_id(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> tuple[User, str] | None:
        norm_email = email.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
                (norm_email,),
            ).fetchone()
        if not row:
            return None
        user = self._row_to_user(row)
        return user, row["password_hash"]

    @staticmethod
    def _token_hash(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def get_user_by_google_sub(self, google_sub: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        return self._row_to_user(row) if row else None

    def create_google_user(self, *, email: str, google_sub: str, password_hash: str) -> User:
        norm_email = email.strip().lower()
        user_id = str(uuid.uuid4())
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO users (
                    user_id, email, password_hash, google_sub, google_email_verified,
                    password_setup_required, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
                (user_id, norm_email, password_hash, google_sub, now, now),
            )
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return self._row_to_user(row)

    def link_google_identity(self, *, user_id: str, google_sub: str) -> bool:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET google_sub = ?, google_email_verified = 1, updated_at = ? WHERE user_id = ? AND (google_sub IS NULL OR google_sub = ?)",
                (google_sub, now, user_id, google_sub),
            )
            return cursor.rowcount > 0

    def create_oauth_state(self, *, state: str, nonce: str, code_verifier: str, intent: str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now.isoformat(),))
            conn.execute(
                "INSERT INTO oauth_states (state, nonce, code_verifier, intent, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (state, nonce, code_verifier, intent, (now + timedelta(minutes=10)).isoformat(), now.isoformat()),
            )

    def consume_oauth_state(self, state: str) -> dict[str, str] | None:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM oauth_states WHERE state = ? AND expires_at > ?", (state, now)).fetchone()
            if not row:
                conn.rollback()
                return None
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            conn.commit()
        return {"nonce": row["nonce"], "code_verifier": row["code_verifier"], "intent": row["intent"]}

    def create_action_token(self, *, user_id: str, purpose: str, lifetime: timedelta) -> str:
        raw_token = secrets.token_urlsafe(32)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO auth_action_tokens (token_hash, user_id, purpose, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (self._token_hash(raw_token), user_id, purpose, (now + lifetime).isoformat(), now.isoformat()),
            )
        return raw_token

    def consume_action_token(self, *, raw_token: str, purpose: str) -> str | None:
        now = _utc_now().isoformat()
        token_hash = self._token_hash(raw_token)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT user_id FROM auth_action_tokens WHERE token_hash = ? AND purpose = ? AND used_at IS NULL AND expires_at > ?",
                (token_hash, purpose, now),
            ).fetchone()
            if not row:
                conn.rollback()
                return None
            conn.execute("UPDATE auth_action_tokens SET used_at = ? WHERE token_hash = ?", (now, token_hash))
            conn.commit()
        return row["user_id"]

    def create_exchange_code(self, *, user_id: str) -> str:
        raw_code = secrets.token_urlsafe(32)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO auth_exchange_codes (code_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (self._token_hash(raw_code), user_id, (now + timedelta(minutes=2)).isoformat(), now.isoformat()),
            )
        return raw_code

    def consume_exchange_code(self, raw_code: str) -> str | None:
        now = _utc_now().isoformat()
        code_hash = self._token_hash(raw_code)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT user_id FROM auth_exchange_codes WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
                (code_hash, now),
            ).fetchone()
            if not row:
                conn.rollback()
                return None
            conn.execute("UPDATE auth_exchange_codes SET used_at = ? WHERE code_hash = ?", (now, code_hash))
            conn.commit()
        return row["user_id"]

    def link_resident(self, *, user_id: str, property_id: str, resident_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_resident_links (user_id, property_id, resident_id, created_at) VALUES (?, ?, ?, ?)",
                (user_id, property_id, resident_id, _utc_now().isoformat()),
            )

    def list_resident_links(self, user_id: str) -> list[tuple[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT property_id, resident_id FROM user_resident_links WHERE user_id = ? ORDER BY property_id, resident_id",
                (user_id,),
            ).fetchall()
        return [(row["property_id"], row["resident_id"]) for row in rows]

    def set_user_active(self, user_id: str, is_active: bool) -> bool:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE user_id = ?",
                (1 if is_active else 0, now, user_id),
            )
            return cursor.rowcount > 0

    def update_user_password(self, user_id: str, password_hash: str) -> bool:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ?, password_setup_required = 0, updated_at = ? WHERE user_id = ?",
                (password_hash, now, user_id),
            )
            return cursor.rowcount > 0

    # Property Management
    def create_property(
        self,
        *,
        name: str,
        property_id: str | None = None,
    ) -> Property:
        pid = property_id or str(uuid.uuid4())
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO properties (property_id, name, created_at) VALUES (?, ?, ?)",
                (pid, name.strip(), now),
            )

        return Property(
            property_id=pid,
            name=name.strip(),
            created_at=datetime.fromisoformat(now),
        )

    def get_property(self, property_id: str) -> Property | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM properties WHERE property_id = ?",
                (property_id,),
            ).fetchone()
        if not row:
            return None
        return Property(
            property_id=row["property_id"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_properties(self) -> list[Property]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM properties ORDER BY name ASC"
            ).fetchall()
        return [
            Property(
                property_id=row["property_id"],
                name=row["name"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    # Membership Management
    def create_membership(
        self,
        *,
        property_id: str,
        user_id: str,
        role: UserRole | str,
        membership_id: str | None = None,
    ) -> PropertyMembership:
        mid = membership_id or str(uuid.uuid4())
        role_str = role.value if isinstance(role, UserRole) else str(role).strip().lower()
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO property_memberships (
                    membership_id, property_id, user_id, role, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (mid, property_id, user_id, role_str, now),
            )

        return PropertyMembership(
            membership_id=mid,
            property_id=property_id,
            user_id=user_id,
            role=UserRole(role_str),
            created_at=datetime.fromisoformat(now),
        )

    def get_membership(self, property_id: str, user_id: str) -> PropertyMembership | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM property_memberships
                WHERE property_id = ? AND user_id = ?
                """,
                (property_id, user_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_membership(row)

    def list_memberships_for_user(self, user_id: str) -> list[PropertyMembership]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM property_memberships
                WHERE user_id = ?
                ORDER BY created_at ASC
                """,
                (user_id,),
            ).fetchall()
        return [self._row_to_membership(row) for row in rows]

    def list_memberships_for_property(self, property_id: str) -> list[PropertyMembership]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM property_memberships
                WHERE property_id = ?
                ORDER BY created_at ASC
                """,
                (property_id,),
            ).fetchall()
        return [self._row_to_membership(row) for row in rows]

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            user_id=row["user_id"],
            email=row["email"],
            is_active=bool(row["is_active"]),
            is_superuser=bool(row["is_superuser"]),
            google_sub=row["google_sub"],
            google_email_verified=bool(row["google_email_verified"]),
            password_setup_required=bool(row["password_setup_required"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_membership(row: sqlite3.Row) -> PropertyMembership:
        return PropertyMembership(
            membership_id=row["membership_id"],
            property_id=row["property_id"],
            user_id=row["user_id"],
            role=UserRole(row["role"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
