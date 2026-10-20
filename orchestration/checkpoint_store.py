"""SQLite-backed durable checkpoint store for workflows and step state."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestration.exceptions import (
    ConcurrentExecutionError,
    WorkflowNotFoundError,
)
from orchestration.schemas import (
    StepName,
    StepStatus,
    WorkflowStepRecord,
    WorkflowStatus,
    WorkflowSummary,
    WorkflowType,
)
from orchestration.state import WorkflowState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SqliteCheckpointStore:
    """Manages transactional persistence of workflows, steps, and worker locks."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path) if isinstance(database_path, str) else database_path
        if str(self.database_path) != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.database_path),
            timeout=10,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        if str(self.database_path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL,
                    initiated_by TEXT NOT NULL,
                    workflow_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step TEXT,
                    input_reference TEXT NOT NULL,
                    approval_request_id TEXT,
                    correlation_id TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    state_json TEXT NOT NULL,
                    worker_lock TEXT,
                    lock_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_workflows_property ON workflows(property_id);
                CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);
                CREATE INDEX IF NOT EXISTS idx_workflows_correlation ON workflows(correlation_id);

                CREATE TABLE IF NOT EXISTS workflow_steps (
                    step_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                    step_name TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    output_summary TEXT,
                    UNIQUE(workflow_id, step_name)
                );
                CREATE INDEX IF NOT EXISTS idx_steps_workflow ON workflow_steps(workflow_id);
                """
            )

    def save_workflow(self, state: WorkflowState) -> WorkflowState:
        """Atomically insert or update workflow state and metadata."""
        now = _utc_now().isoformat()
        state.updated_at = datetime.fromisoformat(now)
        state_json = state.to_json()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflows (
                    workflow_id, property_id, initiated_by, workflow_type, status,
                    current_step, input_reference, approval_request_id, correlation_id,
                    retry_count, state_json, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id) DO UPDATE SET
                    status = excluded.status,
                    current_step = excluded.current_step,
                    approval_request_id = excluded.approval_request_id,
                    retry_count = excluded.retry_count,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at,
                    completed_at = excluded.completed_at
                """,
                (
                    state.workflow_id,
                    state.property_id,
                    state.initiated_by,
                    state.workflow_type.value,
                    state.status.value,
                    state.current_step.value if state.current_step else None,
                    state.input_reference,
                    state.approval_request_id,
                    state.correlation_id,
                    state.retry_count,
                    state_json,
                    state.created_at.isoformat(),
                    now,
                    state.completed_at.isoformat() if state.completed_at else None,
                ),
            )
        return state

    def get_workflow(self, property_id: str, workflow_id: str) -> WorkflowState | None:
        """Retrieve workflow strictly within authorized property scope."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM workflows WHERE workflow_id = ? AND property_id = ?",
                (workflow_id, property_id),
            ).fetchone()
        if not row:
            return None
        return WorkflowState.from_json(row["state_json"])

    def get_workflow_by_id(self, workflow_id: str) -> WorkflowState | None:
        """Internal retrieval without property filter (e.g. for worker claim resolution)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        if not row:
            return None
        return WorkflowState.from_json(row["state_json"])

    def list_workflows(
        self,
        property_id: str,
        status: WorkflowStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowSummary]:
        """List workflows for a property with pagination and optional status filter."""
        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT state_json FROM workflows
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        summaries: list[WorkflowSummary] = []
        for r in rows:
            st = WorkflowState.from_json(r["state_json"])
            summaries.append(st.to_summary())
        return summaries

    def record_step(self, step: WorkflowStepRecord) -> WorkflowStepRecord:
        """Insert or update step execution record."""
        sid = step.step_id or str(uuid.uuid4())
        started_iso = step.started_at.isoformat() if step.started_at else None
        completed_iso = step.completed_at.isoformat() if step.completed_at else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_steps (
                    step_id, workflow_id, step_name, step_index, status,
                    started_at, completed_at, retry_count, error_message, output_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id, step_name) DO UPDATE SET
                    status = excluded.status,
                    started_at = COALESCE(excluded.started_at, workflow_steps.started_at),
                    completed_at = excluded.completed_at,
                    retry_count = excluded.retry_count,
                    error_message = excluded.error_message,
                    output_summary = excluded.output_summary
                """,
                (
                    sid,
                    step.workflow_id,
                    step.step_name.value,
                    step.step_index,
                    step.status.value,
                    started_iso,
                    completed_iso,
                    step.retry_count,
                    step.error_message,
                    step.output_summary,
                ),
            )
        step.step_id = sid
        return step

    def get_step(self, workflow_id: str, step_name: StepName) -> WorkflowStepRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM workflow_steps
                WHERE workflow_id = ? AND step_name = ?
                """,
                (workflow_id, step_name.value),
            ).fetchone()
        if not row:
            return None
        return WorkflowStepRecord(
            step_id=row["step_id"],
            workflow_id=row["workflow_id"],
            step_name=StepName(row["step_name"]),
            step_index=row["step_index"],
            status=StepStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            retry_count=row["retry_count"],
            error_message=row["error_message"],
            output_summary=row["output_summary"],
        )

    def list_steps(self, workflow_id: str) -> list[WorkflowStepRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workflow_steps
                WHERE workflow_id = ?
                ORDER BY step_index ASC
                """,
                (workflow_id,),
            ).fetchall()

        return [
            WorkflowStepRecord(
                step_id=row["step_id"],
                workflow_id=row["workflow_id"],
                step_name=StepName(row["step_name"]),
                step_index=row["step_index"],
                status=StepStatus(row["status"]),
                started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
                completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                retry_count=row["retry_count"],
                error_message=row["error_message"],
                output_summary=row["output_summary"],
            )
            for row in rows
        ]

    def claim_step(
        self,
        workflow_id: str,
        worker_id: str,
        lock_ttl_seconds: int = 60,
    ) -> bool:
        """Atomically claim execution lock for a workflow to prevent concurrent race conditions."""
        now = _utc_now()
        expires_at = (now + timedelta(seconds=lock_ttl_seconds)).isoformat()
        now_iso = now.isoformat()

        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workflows
                SET worker_lock = ?, lock_expires_at = ?
                WHERE workflow_id = ?
                  AND (worker_lock IS NULL OR lock_expires_at < ? OR worker_lock = ?)
                """,
                (worker_id, expires_at, workflow_id, now_iso, worker_id),
            )
            return cursor.rowcount > 0

    def release_claim(self, workflow_id: str, worker_id: str) -> None:
        """Release the worker lock if held by worker_id."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflows
                SET worker_lock = NULL, lock_expires_at = NULL
                WHERE workflow_id = ? AND worker_lock = ?
                """,
                (workflow_id, worker_id),
            )
