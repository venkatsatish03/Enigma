"""Tenant booking requests for published owner listings."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class BookingRequestCreate(BaseModel):
    listing_id: str = Field(min_length=1, max_length=80)
    desired_move_in_date: date | None = None
    message: str | None = Field(default=None, max_length=2000)


class BookingRequestDecision(BaseModel):
    status: Literal["accepted", "declined"]
    decision_note: str | None = Field(default=None, max_length=2000)


class OwnerListingAvailabilityUpdate(BaseModel):
    property_status: Literal["available", "occupied", "under_offer", "pending", "sold", "let"]


class BookingRequestRecord(BaseModel):
    request_id: str
    listing_id: str
    property_label: str
    city: str | None = None
    country: str | None = None
    listing_type: str
    price_amount: str | None = None
    price_currency: str
    tenant_email: str
    owner_email: str | None = None
    desired_move_in_date: date | None = None
    message: str | None = None
    status: Literal["pending", "accepted", "declined"]
    decision_note: str | None = None
    created_at: datetime
    updated_at: datetime
