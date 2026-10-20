"""Typed schemas, enums, and request/response models for workflows."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.payment_allocation import PaymentAllocationInstruction


class WorkflowType(str, Enum):
    INVOICE_PROCESSING = "invoice_processing"
    RENT_RECONCILIATION = "rent_reconciliation"
    FINANCIAL_AUDIT = "financial_audit"
    MAINTENANCE_LIFECYCLE = "maintenance_lifecycle"


class WorkflowStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        )

    @property
    def is_resumable(self) -> bool:
        return self in (
            WorkflowStatus.AWAITING_APPROVAL,
            WorkflowStatus.NEEDS_REVIEW,
            WorkflowStatus.RUNNING,
            WorkflowStatus.CREATED,
        )


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepName(str, Enum):
    DOCUMENT_ANALYSIS = "document_analysis"
    FINANCIAL_OPERATIONS = "financial_operations"
    POLICY_VALIDATION = "policy_validation"
    APPROVAL_GATE = "approval_gate"
    EXECUTE_ACTION = "execute_action"
    VERIFY_OUTCOME = "verify_outcome"
    MAINTENANCE_TRIAGE = "maintenance_triage"
    MAINTENANCE_DISPATCH = "maintenance_dispatch"


class WorkflowCreateRequest(BaseModel):
    workflow_type: WorkflowType
    input_reference: str = Field(
        ...,
        min_length=1,
        description="Path to a PDF file or existing storage document reference.",
    )
    allocation_instructions: list[PaymentAllocationInstruction] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowResumeRequest(BaseModel):
    reason: str | None = Field(default=None, description="Optional justification for resumption.")


class WorkflowCancelRequest(BaseModel):
    reason: str = Field(min_length=1, description="Mandatory justification for workflow cancellation.")


class WorkflowStepRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_id: str
    workflow_id: str
    step_name: StepName
    step_index: int
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    retry_count: int = 0
    error_message: str | None = None
    output_summary: str | None = None


class ActionExecutionDetail(BaseModel):
    action_type: str
    entity_type: str
    entity_id: str
    status: str
    executed_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class OutcomeVerificationDetail(BaseModel):
    verified: bool
    verified_at: datetime
    evidence_details: list[str] = Field(default_factory=list)


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workflow_id: str
    property_id: str
    workflow_type: WorkflowType
    status: WorkflowStatus
    current_step: StepName | None = None
    initiated_by: str
    correlation_id: str
    input_reference: str
    approval_request_id: str | None = None
    action_executed: bool = False
    outcome_verified: bool = False
    errors: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
