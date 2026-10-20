
"""Schemas for evidence-based payment allocation."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PaymentAllocationInstruction(BaseModel):
    """An explicit instruction to allocate part of a payment to an invoice."""

    payment_record_id: str = Field(min_length=1)
    invoice_record_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=Decimal("0"))
    currency: str = Field(min_length=3, max_length=3)
    instruction_reference: str | None = None

    @model_validator(mode="after")
    def validate_distinct_records(self):
        if self.payment_record_id == self.invoice_record_id:
            raise ValueError("Payment and invoice record IDs must differ")
        self.currency = self.currency.upper()
        return self


class PaymentAllocation(BaseModel):
    """A supported allocation of a payment to an invoice."""

    allocation_id: str
    payment_record_id: str
    invoice_record_id: str
    amount: Decimal = Field(gt=Decimal("0"))
    currency: str = Field(min_length=3, max_length=3)
    method: Literal["explicit_instruction", "unique_reference", "confirmed_match"]
    status: Literal["allocated"] = "allocated"
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str

    @model_validator(mode="after")
    def normalize_currency(self):
        self.currency = self.currency.upper()
        return self


class PaymentAllocationResult(BaseModel):
    """Allocation outcomes and any payment amounts that remain unallocated."""

    allocations: list[PaymentAllocation] = Field(default_factory=list)
    unallocated_amounts: dict[str, Decimal] = Field(default_factory=dict)
    unresolved_issues: list[str] = Field(default_factory=list)
