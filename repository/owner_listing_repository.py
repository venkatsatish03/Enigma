"""Persistence for user-owned property listings and their media metadata."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from schemas.booking_request import BookingRequestRecord
from schemas.owner_listing import (
    ListingMediaRecord,
    ListingType,
    OwnerListingDraft,
    OwnerListingRecord,
    PropertyStatus,
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
                CREATE TABLE IF NOT EXISTS booking_requests (
                    request_id TEXT PRIMARY KEY,
                    listing_id TEXT NOT NULL REFERENCES owner_listings(listing_id) ON DELETE CASCADE,
                    tenant_user_id TEXT NOT NULL,
                    tenant_email TEXT NOT NULL,
                    desired_move_in_date TEXT,
                    message TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    decision_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_booking_requests_tenant ON booking_requests(tenant_user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_booking_requests_listing ON booking_requests(listing_id, status, created_at DESC);
                """
            )
            # Listings submitted under the previous review flow are now live.
            conn.execute("UPDATE owner_listings SET status = 'approved' WHERE status = 'submitted'")

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

    def list_published(self) -> list[OwnerListingRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM owner_listings WHERE status = 'approved' ORDER BY updated_at DESC"
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
        records = [self._to_record(row, media_by_listing.get(row["listing_id"], [])) for row in rows]
        return [record for record in records if record.data.property_status == PropertyStatus.AVAILABLE]

    def get_published_media(self, listing_id: str, media_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT m.*, l.data_json FROM listing_media m JOIN owner_listings l ON l.listing_id = m.listing_id
                   WHERE m.listing_id = ? AND m.media_id = ? AND l.status = 'approved'""",
                (listing_id, media_id),
            ).fetchone()
        if not row:
            return None
        data = OwnerListingDraft.model_validate(json.loads(row["data_json"]))
        if data.property_status != PropertyStatus.AVAILABLE:
            return None
        return dict(row)

    def create_booking_request(
        self,
        *,
        tenant_user_id: str,
        tenant_email: str,
        listing_id: str,
        desired_move_in_date: date | None,
        message: str | None,
    ) -> tuple[BookingRequestRecord | None, str | None]:
        now = _now().isoformat()
        request_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            listing = conn.execute(
                "SELECT * FROM owner_listings WHERE listing_id = ? AND status = 'approved'",
                (listing_id,),
            ).fetchone()
            if not listing:
                conn.execute("ROLLBACK")
                return None, "listing_unavailable"
            if listing["owner_user_id"] == tenant_user_id:
                conn.execute("ROLLBACK")
                return None, "cannot_request_own_listing"
            data = OwnerListingDraft.model_validate(json.loads(listing["data_json"]))
            rental_types = {
                ListingType.FOR_RENT,
                ListingType.LONG_TERM_LET,
                ListingType.SHORT_TERM_RENTAL,
                ListingType.ROOM_RENTAL,
                ListingType.COMMERCIAL_LEASE,
            }
            if data.property_status != PropertyStatus.AVAILABLE or data.listing_type not in rental_types:
                conn.execute("ROLLBACK")
                return None, "listing_unavailable"
            pending = conn.execute(
                "SELECT 1 FROM booking_requests WHERE listing_id = ? AND tenant_user_id = ? AND status = 'pending'",
                (listing_id, tenant_user_id),
            ).fetchone()
            if pending:
                conn.execute("ROLLBACK")
                return None, "request_already_pending"
            conn.execute(
                """INSERT INTO booking_requests (
                    request_id, listing_id, tenant_user_id, tenant_email, desired_move_in_date,
                    message, status, decision_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)""",
                (
                    request_id,
                    listing_id,
                    tenant_user_id,
                    tenant_email,
                    desired_move_in_date.isoformat() if desired_move_in_date else None,
                    message.strip() if message and message.strip() else None,
                    now,
                    now,
                ),
            )
            conn.execute("COMMIT")
        return self.get_booking_request_for_tenant(tenant_user_id, request_id), None

    def list_booking_requests_for_tenant(self, tenant_user_id: str) -> list[BookingRequestRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT r.*, l.data_json FROM booking_requests r
                   JOIN owner_listings l ON l.listing_id = r.listing_id
                   WHERE r.tenant_user_id = ? ORDER BY r.created_at DESC""",
                (tenant_user_id,),
            ).fetchall()
        return [self._booking_record(row) for row in rows]

    def get_booking_request_for_tenant(
        self, tenant_user_id: str, request_id: str
    ) -> BookingRequestRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT r.*, l.data_json FROM booking_requests r
                   JOIN owner_listings l ON l.listing_id = r.listing_id
                   WHERE r.tenant_user_id = ? AND r.request_id = ?""",
                (tenant_user_id, request_id),
            ).fetchone()
        return self._booking_record(row) if row else None

    def list_booking_requests_for_owner(self, owner_user_id: str) -> list[BookingRequestRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT r.*, l.data_json FROM booking_requests r
                   JOIN owner_listings l ON l.listing_id = r.listing_id
                   WHERE l.owner_user_id = ? ORDER BY r.created_at DESC""",
                (owner_user_id,),
            ).fetchall()
        return [self._booking_record(row) for row in rows]

    def decide_booking_request(
        self,
        *,
        owner_user_id: str,
        request_id: str,
        status: str,
        decision_note: str | None,
    ) -> tuple[BookingRequestRecord | None, str | None]:
        now = _now().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT r.*, l.data_json FROM booking_requests r
                   JOIN owner_listings l ON l.listing_id = r.listing_id
                   WHERE l.owner_user_id = ? AND r.request_id = ?""",
                (owner_user_id, request_id),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return None, "not_found"
            if row["status"] != "pending":
                conn.execute("ROLLBACK")
                return self._booking_record(row), "already_decided"
            if status == "accepted":
                listing_data = OwnerListingDraft.model_validate(json.loads(row["data_json"]))
                if listing_data.property_status != PropertyStatus.AVAILABLE:
                    conn.execute("ROLLBACK")
                    return self._booking_record(row), "listing_unavailable"
                listing_data = listing_data.model_copy(update={"property_status": PropertyStatus.UNDER_OFFER})
                conn.execute(
                    "UPDATE owner_listings SET data_json = ?, updated_at = ? WHERE listing_id = ? AND owner_user_id = ?",
                    (listing_data.model_dump_json(), now, row["listing_id"], owner_user_id),
                )
                conn.execute(
                    """UPDATE booking_requests SET status = 'declined', decision_note = ?, updated_at = ?
                       WHERE listing_id = ? AND request_id <> ? AND status = 'pending'""",
                    ("The owner accepted another request.", now, row["listing_id"], request_id),
                )
            conn.execute(
                "UPDATE booking_requests SET status = ?, decision_note = ?, updated_at = ? WHERE request_id = ?",
                (status, decision_note.strip() if decision_note and decision_note.strip() else None, now, request_id),
            )
            conn.execute("COMMIT")
        return self.get_booking_request_for_owner(owner_user_id, request_id), None

    def get_booking_request_for_owner(
        self, owner_user_id: str, request_id: str
    ) -> BookingRequestRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT r.*, l.data_json FROM booking_requests r
                   JOIN owner_listings l ON l.listing_id = r.listing_id
                   WHERE l.owner_user_id = ? AND r.request_id = ?""",
                (owner_user_id, request_id),
            ).fetchone()
        return self._booking_record(row) if row else None

    def update_property_status(
        self, owner_user_id: str, listing_id: str, property_status: PropertyStatus
    ) -> OwnerListingRecord | None:
        now = _now().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data_json FROM owner_listings WHERE listing_id = ? AND owner_user_id = ? AND status = 'approved'",
                (listing_id, owner_user_id),
            ).fetchone()
            if not row:
                return None
            data = OwnerListingDraft.model_validate(json.loads(row["data_json"]))
            data = data.model_copy(update={"property_status": property_status})
            conn.execute(
                "UPDATE owner_listings SET data_json = ?, updated_at = ? WHERE listing_id = ? AND owner_user_id = ? AND status = 'approved'",
                (data.model_dump_json(), now, listing_id, owner_user_id),
            )
        return self.get(owner_user_id, listing_id)

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
                "UPDATE owner_listings SET status = 'approved', updated_at = ? WHERE listing_id = ? AND owner_user_id = ? AND status = 'draft'",
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

    @staticmethod
    def _booking_record(row: sqlite3.Row) -> BookingRequestRecord:
        data = OwnerListingDraft.model_validate(json.loads(row["data_json"]))
        address_parts = [data.street_address, data.unit_number]
        property_label = ", ".join(part for part in address_parts if part) or "Property listing"
        amount = data.sale_price if data.listing_type == ListingType.FOR_SALE else data.rent_amount
        return BookingRequestRecord(
            request_id=row["request_id"],
            listing_id=row["listing_id"],
            property_label=property_label,
            city=data.city,
            country=data.country,
            listing_type=data.listing_type.value,
            price_amount=str(amount) if amount is not None else None,
            price_currency=data.price_currency,
            tenant_email=row["tenant_email"],
            owner_email=data.contact_email,
            desired_move_in_date=date.fromisoformat(row["desired_move_in_date"]) if row["desired_move_in_date"] else None,
            message=row["message"],
            status=row["status"],
            decision_note=row["decision_note"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
