"""Typed workflow state for durable execution, checkpointing, and recovery."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orchestration.schemas import (
    StepName,
    WorkflowStatus,
    WorkflowSummary,
    WorkflowType,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workflow_id: str
    property_id: str
    initiated_by: str
    workflow_type: WorkflowType
    status: WorkflowStatus = WorkflowStatus.CREATED
    current_step: StepName | None = None
    input_reference: str
    document_result: dict[str, Any] | None = None
    financial_result: dict[str, Any] | None = None
    maintenance_result: dict[str, Any] | None = None
    validation_result: dict[str, Any] | None = None
    approval_request_id: str | None = None
    action_result: dict[str, Any] | None = None
    verification_result: dict[str, Any] | None = None
    allocation_instructions: list[dict[str, Any]] | None = None
    errors: list[str] = Field(default_factory=list)
    correlation_id: str
    retry_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None

    def to_summary(self) -> WorkflowSummary:
        return WorkflowSummary(
            workflow_id=self.workflow_id,
            property_id=self.property_id,
            workflow_type=self.workflow_type,
            status=self.status,
            current_step=self.current_step,
            initiated_by=self.initiated_by,
            correlation_id=self.correlation_id,
            input_reference=self.input_reference,
            approval_request_id=self.approval_request_id,
            action_executed=bool(self.action_result and self.action_result.get("status") == "executed"),
            outcome_verified=bool(self.verification_result and self.verification_result.get("verified") is True),
            errors=list(self.errors),
            created_at=self.created_at,
            updated_at=self.updated_at,
            completed_at=self.completed_at,
        )

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> WorkflowState:
        return cls.model_validate_json(data)
