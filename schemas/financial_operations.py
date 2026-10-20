
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field
from schemas.payment_allocation import PaymentAllocation

class FinancialRecordType(str, Enum):
    INVOICE = "invoice"
    RENT_RECEIPT = "rent_receipt"
    BANK_TRANSACTION = "bank_transaction"
    CREDIT_NOTE = "credit_note"
    REFUND = "refund"
    OTHER = "other"


class MatchStatus(str, Enum):
    CANDIDATE = "candidate"
    MATCHED = "matched"
    MATCHED_WITH_DATE_CONFLICT = "matched_with_date_conflict"
    AMOUNT_CONFLICT = "amount_conflict"
    UNMATCHED = "unmatched"
    REQUIRES_REVIEW = "requires_review"


class FindingType(str, Enum):
    POSSIBLE_DUPLICATE = "possible_duplicate"
    AMOUNT_DISCREPANCY = "amount_discrepancy"
    DATE_DISCREPANCY = "date_discrepancy"
    MISSING_SUPPORTING_DOCUMENT = "missing_supporting_document"
    UNMATCHED_TRANSACTION = "unmatched_transaction"
    UNVERIFIED_SETTLEMENT = "unverified_settlement"
    CURRENCY_MISMATCH = "currency_mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class ActionType(str, Enum):
    REQUEST_DOCUMENT = "request_document"
    REVIEW_MATCH = "review_match"
    PROPOSE_PAYMENT = "propose_payment"
    PROPOSE_ACCOUNTING_ADJUSTMENT = "propose_accounting_adjustment"
    PROPOSE_PAYMENT_ALLOCATION = "propose_payment_allocation"


class ActionStatus(str, Enum):
    PROPOSED = "proposed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceReference(BaseModel):
    """
    Pointer to evidence produced by the existing Document Agent.

    evidence_id and document_id must refer to actual stored records
    or source identifiers. This model does not create evidence itself.
    """

    evidence_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    field_name: Optional[str] = None
    extracted_value: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=1)


class FinancialRecord(BaseModel):
    """
    Normalized financial record consumed by the Financial Operations Agent.

    Amount sign conventions must be defined by the source adapter.
    Currency conversion is deliberately not performed here.
    """

    record_id: str = Field(min_length=1)
    record_type: FinancialRecordType

    document_id: str = Field(min_length=1)
    reference: Optional[str] = None
    party_name: Optional[str] = None
    party_id: Optional[str] = None

    amount: Optional[Decimal] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    transaction_date: Optional[date] = None
    due_date: Optional[date] = None

    # Preserve the originating system's status without assuming
    # that extraction or validation means the obligation is settled.
    source_status: Optional[str] = None

    evidence: list[EvidenceReference] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class ReconciliationMatch(BaseModel):
    match_id: str = Field(min_length=1)
    record_ids: list[str] = Field(min_length=2)
    status: MatchStatus
    match_method: str = Field(min_length=1)

    # A score helps rank candidates; it is not proof of settlement.
    score: Optional[float] = Field(default=None, ge=0, le=1)

    matched_fields: list[str] = Field(default_factory=list)
    conflicting_fields: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = ""


class FinancialFinding(BaseModel):
    finding_id: str = Field(min_length=1)
    finding_type: FindingType
    severity: FindingSeverity
    summary: str = Field(min_length=1)

    record_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    status: str = "open"
    explanation: str = ""


class BalanceAssessment(BaseModel):
    """
    A calculated balance with an explicit evidence and currency basis.

    A missing amount means the balance has not been established.
    """

    assessment_id: str = Field(min_length=1)
    obligation_record_id: str = Field(min_length=1)

    currency: Optional[str] = None
    original_amount: Optional[Decimal] = None
    supported_payments: Decimal = Decimal("0")
    supported_credits: Decimal = Decimal("0")
    outstanding_amount: Optional[Decimal] = None

    evidence_ids: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    calculation_supported: bool = False


class ProposedFinancialAction(BaseModel):
    action_id: str = Field(min_length=1)
    action_type: ActionType
    status: ActionStatus = ActionStatus.PROPOSED

    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    target_reference: Optional[str] = None

    record_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)

    requires_approval: bool = True
    approval_id: Optional[str] = None

    # Stable key used by the execution layer to prevent duplicate actions.
    idempotency_key: str = Field(min_length=1)


class FinancialOperationsResult(BaseModel):
    operation_id: str = Field(min_length=1)
    status: str = "completed"

    records: list[FinancialRecord] = Field(default_factory=list)
    matches: list[ReconciliationMatch] = Field(default_factory=list)
    findings: list[FinancialFinding] = Field(default_factory=list)
    payment_allocations: list[PaymentAllocation] = Field(default_factory=list)
    unallocated_payment_amounts: dict[str, Decimal] = Field(default_factory=dict)
    unresolved_allocation_issues: list[str] = Field(default_factory=list)

    balance_assessments: list[BalanceAssessment] = Field(
        default_factory=list
    )
    proposed_actions: list[ProposedFinancialAction] = Field(
        default_factory=list
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
