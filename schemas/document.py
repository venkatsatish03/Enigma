from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


DocumentType = Literal[
    "invoice",
    "maintenance_invoice",
    "rent_receipt",
    "bank_statement",
    "lease",
    "unknown",
]


class FieldEvidence(BaseModel):
    field_name: str
    value: str
    page_number: int = Field(ge=1)
    text_snippet: str
    confidence: float | None = Field(default=None, ge=0, le=1)


class LineItem(BaseModel):
    description: str
    quantity: Decimal | None = None
    unit_of_measure: str | None = None
    unit_price: Decimal | None = None
    total: Decimal | None = None
    is_arithmetic_valid: bool | None = None


class BankTransaction(BaseModel):
    date: str | None = None
    description: str
    reference: str | None = None
    amount: Decimal
    signed_amount: str | None = None
    transaction_type: Literal["credit", "debit", "unknown"] = "unknown"
    sign: str = ""
    currency: str | None = None
    page_number: int | None = None
    confidence: float | None = None
    is_sign_validated: bool = False
    sign_validation_note: str | None = None


class ValidationDimensions(BaseModel):
    extraction_completeness: Literal["complete", "partial", "incomplete"] = "complete"
    field_validation: Literal["passed", "failed", "warnings"] = "passed"
    arithmetic_validation: Literal["passed", "failed", "not_applicable"] = "not_applicable"
    currency_validation: Literal["explicit_iso", "unresolved_symbol", "missing"] = "explicit_iso"
    temporal_consistency: Literal["consistent", "date_conflict", "unconfirmed", "not_applicable"] = "not_applicable"
    cross_document_matching: Literal["matched", "unmatched", "pending_statement_coverage", "not_applicable"] = "not_applicable"
    reconciliation_status: str = "not_applicable"
    human_review_required: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class CrossDocumentRelationship(BaseModel):
    relationship_type: str
    source_document_id: str | None = None
    source_document: str
    source_date: str | None = None
    target_document_id: str | None = None
    target_document: str | None = None
    target_date: str | None = None
    reference: str | None = None
    amount: Decimal | None = None
    matched_amount: Decimal | None = None
    status: str = "RECONCILED"
    is_validated: bool = True
    human_review_required: bool = False
    conflict_reason: str | None = None
    description: str = ""
    checks_performed: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class FinancialDocument(BaseModel):
    document_id: str | None = None
    document_type: DocumentType = "unknown"
    source_file: str

    vendor_name: str | None = None
    tenant_name: str | None = None
    bill_to: str | None = None
    property_address: str | None = None
    unit: str | None = None
    service_description: str | None = None

    invoice_number: str | None = None
    receipt_number: str | None = None

    document_date: str | None = None
    due_date: str | None = None
    payment_date: str | None = None

    # Rent receipt specific fields
    rent_period_start: str | None = None
    rent_period_end: str | None = None
    payment_method: str | None = None
    payment_reference: str | None = None

    # Bank statement specific fields
    account_name: str | None = None
    account_identifier: str | None = None
    statement_period_start: str | None = None
    statement_period_end: str | None = None
    statement_reconciliation_status: str | None = None

    # Financial figures
    subtotal: Decimal | None = None
    tax_rate: str | None = None
    tax: Decimal | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    total_amount: Decimal | None = None
    total_is_calculated: bool = False
    amount_paid: Decimal | None = None
    outstanding_balance: Decimal | None = None
    payment_terms: str | None = None

    currency: str | None = None
    currency_status: Literal["explicit", "inferred", "unresolved"] = "unresolved"
    currency_evidence: str | None = None

    line_items: list[LineItem] = Field(default_factory=list)
    line_items_total: Decimal | None = None
    is_total_validated: bool = False
    total_validation_note: str | None = None
    bank_transactions: list[BankTransaction] = Field(default_factory=list)

    extraction_method: str = "paddleocr"
    overall_confidence: float | None = Field(default=None, ge=0, le=1)
    field_evidence: list[FieldEvidence] = Field(default_factory=list)

    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation_dimensions: ValidationDimensions | None = None
    requires_human_review: bool = False


