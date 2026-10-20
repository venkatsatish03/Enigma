
"""Persistent approval workflow with authorization and audit events."""

from __future__ import annotations

import json
import sqlite3
import uuid

from datetime import datetime, timezone
from pathlib import Path

from schemas.approvals import (
    ApprovalDecision,
    ApprovalDecisionResult,
    ApprovalRequest,
    ApprovalRequestCreate,
)
from schemas.audit import AuditEventCreate
from tools.audit_trail import AuditTrail


class ApprovalWorkflowError(Exception):
    """Base exception for approval workflow errors."""


class ApprovalNotFoundError(ApprovalWorkflowError):
    pass


class ApprovalAuthorizationError(ApprovalWorkflowError):
    pass


class ApprovalStateError(ApprovalWorkflowError):
    pass


class ApprovalWorkflow:
    def __init__(
        self,
        database_path: str | Path,
        audit_trail: AuditTrail,
    ):
        self.database_path = str(database_path)
        self.audit_trail = audit_trail

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
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    approval_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    approver_ids_json TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    amount TEXT,
                    currency TEXT,
                    evidence_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    decision_reason TEXT,
                    correlation_id TEXT,
                    property_id TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_approval_action
                    ON approval_requests(action_id);

                CREATE INDEX IF NOT EXISTS idx_approval_status
                    ON approval_requests(status);
                """
            )
            cursor = connection.execute("PRAGMA table_info(approval_requests)")
            cols = [row["name"] for row in cursor.fetchall()]
            if "property_id" not in cols:
                connection.execute(
                    "ALTER TABLE approval_requests ADD COLUMN property_id TEXT"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_property ON approval_requests(property_id)"
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Datetime values must include a timezone")
        return value.astimezone(timezone.utc)

    def create_request(
        self,
        request: ApprovalRequestCreate,
    ) -> ApprovalRequest:
        now = self._now()
        expires_at = self._normalize_datetime(request.expires_at)

        if expires_at <= now:
            raise ValueError("expires_at must be in the future")

        approver_ids = list(dict.fromkeys(
            approver.strip()
            for approver in request.approver_ids
            if approver.strip()
        ))
        if not approver_ids:
            raise ValueError("At least one valid approver is required")

        if request.requested_by in approver_ids:
            raise ValueError(
                "The requester cannot be an approver for their own request"
            )

        approval_id = str(uuid.uuid4())
        created_at = now
        currency = request.currency.upper() if request.currency else None

        approval = ApprovalRequest(
            approval_id=approval_id,
            action_id=request.action_id,
            action_type=request.action_type,
            requested_by=request.requested_by,
            approver_ids=approver_ids,
            rationale=request.rationale,
            amount=request.amount,
            currency=currency,
            evidence_ids=request.evidence_ids,
            status="pending",
            created_at=created_at,
            expires_at=expires_at,
            correlation_id=request.correlation_id,
            property_id=request.property_id,
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO approval_requests (
                    approval_id, action_id, action_type, requested_by,
                    approver_ids_json, rationale, amount, currency,
                    evidence_ids_json, status, created_at, expires_at,
                    correlation_id, property_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.action_id,
                    approval.action_type,
                    approval.requested_by,
                    json.dumps(approval.approver_ids),
                    approval.rationale,
                    approval.amount,
                    approval.currency,
                    json.dumps(approval.evidence_ids),
                    approval.status,
                    approval.created_at.isoformat(),
                    approval.expires_at.isoformat(),
                    approval.correlation_id,
                    approval.property_id,
                ),
            )
            connection.commit()

        self.audit_trail.append(
            AuditEventCreate(
                actor_id=request.requested_by,
                actor_type="user_or_agent",
                event_type="approval.requested",
                entity_type="approval",
                entity_id=approval_id,
                payload={
                    "action_id": request.action_id,
                    "action_type": request.action_type,
                    "approver_ids": approver_ids,
                    "status": "pending",
                    "expires_at": expires_at.isoformat(),
                    "property_id": request.property_id,
                },
                evidence_ids=request.evidence_ids,
                correlation_id=request.correlation_id,
                property_id=request.property_id,
            )
        )

        return approval

    def get_request(self, approval_id: str) -> ApprovalRequest:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()

        if row is None:
            raise ApprovalNotFoundError(
                f"Approval request {approval_id} was not found"
            )

        approval = self._row_to_request(row)

        # Persist expiry when a pending request is first observed after expiry.
        if approval.status == "pending" and self._now() >= approval.expires_at:
            return self._expire_request(approval)

        return approval

    def decide(self, decision: ApprovalDecision) -> ApprovalDecisionResult:
        now = self._now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE approval_id = ?",
                (decision.approval_id,),
            ).fetchone()

            if row is None:
                connection.rollback()
                raise ApprovalNotFoundError(
                    f"Approval request {decision.approval_id} was not found"
                )

            approval = self._row_to_request(row)

            if decision.approver_id not in approval.approver_ids:
                connection.rollback()
                raise ApprovalAuthorizationError(
                    "This user is not an authorized approver for this request"
                )

            if decision.approver_id == approval.requested_by:
                connection.rollback()
                raise ApprovalAuthorizationError(
                    "The requester cannot approve or reject their own request"
                )

            if approval.status != "pending":
                connection.rollback()
                raise ApprovalStateError(
                    f"Cannot decide a request with status '{approval.status}'"
                )

            if now >= approval.expires_at:
                connection.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'expired'
                    WHERE approval_id = ? AND status = 'pending'
                    """,
                    (approval.approval_id,),
                )
                connection.commit()
                expired = self.get_request(approval.approval_id)
                self.audit_trail.append(
                    AuditEventCreate(
                        actor_id="approval-system",
                        actor_type="system",
                        event_type="approval.expired",
                        entity_type="approval",
                        entity_id=approval.approval_id,
                        payload={"status": "expired", "property_id": approval.property_id},
                        evidence_ids=approval.evidence_ids,
                        correlation_id=approval.correlation_id,
                        property_id=approval.property_id,
                    )
                )
                return ApprovalDecisionResult(
                    approval=expired,
                    message="Approval request expired before the decision.",
                )

            new_status = (
                "approved" if decision.decision == "approve" else "rejected"
            )
            connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, decided_at = ?, decided_by = ?,
                    decision_reason = ?
                WHERE approval_id = ? AND status = 'pending'
                """,
                (
                    new_status,
                    now.isoformat(),
                    decision.approver_id,
                    decision.reason,
                    approval.approval_id,
                ),
            )
            connection.commit()

        updated = self.get_request(decision.approval_id)

        self.audit_trail.append(
            AuditEventCreate(
                actor_id=decision.approver_id,
                actor_type="user",
                event_type=f"approval.{new_status}",
                entity_type="approval",
                entity_id=approval.approval_id,
                payload={
                    "action_id": approval.action_id,
                    "status": new_status,
                    "reason": decision.reason,
                    "property_id": approval.property_id,
                },
                evidence_ids=approval.evidence_ids,
                correlation_id=approval.correlation_id,
                property_id=approval.property_id,
            )
        )

        return ApprovalDecisionResult(
            approval=updated,
            message=f"Approval request {new_status}.",
        )

    def list_requests(
        self,
        *,
        status: str | None = None,
        action_id: str | None = None,
        property_id: str | None = None,
        limit: int = 100,
    ) -> list[ApprovalRequest]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")

        clauses: list[str] = []
        params: list[object] = []

        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if action_id is not None:
            clauses.append("action_id = ?")
            params.append(action_id)
        if property_id is not None:
            clauses.append("property_id = ?")
            params.append(property_id)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT approval_id FROM approval_requests
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        return [
            self.get_request(row["approval_id"])
            for row in rows
        ]

    def _expire_request(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE approval_requests
                SET status = 'expired'
                WHERE approval_id = ? AND status = 'pending'
                """,
                (approval.approval_id,),
            )
            connection.commit()

        expired = self._read_request(approval.approval_id)
        self.audit_trail.append(
            AuditEventCreate(
                actor_id="approval-system",
                actor_type="system",
                event_type="approval.expired",
                entity_type="approval",
                entity_id=approval.approval_id,
                payload={"status": "expired", "property_id": approval.property_id},
                evidence_ids=approval.evidence_ids,
                correlation_id=approval.correlation_id,
                property_id=approval.property_id,
            )
        )
        return expired

    def _read_request(self, approval_id: str) -> ApprovalRequest:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval request {approval_id} was not found"
            )
        return self._row_to_request(row)

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=row["approval_id"],
            action_id=row["action_id"],
            action_type=row["action_type"],
            requested_by=row["requested_by"],
            approver_ids=json.loads(row["approver_ids_json"]),
            rationale=row["rationale"],
            amount=row["amount"],
            currency=row["currency"],
            evidence_ids=json.loads(row["evidence_ids_json"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            decided_at=(
                datetime.fromisoformat(row["decided_at"])
                if row["decided_at"]
                else None
            ),
            decided_by=row["decided_by"],
            decision_reason=row["decision_reason"],
            correlation_id=row["correlation_id"],
            property_id=(
                row["property_id"] if "property_id" in row.keys() else None
            ),
        )
