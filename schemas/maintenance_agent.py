"""Typed request and response schemas for the Maintenance & Property Operations Agent."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from schemas.domain import (
    VendorCategory,
    WorkOrderPriority,
    WorkOrderStatus,
)


class MaintenanceCategory(str, Enum):
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    HVAC = "hvac"
    APPLIANCE = "appliance"
    STRUCTURAL = "structural"
    PEST_CONTROL = "pest_control"
    LANDSCAPING = "landscaping"
    CLEANING = "cleaning"
    SECURITY = "security"
    GENERAL = "general"


class MaintenanceFindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class MaintenanceFindingType(str, Enum):
    OVERDUE_WORK_ORDER = "overdue_work_order"
    STALLED_WORK_ORDER = "stalled_work_order"
    UNASSIGNED_HIGH_PRIORITY = "unassigned_high_priority"
    COST_OVERRUN = "cost_overrun"
    PREVENTIVE_MAINTENANCE_DUE = "preventive_maintenance_due"
    PREVENTIVE_MAINTENANCE_OVERDUE = "preventive_maintenance_overdue"
    WARRANTY_EXPIRING_SOON = "warranty_expiring_soon"
    WARRANTY_EXPIRED = "warranty_expired"
    HIGH_FAILURE_ASSET = "high_failure_asset"
    INSPECTION_DEFICIENCY = "inspection_deficiency"

    # New findings
    DUPLICATE_WORK_ORDER = "duplicate_work_order"
    RECURRING_UNIT_ISSUE = "recurring_unit_issue"


class MaintenanceFinding(BaseModel):
    finding_type: MaintenanceFindingType
    severity: MaintenanceFindingSeverity
    title: str
    description: str
    entity_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommended_action: str


# --- 1. Maintenance Request Triage ---

class MaintenanceTriageRequest(BaseModel):
    description: str = Field(min_length=3)
    unit_id: str | None = None
    resident_id: str | None = None
    reported_date: date | None = None
    photos_or_documents: list[str] = Field(default_factory=list)


class MaintenanceTriageResult(BaseModel):
    category: MaintenanceCategory
    priority: WorkOrderPriority
    is_emergency: bool
    emergency_rationale: str | None = None
    immediate_safety_instructions: list[str] = Field(default_factory=list)
    estimated_sla_hours: int
    recommended_action: str
    suggested_vendor_category: VendorCategory
    extracted_keywords: list[str] = Field(default_factory=list)


# --- 2. Vendor Coordination & Dispatch ---

class VendorMatchRecommendation(BaseModel):
    vendor_id: str
    vendor_name: str
    category: VendorCategory
    match_score: float = Field(ge=0.0, le=1.0)
    reason: str
    phone: str | None = None
    email: str | None = None
    active_work_orders_count: int = 0


class AutoDispatchWorkOrderRequest(BaseModel):
    work_order_id: str | None = None
    unit_id: str | None = None
    resident_id: str | None = None
    title: str | None = None
    description: str | None = None
    auto_assign: bool = True
    budget_limit: Decimal | None = None


class AutoDispatchWorkOrderResult(BaseModel):
    work_order_id: str
    property_id: str
    title: str
    category: MaintenanceCategory
    priority: WorkOrderPriority
    status: WorkOrderStatus
    assigned_vendor: VendorMatchRecommendation | None = None
    available_candidate_vendors: list[VendorMatchRecommendation] = Field(default_factory=list)
    sla_deadline: datetime | None = None
    audit_note: str


# --- 3. Inspections & Property Checks ---

class InspectionType(str, Enum):
    MOVE_IN = "move_in"
    MOVE_OUT = "move_out"
    ROUTINE_QUARTERLY = "routine_quarterly"
    ANNUAL_SAFETY = "annual_safety"
    HVAC_SEASONAL = "hvac_seasonal"


class InspectionItemStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ATTENTION_NEEDED = "attention_needed"
    NOT_APPLICABLE = "not_applicable"


class InspectionChecklistItem(BaseModel):
    item_name: str
    category: MaintenanceCategory
    status: InspectionItemStatus
    notes: str | None = None
    severity_if_failed: WorkOrderPriority = WorkOrderPriority.MEDIUM


class InspectionReportCreate(BaseModel):
    unit_id: str
    inspection_type: InspectionType
    inspector_name: str
    inspection_date: date | None = None
    items: list[InspectionChecklistItem]
    overall_notes: str | None = None


class InspectionAnalysisResult(BaseModel):
    inspection_id: str
    property_id: str
    unit_id: str
    inspection_type: InspectionType
    inspection_date: date
    items_inspected: int
    items_passed: int
    items_failed: int
    generated_work_orders: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[MaintenanceFinding] = Field(default_factory=list)
    summary: str


# --- 4. Preventive Maintenance ---

class PreventiveFrequency(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"
    CUSTOM_DAYS = "custom_days"


class PreventiveTaskCreate(BaseModel):
    title: str
    category: MaintenanceCategory
    frequency: PreventiveFrequency
    interval_days: int = Field(default=90, ge=1)
    unit_id: str | None = None
    asset_id: str | None = None
    description: str = ""
    next_due_date: date
    estimated_cost: Decimal | None = None


class PreventiveTaskRecord(BaseModel):
    task_id: str
    property_id: str
    title: str
    category: MaintenanceCategory
    frequency: PreventiveFrequency
    interval_days: int
    unit_id: str | None = None
    asset_id: str | None = None
    description: str = ""
    last_performed_date: date | None = None
    next_due_date: date
    is_overdue: bool = False
    days_until_due: int


# --- 5. Asset & Inventory Tracking ---

class AssetCategory(str, Enum):
    HVAC = "hvac"
    WATER_HEATER = "water_heater"
    REFRIGERATOR = "refrigerator"
    DISHWASHER = "dishwasher"
    STOVE_OVEN = "stove_oven"
    WASHER_DRYER = "washer_dryer"
    ELECTRICAL_PANEL = "electrical_panel"
    ROOF = "roof"
    PLUMBING_PUMP = "plumbing_pump"
    OTHER = "other"


class AssetCreate(BaseModel):
    name: str = Field(min_length=1)
    asset_category: AssetCategory
    unit_id: str | None = None
    make_model: str | None = None
    serial_number: str | None = None
    install_date: date | None = None
    warranty_expiry_date: date | None = None
    purchase_cost: Decimal | None = None
    expected_lifespan_years: int | None = Field(default=10, ge=1)


class AssetRecord(BaseModel):
    asset_id: str
    property_id: str
    unit_id: str | None = None
    name: str
    asset_category: AssetCategory
    make_model: str | None = None
    serial_number: str | None = None
    install_date: date | None = None
    warranty_expiry_date: date | None = None
    purchase_cost: Decimal | None = None
    expected_lifespan_years: int | None = None
    total_maintenance_spend: Decimal = Decimal("0.00")
    work_orders_count: int = 0
    is_warranty_active: bool = False
    health_status: str = "good"  # "good", "attention_needed", "replace_recommended"


# --- 6. Cost Tracking & Reporting ---

class CategoryCostSummary(BaseModel):
    category: str
    work_order_count: int
    total_estimated_cost: Decimal
    total_actual_cost: Decimal
    variance: Decimal


class MaintenanceCostAnalysisResult(BaseModel):
    property_id: str
    as_of_date: date
    total_work_orders: int
    completed_work_orders: int
    total_estimated_spend: Decimal
    total_actual_spend: Decimal
    budget_variance: Decimal
    cost_overrun_work_orders: list[str] = Field(default_factory=list)
    category_breakdown: list[CategoryCostSummary] = Field(default_factory=list)
    top_spending_units: list[dict[str, Any]] = Field(default_factory=list)
    top_spending_vendors: list[dict[str, Any]] = Field(default_factory=list)
    summary: str


# --- 7. Full Operations Analysis ---

class MaintenanceOperationsAnalysisRequest(BaseModel):
    as_of_date: date | None = None
    lookback_days: int = Field(default=90, ge=1, le=365)
    overdue_sla_hours_threshold: int = Field(default=48, ge=1)


class MaintenanceOperationsAnalysisResult(BaseModel):
    property_id: str
    analysis_date: date
    open_work_orders_count: int
    emergency_count: int
    overdue_work_orders_count: int
    preventive_tasks_due_count: int
    assets_tracked_count: int
    findings: list[MaintenanceFinding] = Field(default_factory=list)
    summary: str
