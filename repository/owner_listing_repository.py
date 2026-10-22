"""Persistence for user-owned property listings and their media metadata."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from schemas.owner_listing import (
    ListingMediaRecord,
    OwnerListingDraft,
    OwnerListingRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OwnerListingRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
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
                CREATE TABLE IF NOT EXISTS owner_listings (
                    listing_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_owner_listings_user ON owner_listings(owner_user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS listing_media (
                    media_id TEXT PRIMARY KEY,
                    listing_id TEXT NOT NULL REFERENCES owner_listings(listing_id) ON DELETE CASCADE,
                    owner_user_id TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    storage_name TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_listing_media_listing ON listing_media(listing_id, created_at);
                """
            )

    def create(self, owner_user_id: str, data: OwnerListingDraft) -> OwnerListingRecord:
        listing_id = str(uuid.uuid4())
        now = _now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO owner_listings (listing_id, owner_user_id, status, data_json, created_at, updated_at) VALUES (?, ?, 'draft', ?, ?, ?)",
                (listing_id, owner_user_id, data.model_dump_json(), now, now),
            )
        return self.get(owner_user_id, listing_id)  # type: ignore[return-value]

    def get(self, owner_user_id: str, listing_id: str) -> OwnerListingRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM owner_listings WHERE listing_id = ? AND owner_user_id = ?",
                (listing_id, owner_user_id),
            ).fetchone()
            if not row:
                return None
            media_rows = conn.execute(
                "SELECT * FROM listing_media WHERE listing_id = ? ORDER BY created_at, media_id",
                (listing_id,),
            ).fetchall()
        return self._to_record(row, media_rows)

    def list_for_owner(self, owner_user_id: str) -> list[OwnerListingRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM owner_listings WHERE owner_user_id = ? ORDER BY updated_at DESC",
                (owner_user_id,),
            ).fetchall()
            media_by_listing: dict[str, list[sqlite3.Row]] = {}
            if rows:
                ids = [row["listing_id"] for row in rows]
                placeholders = ",".join("?" for _ in ids)
                media_rows = conn.execute(
                    f"SELECT * FROM listing_media WHERE listing_id IN ({placeholders}) ORDER BY created_at, media_id",
                    ids,
                ).fetchall()
                for media in media_rows:
                    media_by_listing.setdefault(media["listing_id"], []).append(media)
        return [self._to_record(row, media_by_listing.get(row["listing_id"], [])) for row in rows]

    def update(self, owner_user_id: str, listing_id: str, data: OwnerListingDraft) -> OwnerListingRecord | None:
        now = _now().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE owner_listings SET data_json = ?, updated_at = ? WHERE listing_id = ? AND owner_user_id = ? AND status = 'draft'",
                (data.model_dump_json(), now, listing_id, owner_user_id),
            )
            if not cursor.rowcount:
                return None
        return self.get(owner_user_id, listing_id)

    def submit(self, owner_user_id: str, listing_id: str) -> tuple[OwnerListingRecord | None, list[str]]:
        listing = self.get(owner_user_id, listing_id)
        if not listing:
            return None, []
        errors = listing.data.submission_errors()
        if errors:
            return listing, errors
        now = _now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE owner_listings SET status = 'submitted', updated_at = ? WHERE listing_id = ? AND owner_user_id = ? AND status = 'draft'",
                (now, listing_id, owner_user_id),
            )
        return self.get(owner_user_id, listing_id), []

    def add_media(
        self,
        *,
        owner_user_id: str,
        listing_id: str,
        original_filename: str,
        storage_name: str,
        media_kind: str,
        mime_type: str,
        size_bytes: int,
    ) -> ListingMediaRecord | None:
        media_id = str(uuid.uuid4())
        now = _now().isoformat()
        with self._connect() as conn:
            owns_listing = conn.execute(
                "SELECT 1 FROM owner_listings WHERE listing_id = ? AND owner_user_id = ?",
                (listing_id, owner_user_id),
            ).fetchone()
            if not owns_listing:
                return None
            conn.execute(
                "INSERT INTO listing_media (media_id, listing_id, owner_user_id, original_filename, storage_name, media_kind, mime_type, size_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (media_id, listing_id, owner_user_id, original_filename, storage_name, media_kind, mime_type, size_bytes, now),
            )
        return ListingMediaRecord(
            media_id=media_id,
            listing_id=listing_id,
            original_filename=original_filename,
            media_kind=media_kind,
            mime_type=mime_type,
            size_bytes=size_bytes,
            created_at=datetime.fromisoformat(now),
        )

    def get_media(self, owner_user_id: str, listing_id: str, media_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM listing_media WHERE owner_user_id = ? AND listing_id = ? AND media_id = ?",
                (owner_user_id, listing_id, media_id),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _to_record(row: sqlite3.Row, media_rows: list[sqlite3.Row]) -> OwnerListingRecord:
        return OwnerListingRecord(
            listing_id=row["listing_id"],
            owner_user_id=row["owner_user_id"],
            status=row["status"],
            data=OwnerListingDraft.model_validate(json.loads(row["data_json"])),
            media=[
                ListingMediaRecord(
                    media_id=item["media_id"],
                    listing_id=item["listing_id"],
                    original_filename=item["original_filename"],
                    media_kind=item["media_kind"],
                    mime_type=item["mime_type"],
                    size_bytes=item["size_bytes"],
                    created_at=datetime.fromisoformat(item["created_at"]),
                )
                for item in media_rows
            ],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
