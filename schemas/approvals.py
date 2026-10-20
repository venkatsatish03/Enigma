
"""Schemas for human approval workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from pydantic import BaseModel, Field, field_validator

ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


class ApprovalRequestCreate(BaseModel):
    action_id: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)
    approver_ids: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    amount: str | None = None
    currency: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    expires_at: datetime
    correlation_id: str | None = None
    property_id: str | None = None


class ApprovalRequest(BaseModel):
    approval_id: str
    action_id: str
    action_type: str
    requested_by: str
    approver_ids: list[str]
    rationale: str
    amount: str | None = None
    currency: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    status: ApprovalStatus = "pending"
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    correlation_id: str | None = None
    property_id: str | None = None


class ApprovalDecision(BaseModel):
    approval_id: str = Field(min_length=1)
    approver_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1)
    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Decision reason cannot be blank")
        return value


class ApprovalDecisionResult(BaseModel):
    approval: ApprovalRequest
    message: str
