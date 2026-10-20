
"""Typed request and response schemas for the Resident & Lease Agent."""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"


class FindingType(str, Enum):
    DUPLICATE_RESIDENT = "duplicate_resident"
    INCOMPLETE_RESIDENT = "incomplete_resident"
    EXPIRED_ACTIVE_LEASE = "expired_active_lease"
    UPCOMING_LEASE_EXPIRATION = "upcoming_lease_expiration"
    FUTURE_ACTIVE_LEASE = "future_active_lease"
    OVERLAPPING_UNIT_LEASES = "overlapping_unit_leases"
    OVERLAPPING_RESIDENT_LEASES = "overlapping_resident_leases"


class ResidentLeaseFinding(BaseModel):
    finding_type: FindingType
    severity: FindingSeverity
    title: str
    description: str
    entity_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommended_action: str


class ResidentLeaseAnalysisRequest(BaseModel):
    as_of_date: date | None = None
    expiration_window_days: int = Field(default=60, ge=0, le=365)


class ResidentLeaseAnalysisResult(BaseModel):
    property_id: str
    analysis_date: date
    expiration_window_days: int
    residents_analyzed: int
    leases_analyzed: int
    findings: list[ResidentLeaseFinding] = Field(default_factory=list)
    summary: str
