
"""SQLite-backed append-only audit trail with hash-chain verification."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from schemas.audit import (
    AuditEvent,
    AuditEventCreate,
    AuditVerificationResult,
)


GENESIS_HASH = "0" * 64


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> str:
    """Serialize JSON consistently for hashing and persistence."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _hash_event(event_data: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json(event_data).encode("utf-8")
    ).hexdigest()


class AuditTrail:
    """Persistent audit repository.

    API operations only append events. SQLite triggers prevent ordinary
    UPDATE and DELETE operations against the audit_events table.

    Hash-chain verification detects changes to stored event content or order.
    This is tamper-evident, not protection against an administrator who can
    replace the database and all its hashes.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(
                parents=True, exist_ok=True
            )
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        if self.database_path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    correlation_id TEXT,
                    property_id TEXT,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );

                CREATE INDEX IF NOT EXISTS idx_audit_entity
                    ON audit_events(entity_type, entity_id);

                CREATE INDEX IF NOT EXISTS idx_audit_correlation
                    ON audit_events(correlation_id);

                CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                BEFORE UPDATE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'Audit events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                BEFORE DELETE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'Audit events are append-only');
                END;
                """
            )
            cursor = connection.execute("PRAGMA table_info(audit_events)")
            cols = [row["name"] for row in cursor.fetchall()]
            if "property_id" not in cols:
                connection.execute("ALTER TABLE audit_events ADD COLUMN property_id TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_property ON audit_events(property_id)"
            )

    def append(self, event: AuditEventCreate) -> AuditEvent:
        """Append one event and return the persisted event."""
        event_id = str(uuid.uuid4())
        created_at = _utc_now().isoformat()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            previous = connection.execute(
                """
                SELECT sequence_number, event_hash
                FROM audit_events
                ORDER BY sequence_number DESC
                LIMIT 1
                """
            ).fetchone()

            previous_hash = (
                previous["event_hash"] if previous else GENESIS_HASH
            )

            payload_json = _canonical_json(event.payload)
            evidence_json = _canonical_json(event.evidence_ids)

            hash_data = {
                "event_id": event_id,
                "created_at": created_at,
                "actor_id": event.actor_id,
                "actor_type": event.actor_type,
                "event_type": event.event_type,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "payload_json": payload_json,
                "evidence_ids_json": evidence_json,
                "correlation_id": event.correlation_id,
                "previous_hash": previous_hash,
            }
            event_hash = _hash_event(hash_data)

            property_id = event.property_id or event.payload.get("property_id")

            cursor = connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, created_at, actor_id, actor_type,
                    event_type, entity_type, entity_id, payload_json,
                    evidence_ids_json, correlation_id, property_id, previous_hash,
                    event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    created_at,
                    event.actor_id,
                    event.actor_type,
                    event.event_type,
                    event.entity_type,
                    event.entity_id,
                    payload_json,
                    evidence_json,
                    event.correlation_id,
                    property_id,
                    previous_hash,
                    event_hash,
                ),
            )
            sequence_number = cursor.lastrowid
            connection.commit()

        return AuditEvent(
            event_id=event_id,
            sequence_number=sequence_number,
            created_at=datetime.fromisoformat(created_at),
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            event_type=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            payload=event.payload,
            evidence_ids=event.evidence_ids,
            correlation_id=event.correlation_id,
            property_id=property_id,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )

    def list_events(
        self,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        correlation_id: str | None = None,
        property_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Read events in chronological sequence, with optional filters."""
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")

        clauses: list[str] = []
        parameters: list[Any] = []

        if entity_type is not None:
            clauses.append("entity_type = ?")
            parameters.append(entity_type)
        if entity_id is not None:
            clauses.append("entity_id = ?")
            parameters.append(entity_id)
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            parameters.append(correlation_id)
        if property_id is not None:
            clauses.append("property_id = ?")
            parameters.append(property_id)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM (
                    SELECT * FROM audit_events
                    {where_sql}
                    ORDER BY sequence_number DESC
                    LIMIT ?
                )
                ORDER BY sequence_number ASC
                """,
                [*parameters, limit],
            ).fetchall()

        return [self._row_to_event(row) for row in rows]

    def verify_chain(self) -> AuditVerificationResult:
        """Verify the complete chain in sequence order."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY sequence_number ASC"
            ).fetchall()

        expected_previous_hash = GENESIS_HASH

        for row in rows:
            if row["previous_hash"] != expected_previous_hash:
                return AuditVerificationResult(
                    valid=False,
                    events_checked=row["sequence_number"] - 1,
                    first_invalid_sequence=row["sequence_number"],
                    message="Previous-hash link does not match.",
                )

            hash_data = {
                "event_id": row["event_id"],
                "created_at": row["created_at"],
                "actor_id": row["actor_id"],
                "actor_type": row["actor_type"],
                "event_type": row["event_type"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "payload_json": row["payload_json"],
                "evidence_ids_json": row["evidence_ids_json"],
                "correlation_id": row["correlation_id"],
                "previous_hash": row["previous_hash"],
            }

            if _hash_event(hash_data) != row["event_hash"]:
                return AuditVerificationResult(
                    valid=False,
                    events_checked=row["sequence_number"] - 1,
                    first_invalid_sequence=row["sequence_number"],
                    message="Event content hash does not match.",
                )

            expected_previous_hash = row["event_hash"]

        return AuditVerificationResult(
            valid=True,
            events_checked=len(rows),
            first_invalid_sequence=None,
            message="Audit hash chain is valid.",
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            event_id=row["event_id"],
            sequence_number=row["sequence_number"],
            created_at=datetime.fromisoformat(row["created_at"]),
            actor_id=row["actor_id"],
            actor_type=row["actor_type"],
            event_type=row["event_type"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            payload=json.loads(row["payload_json"]),
            evidence_ids=json.loads(row["evidence_ids_json"]),
            correlation_id=row["correlation_id"],
            property_id=(
                row["property_id"] if "property_id" in row.keys() else None
            ),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )
