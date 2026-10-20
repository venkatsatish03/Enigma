"""Shared domain model schemas for property management entities."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


def _validate_decimal_amount(value: Decimal | None) -> Decimal | None:
    if value is not None:
        if not value.is_finite() or value < Decimal("0"):
            raise ValueError("Amount must be a non-negative finite decimal value.")
    return value


# --- Enums ---

class UnitStatus(str, Enum):
    VACANT = "vacant"
    OCCUPIED = "occupied"
    MAINTENANCE = "maintenance"


class ResidentStatus(str, Enum):
    ACTIVE = "active"
    PAST = "past"
    APPLICANT = "applicant"


class LeaseStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    TERMINATED = "terminated"


class VendorCategory(str, Enum):
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    HVAC = "hvac"
    LANDSCAPING = "landscaping"
    CLEANING = "cleaning"
    GENERAL = "general"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"
    REFUNDED = "refunded"
    RECONCILED = "reconciled"


class PaymentMethod(str, Enum):
    ACH = "ach"
    CHECK = "check"
    CREDIT_CARD = "credit_card"
    BANK_TRANSFER = "bank_transfer"
    WIRE = "wire"
    CASH = "cash"


class ExpenseCategory(str, Enum):
    REPAIRS = "repairs"
    MAINTENANCE = "maintenance"
    UTILITIES = "utilities"
    INSURANCE = "insurance"
    TAXES = "taxes"
    MANAGEMENT = "management"
    SUPPLIES = "supplies"
    OTHER = "other"


class ExpenseStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PAID = "paid"
    CANCELLED = "cancelled"
    RECONCILED = "reconciled"


class WorkOrderPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EMERGENCY = "emergency"

class MaintenanceRequestStatus(str, Enum):
    SUBMITTED = "submitted"
    TRIAGED = "triaged"
    CONVERTED_TO_WORK_ORDER = "converted_to_work_order"
    CLOSED = "closed"
    CANCELLED = "cancelled"

class WorkOrderStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# --- Property ---

class PropertyRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    property_id: str
    name: str
    address: str | None = None
    management_fee_rate: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class PropertyUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    management_fee_rate: Decimal | None = None


# --- Unit ---

class UnitCreate(BaseModel):
    unit_number: str = Field(min_length=1)
    status: UnitStatus = UnitStatus.VACANT
    rent_amount: Decimal | None = None

    @field_validator("rent_amount")
    @classmethod
    def check_amount(cls, v: Decimal | None) -> Decimal | None:
        return _validate_decimal_amount(v)


class UnitUpdate(BaseModel):
    unit_number: str | None = None
    status: UnitStatus | None = None
    rent_amount: Decimal | None = None

    @field_validator("rent_amount")
    @classmethod
    def check_amount(cls, v: Decimal | None) -> Decimal | None:
        return _validate_decimal_amount(v)


class UnitRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit_id: str
    property_id: str
    unit_number: str
    status: UnitStatus
    rent_amount: Decimal | None = None
    created_at: datetime
    updated_at: datetime


# --- Resident ---

class ResidentCreate(BaseModel):
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    phone: str | None = None
    status: ResidentStatus = ResidentStatus.ACTIVE

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v:
            raise ValueError("Invalid email format")
        return v


class ResidentUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    status: ResidentStatus | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip().lower()
            if "@" not in v:
                raise ValueError("Invalid email format")
        return v


class ResidentRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resident_id: str
    property_id: str
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    status: ResidentStatus
    created_at: datetime
    updated_at: datetime


# --- Lease ---

class LeaseCreate(BaseModel):
    unit_id: str = Field(min_length=1)
    resident_id: str = Field(min_length=1)
    start_date: date
    end_date: date
    rent_amount: Decimal
    deposit_amount: Decimal | None = None
    status: LeaseStatus = LeaseStatus.ACTIVE

    @field_validator("rent_amount", "deposit_amount")
    @classmethod
    def check_amount(cls, v: Decimal | None) -> Decimal | None:
        return _validate_decimal_amount(v)

    @field_validator("end_date")
    @classmethod
    def check_dates(cls, v: date, info) -> date:
        start = info.data.get("start_date")
        if start and v < start:
            raise ValueError("end_date cannot be earlier than start_date")
        return v


class LeaseUpdate(BaseModel):
    status: LeaseStatus | None = None
    end_date: date | None = None
    rent_amount: Decimal | None = None
    deposit_amount: Decimal | None = None

    @field_validator("rent_amount", "deposit_amount")
    @classmethod
    def check_amount(cls, v: Decimal | None) -> Decimal | None:
        return _validate_decimal_amount(v)


class LeaseRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lease_id: str
    property_id: str
    unit_id: str
    resident_id: str
    start_date: date
    end_date: date
    rent_amount: Decimal
    deposit_amount: Decimal | None = None
    status: LeaseStatus
    created_at: datetime
    updated_at: datetime


# --- Vendor ---

class VendorCreate(BaseModel):
    name: str = Field(min_length=1)
    category: VendorCategory = VendorCategory.GENERAL
    email: str | None = None
    phone: str | None = None
    is_active: bool = True


class VendorUpdate(BaseModel):
    name: str | None = None
    category: VendorCategory | None = None
    email: str | None = None
    phone: str | None = None
    is_active: bool | None = None


class VendorRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vendor_id: str
    property_id: str
    name: str
    category: VendorCategory
    email: str | None = None
    phone: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Document Metadata ---

class DocumentMetadataCreate(BaseModel):
    filename: str = Field(min_length=1)
    file_type: str = Field(min_length=1)
    storage_path: str = Field(min_length=1)
    file_size_bytes: int = Field(ge=0)
    sha256_checksum: str = Field(min_length=64, max_length=64)
    associated_entity_type: str | None = None
    associated_entity_id: str | None = None


class DocumentMetadataRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    property_id: str
    filename: str
    file_type: str
    storage_path: str
    file_size_bytes: int
    sha256_checksum: str
    associated_entity_type: str | None = None
    associated_entity_id: str | None = None
    created_at: datetime


# --- Payment ---

class PaymentCreate(BaseModel):
    amount: Decimal
    currency: str = Field(default="USD", min_length=3, max_length=3)
    payment_date: date
    payment_method: PaymentMethod = PaymentMethod.ACH
    status: PaymentStatus = PaymentStatus.PENDING
    resident_id: str | None = None
    lease_id: str | None = None
    reference_number: str | None = None
    document_id: str | None = None

    @field_validator("amount")
    @classmethod
    def check_amount(cls, v: Decimal) -> Decimal:
        res = _validate_decimal_amount(v)
        if res is None or res <= Decimal("0"):
            raise ValueError("Payment amount must be greater than zero")
        return res

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.strip().upper()


class PaymentUpdate(BaseModel):
    status: PaymentStatus | None = None
    reference_number: str | None = None


class PaymentRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: str
    property_id: str
    amount: Decimal
    currency: str
    payment_date: date
    payment_method: PaymentMethod
    status: PaymentStatus
    resident_id: str | None = None
    lease_id: str | None = None
    reference_number: str | None = None
    document_id: str | None = None
    created_at: datetime
    updated_at: datetime


# --- Expense ---

class ExpenseCreate(BaseModel):
    category: ExpenseCategory
    amount: Decimal
    currency: str = Field(default="USD", min_length=3, max_length=3)
    expense_date: date
    description: str = Field(min_length=1)
    status: ExpenseStatus = ExpenseStatus.PENDING
    vendor_id: str | None = None
    unit_id: str | None = None
    document_id: str | None = None

    @field_validator("amount")
    @classmethod
    def check_amount(cls, v: Decimal) -> Decimal:
        res = _validate_decimal_amount(v)
        if res is None or res <= Decimal("0"):
            raise ValueError("Expense amount must be greater than zero")
        return res

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.strip().upper()


class ExpenseUpdate(BaseModel):
    status: ExpenseStatus | None = None
    description: str | None = None
    amount: Decimal | None = None

    @field_validator("amount")
    @classmethod
    def check_amount(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v <= Decimal("0"):
            raise ValueError("Expense amount must be greater than zero")
        return _validate_decimal_amount(v)


class ExpenseRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    expense_id: str
    property_id: str
    category: ExpenseCategory
    amount: Decimal
    currency: str
    expense_date: date
    status: ExpenseStatus
    description: str
    vendor_id: str | None = None
    unit_id: str | None = None
    document_id: str | None = None
    approval_status: str = "pending"
    approved_by: str | None = None
    approved_at: datetime | None = None
    decision_reason: str | None = None
    created_at: datetime
    updated_at: datetime


# --- Maintenance Work Order ---

class WorkOrderCreate(BaseModel):
    unit_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: WorkOrderPriority = WorkOrderPriority.MEDIUM
    status: WorkOrderStatus = WorkOrderStatus.OPEN
    resident_id: str | None = None
    vendor_id: str | None = None
    estimated_cost: Decimal | None = None
    actual_cost: Decimal | None = None

    @field_validator("estimated_cost", "actual_cost")
    @classmethod
    def check_amount(cls, v: Decimal | None) -> Decimal | None:
        return _validate_decimal_amount(v)


class WorkOrderUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: WorkOrderStatus | None = None
    priority: WorkOrderPriority | None = None
    vendor_id: str | None = None
    estimated_cost: Decimal | None = None
    actual_cost: Decimal | None = None

    @field_validator("estimated_cost", "actual_cost")
    @classmethod
    def check_amount(cls, v: Decimal | None) -> Decimal | None:
        return _validate_decimal_amount(v)


class WorkOrderRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_order_id: str
    property_id: str
    unit_id: str
    resident_id: str | None = None
    vendor_id: str | None = None
    title: str
    description: str
    priority: WorkOrderPriority
    status: WorkOrderStatus
    estimated_cost: Decimal | None = None
    actual_cost: Decimal | None = None
    created_at: datetime
    updated_at: datetime



# --- Maintenance Requests ---

class MaintenanceRequestCreate(BaseModel):
    unit_id: str = Field(min_length=1)
    description: str = Field(min_length=3, max_length=5000)
    resident_id: str | None = None


class MaintenanceRequestUpdate(BaseModel):
    status: MaintenanceRequestStatus | None = None
    priority: WorkOrderPriority | None = None
    category: str | None = Field(default=None, max_length=100)
    is_emergency: bool | None = None
    estimated_sla_hours: int | None = Field(default=None, ge=1, le=8760)
    sla_deadline_at: datetime | None = None
    work_order_id: str | None = None


class MaintenanceRequestRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: str
    property_id: str
    unit_id: str
    resident_id: str | None = None
    description: str
    status: MaintenanceRequestStatus
    priority: WorkOrderPriority | None = None
    category: str | None = None
    is_emergency: bool = False
    estimated_sla_hours: int | None = None
    sla_deadline_at: datetime | None = None
    work_order_id: str | None = None
    created_at: datetime
    updated_at: datetime


# --- SLA Monitoring & Escalations ---

class SlaEscalationType(str, Enum):
    APPROACHING_DEADLINE = "approaching_deadline"
    OVERDUE = "overdue"


class SlaEscalationStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class SlaEscalationCreate(BaseModel):
    request_id: str = Field(min_length=1)
    escalation_type: SlaEscalationType
    sla_deadline_at: datetime
    detected_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class SlaEscalationUpdate(BaseModel):
    status: SlaEscalationStatus
    notes: str | None = Field(default=None, max_length=2000)


class SlaEscalationRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    escalation_id: str
    property_id: str
    request_id: str
    escalation_type: SlaEscalationType
    sla_deadline_at: datetime
    detected_at: datetime
    status: SlaEscalationStatus
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class SlaMonitoringResult(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    property_id: str
    evaluated_count: int = 0
    approaching_deadline_count: int = 0
    overdue_count: int = 0
    new_escalations_count: int = 0
    existing_escalations_count: int = 0
    escalations: list[SlaEscalationRecord] = Field(default_factory=list)
    monitoring_timestamp: datetime
    monitored_at: datetime | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.monitored_at is None:
            self.monitored_at = self.monitoring_timestamp


# --- Invoices & Rent Obligations ---

class InvoiceType(str, Enum):
    RENT = "rent"
    MAINTENANCE = "maintenance"
    UTILITY = "utility"
    FEE = "fee"
    OTHER = "other"


class InvoiceStatus(str, Enum):
    UNPAID = "unpaid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class InvoiceCreate(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=100)
    invoice_type: InvoiceType = InvoiceType.RENT
    amount: Decimal = Field(gt=Decimal("0"))
    currency: str = Field(default="USD", min_length=3, max_length=3)
    issue_date: date
    due_date: date
    lease_id: str | None = None
    resident_id: str | None = None
    vendor_id: str | None = None
    description: str | None = None
    document_id: str | None = None

    @field_validator("amount")
    @classmethod
    def check_amount(cls, v: Decimal) -> Decimal:
        res = _validate_decimal_amount(v)
        if res is None or res <= Decimal("0"):
            raise ValueError("Invoice amount must be greater than zero")
        return res

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.strip().upper()


class InvoiceUpdate(BaseModel):
    status: InvoiceStatus | None = None
    amount_paid: Decimal | None = None
    description: str | None = None
    due_date: date | None = None

    @field_validator("amount_paid")
    @classmethod
    def check_amount_paid(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < Decimal("0"):
            raise ValueError("Amount paid cannot be negative")
        return _validate_decimal_amount(v)


class InvoiceRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invoice_id: str
    property_id: str
    lease_id: str | None = None
    resident_id: str | None = None
    vendor_id: str | None = None
    invoice_number: str
    invoice_type: InvoiceType
    amount: Decimal
    amount_paid: Decimal = Decimal("0.00")
    currency: str
    issue_date: date
    due_date: date
    status: InvoiceStatus
    description: str | None = None
    document_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def outstanding_amount(self) -> Decimal:
        return max(Decimal("0.00"), self.amount - self.amount_paid)


# --- Bank Reconciliation Models ---

class BankStatementRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    statement_id: str
    property_id: str
    account_identifier: str | None = None
    filename: str
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    statement_start_date: date | None = None
    statement_end_date: date | None = None
    total_transactions: int = 0
    reconciliation_status: str = "pending"
    file_checksum: str | None = None
    created_at: datetime


class BankTransactionMatchStatus(str, Enum):
    UNMATCHED = "unmatched"
    MATCHED = "matched"
    PARTIALLY_MATCHED = "partially_matched"
    DISCREPANCY = "discrepancy"
    MANUAL_REVIEW = "manual_review"


class BankTransactionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    statement_id: str
    property_id: str
    transaction_date: date
    description: str
    reference: str | None = None
    amount: Decimal
    transaction_type: str  # "credit" or "debit"
    currency: str = "USD"
    match_status: BankTransactionMatchStatus = BankTransactionMatchStatus.UNMATCHED
    matched_entity_type: str | None = None  # "payment", "invoice", "expense"
    matched_entity_id: str | None = None
    match_confidence: float | None = None
    match_notes: str | None = None
    reconciled_at: datetime | None = None
    reconciled_by: str | None = None
    created_at: datetime


class MatchDecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "correct"]
    matched_entity_type: str | None = None
    matched_entity_id: str | None = None
    notes: str | None = None


# --- Expense Approval Models ---

class ExpenseApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETURNED = "returned"


class ExpenseApprovalDecision(BaseModel):
    decision: Literal["approve", "reject", "return"]
    reason: str = Field(min_length=1, max_length=1000)


# --- Compliance Models ---

class ComplianceCategory(str, Enum):
    OVERDUE_RENT = "overdue_rent"
    MISSING_INFORMATION = "missing_information"
    DUPLICATE_INVOICE = "duplicate_invoice"
    UNAPPROVED_EXPENSE = "unapproved_expense"
    RECONCILIATION_DISCREPANCY = "reconciliation_discrepancy"
    RECORD_INCONSISTENCY = "record_inconsistency"


class ComplianceSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class ComplianceStatus(str, Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ComplianceFindingRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    finding_id: str
    property_id: str
    category: ComplianceCategory
    severity: ComplianceSeverity
    title: str
    description: str
    triggering_rule: str
    affected_record_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    status: ComplianceStatus = ComplianceStatus.OPEN
    assigned_to: str | None = None
    resolution_reason: str | None = None
    resolved_at: datetime | None = None
    escalation_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ComplianceResolutionRequest(BaseModel):
    action: Literal["resolve", "dismiss"]
    reason: str = Field(min_length=1, max_length=1000)


class ComplianceAssignmentRequest(BaseModel):
    assigned_to: str = Field(min_length=1, max_length=100)


# --- Notification Models ---

class NotificationType(str, Enum):
    OVERDUE_RENT = "overdue_rent"
    COMPLIANCE_ALERT = "compliance_alert"
    SLA_ESCALATION = "sla_escalation"
    OWNER_STATEMENT = "owner_statement"
    GENERAL = "general"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    SIMULATED = "simulated"
    FAILED = "failed"


class NotificationDeliveryMode(str, Enum):
    SMTP = "smtp"
    SIMULATED = "simulated"


class NotificationPreviewRequest(BaseModel):
    notification_type: NotificationType
    recipient_email: str = Field(min_length=3)
    recipient_name: str | None = None
    context_data: dict[str, Any] = Field(default_factory=dict)


class NotificationPreviewResponse(BaseModel):
    notification_type: NotificationType
    recipient_email: str
    recipient_name: str | None = None
    subject: str
    body_text: str
    body_html: str | None = None


class NotificationSendRequest(BaseModel):
    notification_type: NotificationType
    recipient_email: str = Field(min_length=3)
    recipient_name: str | None = None
    subject: str | None = None
    context_data: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class NotificationRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_id: str
    property_id: str
    recipient_email: str
    recipient_name: str | None = None
    subject: str
    body_text: str
    body_html: str | None = None
    notification_type: NotificationType
    status: NotificationStatus
    delivery_mode: NotificationDeliveryMode
    idempotency_key: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    error_message: str | None = None
    sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# --- Financial Reporting Models ---

class ExpenseCategorySummary(BaseModel):
    category: ExpenseCategory
    total_amount: Decimal
    count: int


class FinancialSummaryReport(BaseModel):
    property_id: str
    period_start: date | None = None
    period_end: date | None = None
    currency: str = "USD"
    total_rent_billed: Decimal
    total_rent_collected: Decimal
    total_rent_outstanding: Decimal
    collection_rate_percentage: Decimal | None = None
    pending_rent_amount: Decimal = Decimal("0.00")
    pending_payments_count: int = 0
    total_expenses: Decimal
    net_operating_income: Decimal
    pending_expense_approvals_count: int
    pending_expense_approvals_amount: Decimal
    unpaid_invoices_count: int
    unpaid_invoices_amount: Decimal
    reconciliation_discrepancies_count: int
    reconciliation_discrepancy_amount: Decimal
    cash_inflows: Decimal
    cash_outflows: Decimal
    net_cash_flow: Decimal
    expenses_by_category: list[ExpenseCategorySummary] = Field(default_factory=list)
    generated_at: datetime


class OwnerStatementReport(BaseModel):
    property_id: str
    property_name: str
    statement_date: date
    period_start: date
    period_end: date
    currency: str = "USD"
    gross_rental_income: Decimal
    other_income: Decimal
    total_income: Decimal
    total_operating_expenses: Decimal
    management_fee_rate: Decimal = Decimal("0.08")
    management_fee_amount: Decimal
    net_operating_income: Decimal
    reserve_withheld: Decimal = Decimal("0.00")
    owner_distribution_amount: Decimal
    itemized_income: list[dict[str, Any]] = Field(default_factory=list)
    itemized_expenses: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: datetime