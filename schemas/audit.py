
"""Schemas for persistent audit events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditEventCreate(BaseModel):
    actor_id: str = Field(min_length=1)
    actor_type: str = Field(default="system", min_length=1)
    event_type: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    correlation_id: str | None = None
    property_id: str | None = None


class AuditEvent(BaseModel):
    event_id: str
    sequence_number: int
    created_at: datetime
    actor_id: str
    actor_type: str
    event_type: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
    evidence_ids: list[str]
    correlation_id: str | None = None
    property_id: str | None = None
    previous_hash: str
    event_hash: str


class AuditVerificationResult(BaseModel):
    valid: bool
    events_checked: int
    first_invalid_sequence: int | None = None
    message: str
