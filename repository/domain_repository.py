"""SQLite implementation of the shared domain repository."""

from __future__ import annotations
from datetime import datetime
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Generator

from repository.exceptions import (
    CrossPropertyReferenceError,
    EntityNotFoundError,
    IntegrityConflictError,
    InvalidOperationError,
)
from schemas.domain import (
    DocumentMetadataCreate,
    DocumentMetadataRecord,
    ExpenseCategory,
    ExpenseCreate,
    ExpenseRecord,
    ExpenseStatus,
    ExpenseUpdate,
    LeaseCreate,
    LeaseRecord,
    LeaseStatus,
    LeaseUpdate,
    PaymentCreate,
    PaymentMethod,
    PaymentRecord,
    PaymentStatus,
    PaymentUpdate,
    PropertyRecord,
    PropertyUpdate,
    ResidentCreate,
    ResidentRecord,
    ResidentStatus,
    ResidentUpdate,
    UnitCreate,
    UnitRecord,
    UnitStatus,
    UnitUpdate,
    VendorCategory,
    VendorCreate,
    VendorRecord,
    VendorUpdate,
    WorkOrderCreate,
    WorkOrderPriority,
    WorkOrderRecord,
    WorkOrderStatus,
    WorkOrderUpdate,
    MaintenanceRequestCreate,
    MaintenanceRequestRecord,
    MaintenanceRequestStatus,
    MaintenanceRequestUpdate,
    SlaEscalationCreate,
    SlaEscalationRecord,
    SlaEscalationStatus,
    SlaEscalationType,
    SlaEscalationUpdate,
    SlaMonitoringResult,
    BankStatementRecord,
    BankTransactionMatchStatus,
    BankTransactionRecord,
    ComplianceCategory,
    ComplianceFindingRecord,
    ComplianceSeverity,
    ComplianceStatus,
    ExpenseApprovalStatus,
    InvoiceCreate,
    InvoiceRecord,
    InvoiceStatus,
    InvoiceType,
    InvoiceUpdate,
    NotificationDeliveryMode,
    NotificationRecord,
    NotificationStatus,
    NotificationType,
)
from schemas.auth import UserRole
from schemas.maintenance_agent import (
    AssetCategory,
    AssetCreate,
    AssetRecord,
    InspectionChecklistItem,
    InspectionItemStatus,
    InspectionReportCreate,
    InspectionType,
    MaintenanceCategory,
    PreventiveFrequency,
    PreventiveTaskCreate,
    PreventiveTaskRecord,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}"


def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


class DomainRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.database_path,
            timeout=10,
            isolation_level=None,  # Autocommit mode by default; manual transactions with BEGIN
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        if self.database_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for multi-operation atomic transactions."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS properties (
                    property_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    address TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS units (
                    unit_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    unit_number TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rent_amount TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(property_id, unit_number)
                );
                CREATE INDEX IF NOT EXISTS idx_units_property ON units(property_id);

                CREATE TABLE IF NOT EXISTS residents (
                    resident_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_residents_property ON residents(property_id);

                CREATE TABLE IF NOT EXISTS vendors (
                    vendor_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    email TEXT,
                    phone TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_vendors_property ON vendors(property_id);

                CREATE TABLE IF NOT EXISTS document_metadata (
                    document_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    sha256_checksum TEXT NOT NULL,
                    associated_entity_type TEXT,
                    associated_entity_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_doc_property ON document_metadata(property_id);

                CREATE TABLE IF NOT EXISTS leases (
                    lease_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    unit_id TEXT NOT NULL REFERENCES units(unit_id) ON DELETE RESTRICT,
                    resident_id TEXT NOT NULL REFERENCES residents(resident_id) ON DELETE RESTRICT,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    rent_amount TEXT NOT NULL,
                    deposit_amount TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_leases_property ON leases(property_id);
                CREATE INDEX IF NOT EXISTS idx_leases_unit ON leases(unit_id);
                CREATE INDEX IF NOT EXISTS idx_leases_resident ON leases(resident_id);

                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    payment_date TEXT NOT NULL,
                    payment_method TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resident_id TEXT REFERENCES residents(resident_id) ON DELETE SET NULL,
                    lease_id TEXT REFERENCES leases(lease_id) ON DELETE SET NULL,
                    reference_number TEXT,
                    document_id TEXT REFERENCES document_metadata(document_id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_payments_property ON payments(property_id);
                CREATE INDEX IF NOT EXISTS idx_payments_resident ON payments(resident_id);
                CREATE INDEX IF NOT EXISTS idx_payments_lease ON payments(lease_id);

                CREATE TABLE IF NOT EXISTS expenses (
                    expense_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    expense_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    description TEXT NOT NULL,
                    vendor_id TEXT REFERENCES vendors(vendor_id) ON DELETE SET NULL,
                    unit_id TEXT REFERENCES units(unit_id) ON DELETE SET NULL,
                    document_id TEXT REFERENCES document_metadata(document_id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_expenses_property ON expenses(property_id);
                CREATE INDEX IF NOT EXISTS idx_expenses_vendor ON expenses(vendor_id);
                CREATE INDEX IF NOT EXISTS idx_expenses_unit ON expenses(unit_id);

                CREATE TABLE IF NOT EXISTS work_orders (
                    work_order_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    unit_id TEXT NOT NULL REFERENCES units(unit_id) ON DELETE RESTRICT,
                    resident_id TEXT REFERENCES residents(resident_id) ON DELETE SET NULL,
                    vendor_id TEXT REFERENCES vendors(vendor_id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    estimated_cost TEXT,
                    actual_cost TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_work_orders_property ON work_orders(property_id);
                CREATE INDEX IF NOT EXISTS idx_work_orders_unit ON work_orders(unit_id);

                CREATE TABLE IF NOT EXISTS maintenance_requests (
                    request_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL
                        REFERENCES properties(property_id) ON DELETE CASCADE,
                    unit_id TEXT NOT NULL
                        REFERENCES units(unit_id) ON DELETE RESTRICT,
                    resident_id TEXT
                        REFERENCES residents(resident_id) ON DELETE SET NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'submitted',
                    priority TEXT,
                    category TEXT,
                    is_emergency INTEGER NOT NULL DEFAULT 0,
                    estimated_sla_hours INTEGER,
                    sla_deadline_at TEXT,
                    work_order_id TEXT
                        REFERENCES work_orders(work_order_id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_maintenance_requests_property
                    ON maintenance_requests(property_id);

                CREATE INDEX IF NOT EXISTS idx_maintenance_requests_unit
                    ON maintenance_requests(unit_id);

                CREATE INDEX IF NOT EXISTS idx_maintenance_requests_status
                    ON maintenance_requests(property_id, status);



                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    unit_id TEXT REFERENCES units(unit_id) ON DELETE SET NULL,
                    name TEXT NOT NULL,
                    asset_category TEXT NOT NULL,
                    make_model TEXT,
                    serial_number TEXT,
                    install_date TEXT,
                    warranty_expiry_date TEXT,
                    purchase_cost TEXT,
                    expected_lifespan_years INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assets_property ON assets(property_id);
                CREATE INDEX IF NOT EXISTS idx_assets_unit ON assets(unit_id);

                CREATE TABLE IF NOT EXISTS preventive_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    unit_id TEXT REFERENCES units(unit_id) ON DELETE SET NULL,
                    asset_id TEXT REFERENCES assets(asset_id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    interval_days INTEGER NOT NULL,
                    description TEXT,
                    last_performed_date TEXT,
                    next_due_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_preventive_property ON preventive_schedules(property_id);

                CREATE TABLE IF NOT EXISTS inspections (
                    inspection_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    unit_id TEXT NOT NULL REFERENCES units(unit_id) ON DELETE CASCADE,
                    inspection_type TEXT NOT NULL,
                    inspector_name TEXT NOT NULL,
                    inspection_date TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    overall_notes TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_inspections_property ON inspections(property_id);
                CREATE INDEX IF NOT EXISTS idx_inspections_unit ON inspections(unit_id);

                CREATE TABLE IF NOT EXISTS sla_escalations (
                    escalation_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL REFERENCES maintenance_requests(request_id) ON DELETE CASCADE,
                    escalation_type TEXT NOT NULL,
                    sla_deadline_at TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sla_escalations_property
                    ON sla_escalations(property_id);

                CREATE INDEX IF NOT EXISTS idx_sla_escalations_request
                    ON sla_escalations(request_id);

                CREATE INDEX IF NOT EXISTS idx_sla_escalations_property_status
                    ON sla_escalations(property_id, status);

                CREATE INDEX IF NOT EXISTS idx_sla_escalations_property_type
                    ON sla_escalations(property_id, escalation_type);

                CREATE UNIQUE INDEX IF NOT EXISTS uq_sla_escalations_active
                    ON sla_escalations(request_id, escalation_type)
                    WHERE status IN ('open', 'acknowledged');

                CREATE TABLE IF NOT EXISTS invoices (
                    invoice_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    lease_id TEXT REFERENCES leases(lease_id) ON DELETE SET NULL,
                    resident_id TEXT REFERENCES residents(resident_id) ON DELETE SET NULL,
                    vendor_id TEXT REFERENCES vendors(vendor_id) ON DELETE SET NULL,
                    invoice_number TEXT NOT NULL,
                    invoice_type TEXT NOT NULL DEFAULT 'rent',
                    amount TEXT NOT NULL,
                    amount_paid TEXT NOT NULL DEFAULT '0.00',
                    currency TEXT NOT NULL DEFAULT 'USD',
                    issue_date TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unpaid',
                    description TEXT,
                    document_id TEXT REFERENCES document_metadata(document_id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(property_id, invoice_number)
                );
                CREATE INDEX IF NOT EXISTS idx_invoices_property ON invoices(property_id);
                CREATE INDEX IF NOT EXISTS idx_invoices_lease ON invoices(lease_id);
                CREATE INDEX IF NOT EXISTS idx_invoices_resident ON invoices(resident_id);
                CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(property_id, status);
                CREATE INDEX IF NOT EXISTS idx_invoices_due ON invoices(property_id, due_date);

                CREATE TABLE IF NOT EXISTS bank_statements (
                    statement_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    account_identifier TEXT,
                    filename TEXT NOT NULL,
                    opening_balance TEXT,
                    closing_balance TEXT,
                    statement_start_date TEXT,
                    statement_end_date TEXT,
                    total_transactions INTEGER NOT NULL DEFAULT 0,
                    reconciliation_status TEXT NOT NULL DEFAULT 'pending',
                    file_checksum TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bank_statements_property ON bank_statements(property_id);

                CREATE TABLE IF NOT EXISTS bank_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    statement_id TEXT NOT NULL REFERENCES bank_statements(statement_id) ON DELETE CASCADE,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    transaction_date TEXT NOT NULL,
                    description TEXT NOT NULL,
                    reference TEXT,
                    amount TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    match_status TEXT NOT NULL DEFAULT 'unmatched',
                    matched_entity_type TEXT,
                    matched_entity_id TEXT,
                    match_confidence REAL,
                    match_notes TEXT,
                    reconciled_at TEXT,
                    reconciled_by TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bank_transactions_property ON bank_transactions(property_id);
                CREATE INDEX IF NOT EXISTS idx_bank_transactions_statement ON bank_transactions(statement_id);
                CREATE INDEX IF NOT EXISTS idx_bank_transactions_status ON bank_transactions(property_id, match_status);

                CREATE TABLE IF NOT EXISTS compliance_findings (
                    finding_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    triggering_rule TEXT NOT NULL,
                    affected_record_ids TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    assigned_to TEXT,
                    resolution_reason TEXT,
                    resolved_at TEXT,
                    escalation_id TEXT REFERENCES sla_escalations(escalation_id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_compliance_property ON compliance_findings(property_id);
                CREATE INDEX IF NOT EXISTS idx_compliance_status ON compliance_findings(property_id, status);
                CREATE INDEX IF NOT EXISTS idx_compliance_severity ON compliance_findings(property_id, severity);

                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
                    recipient_email TEXT NOT NULL,
                    recipient_name TEXT,
                    subject TEXT NOT NULL,
                    body_text TEXT NOT NULL,
                    body_html TEXT,
                    notification_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    delivery_mode TEXT NOT NULL DEFAULT 'simulated',
                    idempotency_key TEXT UNIQUE,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    error_message TEXT,
                    sent_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_property ON notifications(property_id);
                CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(property_id, status);
                """
            )
            # Add the SLA deadline column to existing databases if missing.
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(maintenance_requests)"
                ).fetchall()
            }

            if "sla_deadline_at" not in columns:
                conn.execute(
                    "ALTER TABLE maintenance_requests ADD COLUMN sla_deadline_at TEXT"
                )

            # Safe migrations for sla_escalations table if columns missing
            escalation_cols = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(sla_escalations)"
                ).fetchall()
            }
            if escalation_cols and "notes" not in escalation_cols:
                conn.execute(
                    "ALTER TABLE sla_escalations ADD COLUMN notes TEXT"
                )

            # Safe migrations for expenses table if approval columns missing
            expense_cols = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(expenses)"
                ).fetchall()
            }
            if expense_cols and "approval_status" not in expense_cols:
                conn.execute(
                    "ALTER TABLE expenses ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'pending'"
                )
            if expense_cols and "approved_by" not in expense_cols:
                conn.execute(
                    "ALTER TABLE expenses ADD COLUMN approved_by TEXT"
                )
            if expense_cols and "approved_at" not in expense_cols:
                conn.execute(
                    "ALTER TABLE expenses ADD COLUMN approved_at TEXT"
                )
            if expense_cols and "decision_reason" not in expense_cols:
                conn.execute(
                    "ALTER TABLE expenses ADD COLUMN decision_reason TEXT"
                )

            # Safe migrations for properties table if updated_at / management_fee_rate missing
            prop_cols = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(properties)"
                ).fetchall()
            }
            if prop_cols and "updated_at" not in prop_cols:
                conn.execute(
                    "ALTER TABLE properties ADD COLUMN updated_at TEXT"
                )
            if prop_cols and "management_fee_rate" not in prop_cols:
                conn.execute(
                    "ALTER TABLE properties ADD COLUMN management_fee_rate TEXT"
                )

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    def create_property(
        self,
        *,
        name: str,
        property_id: str | None = None,
        address: str | None = None,
        management_fee_rate: Decimal | None = None,
    ) -> PropertyRecord:
        pid = property_id or str(uuid.uuid4())
        now = _utc_now().isoformat()
        mgmt_str = _format_decimal(management_fee_rate)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO properties (property_id, name, address, created_at, updated_at, management_fee_rate)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (pid, name.strip(), address.strip() if address else None, now, now, mgmt_str),
                )
        except sqlite3.OperationalError:
            # Fallback if table was created in an older schema without management_fee_rate
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO properties (property_id, name, address, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (pid, name.strip(), address.strip() if address else None, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError(f"Property with ID '{pid}' already exists.") from exc

        return PropertyRecord(
            property_id=pid,
            name=name.strip(),
            address=address.strip() if address else None,
            management_fee_rate=management_fee_rate,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_property(self, property_id: str) -> PropertyRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM properties WHERE property_id = ?",
                (property_id,),
            ).fetchone()
        if not row:
            return None
        created_dt = datetime.fromisoformat(row["created_at"])
        updated_raw = row["updated_at"] if "updated_at" in row.keys() else None
        updated_dt = datetime.fromisoformat(updated_raw) if updated_raw else created_dt
        mgmt_raw = row["management_fee_rate"] if "management_fee_rate" in row.keys() else None
        mgmt_rate = _parse_decimal(mgmt_raw) if mgmt_raw else None
        return PropertyRecord(
            property_id=row["property_id"],
            name=row["name"],
            address=row["address"],
            management_fee_rate=mgmt_rate,
            created_at=created_dt,
            updated_at=updated_dt,
        )

    def list_properties(self, limit: int = 100, offset: int = 0) -> list[PropertyRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM properties ORDER BY name ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        results: list[PropertyRecord] = []
        for row in rows:
            created_dt = datetime.fromisoformat(row["created_at"])
            updated_raw = row["updated_at"] if "updated_at" in row.keys() else None
            updated_dt = datetime.fromisoformat(updated_raw) if updated_raw else created_dt
            mgmt_raw = row["management_fee_rate"] if "management_fee_rate" in row.keys() else None
            mgmt_rate = _parse_decimal(mgmt_raw) if mgmt_raw else None
            results.append(
                PropertyRecord(
                    property_id=row["property_id"],
                    name=row["name"],
                    address=row["address"],
                    management_fee_rate=mgmt_rate,
                    created_at=created_dt,
                    updated_at=updated_dt,
                )
            )
        return results

    def ensure_property(
        self,
        *,
        property_id: str,
        name: str,
        address: str | None = None,
        management_fee_rate: Decimal | None = None,
    ) -> PropertyRecord:
        """Ensure a property exists in the domain DB (e.g. synced from auth membership)."""
        prop = self.get_property(property_id)
        if prop:
            return prop
        return self.create_property(
            name=name,
            property_id=property_id,
            address=address,
            management_fee_rate=management_fee_rate,
        )

    def update_property(self, property_id: str, data: PropertyUpdate) -> PropertyRecord:
        prop = self.get_property(property_id)
        if not prop:
            raise EntityNotFoundError(f"Property '{property_id}' not found.")

        clauses: list[str] = []
        params: list[Any] = []

        if data.name is not None:
            clauses.append("name = ?")
            params.append(data.name.strip())
        if data.address is not None:
            clauses.append("address = ?")
            params.append(data.address.strip() if data.address else None)
        if data.management_fee_rate is not None:
            clauses.append("management_fee_rate = ?")
            params.append(_format_decimal(data.management_fee_rate))

        if not clauses:
            return prop

        now = _utc_now().isoformat()
        clauses.append("updated_at = ?")
        params.append(now)

        params.append(property_id)
        sql = f"UPDATE properties SET {', '.join(clauses)} WHERE property_id = ?"

        try:
            with self._connect() as conn:
                conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError("Property update violated database integrity.") from exc

        updated = self.get_property(property_id)
        if not updated:
            raise EntityNotFoundError(f"Property '{property_id}' not found.")
        return updated

    # -------------------------------------------------------------------------
    # Units
    # -------------------------------------------------------------------------

    def create_unit(self, property_id: str, data: UnitCreate) -> UnitRecord:
        self._ensure_property_exists(property_id)
        uid = str(uuid.uuid4())
        now = _utc_now().isoformat()
        rent_str = _format_decimal(data.rent_amount)

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO units (unit_id, property_id, unit_number, status, rent_amount, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (uid, property_id, data.unit_number.strip(), data.status.value, rent_str, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError(
                f"Unit '{data.unit_number}' already exists in property '{property_id}'."
            ) from exc

        return UnitRecord(
            unit_id=uid,
            property_id=property_id,
            unit_number=data.unit_number.strip(),
            status=data.status,
            rent_amount=data.rent_amount,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_unit(self, property_id: str, unit_id: str) -> UnitRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM units WHERE unit_id = ? AND property_id = ?",
                (unit_id, property_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_unit(row)

    def update_unit(self, property_id: str, unit_id: str, update: UnitUpdate) -> UnitRecord:
        unit = self.get_unit(property_id, unit_id)
        if not unit:
            raise EntityNotFoundError(f"Unit '{unit_id}' not found in property '{property_id}'.")

        clauses: list[str] = []
        params: list[Any] = []

        if update.unit_number is not None:
            clauses.append("unit_number = ?")
            params.append(update.unit_number.strip())
        if update.status is not None:
            clauses.append("status = ?")
            params.append(update.status.value)
        if update.rent_amount is not None:
            clauses.append("rent_amount = ?")
            params.append(_format_decimal(update.rent_amount))

        if not clauses:
            return unit

        now = _utc_now().isoformat()
        clauses.append("updated_at = ?")
        params.append(now)

        params.extend([unit_id, property_id])
        sql = f"UPDATE units SET {', '.join(clauses)} WHERE unit_id = ? AND property_id = ?"

        try:
            with self._connect() as conn:
                conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError("Unit update violated database integrity.") from exc

        updated = self.get_unit(property_id, unit_id)
        if not updated:
            raise EntityNotFoundError(f"Unit '{unit_id}' not found.")
        return updated

    def list_units(
        self,
        property_id: str,
        status: UnitStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UnitRecord]:
        self._ensure_property_exists(property_id)
        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM units {where_sql} ORDER BY unit_number ASC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row_to_unit(r) for r in rows]

    # -------------------------------------------------------------------------
    # Residents
    # -------------------------------------------------------------------------

    def create_resident(self, property_id: str, data: ResidentCreate) -> ResidentRecord:
        self._ensure_property_exists(property_id)
        rid = str(uuid.uuid4())
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO residents (resident_id, property_id, first_name, last_name, email, phone, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    property_id,
                    data.first_name.strip(),
                    data.last_name.strip(),
                    data.email,
                    data.phone.strip() if data.phone else None,
                    data.status.value,
                    now,
                    now,
                ),
            )

        return ResidentRecord(
            resident_id=rid,
            property_id=property_id,
            first_name=data.first_name.strip(),
            last_name=data.last_name.strip(),
            email=data.email,
            phone=data.phone.strip() if data.phone else None,
            status=data.status,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_resident(self, property_id: str, resident_id: str) -> ResidentRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM residents WHERE resident_id = ? AND property_id = ?",
                (resident_id, property_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_resident(row)

    def list_residents(
        self,
        property_id: str,
        status: ResidentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ResidentRecord]:
        self._ensure_property_exists(property_id)
        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM residents {where_sql} ORDER BY last_name ASC, first_name ASC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row_to_resident(r) for r in rows]

    def find_residents_by_email(self, email: str) -> list[tuple[str, str]]:
        """Return exact normalized email matches as (property_id, resident_id)."""
        normalized = email.strip().lower()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT property_id, resident_id FROM residents WHERE lower(email) = ? ORDER BY property_id, resident_id",
                (normalized,),
            ).fetchall()
        return [(row["property_id"], row["resident_id"]) for row in rows]

    def update_resident(
        self,
        property_id: str,
        resident_id: str,
        update: ResidentUpdate,
    ) -> ResidentRecord:
        res = self.get_resident(property_id, resident_id)
        if not res:
            raise EntityNotFoundError(f"Resident '{resident_id}' not found in property '{property_id}'.")

        clauses: list[str] = []
        params: list[Any] = []

        if update.first_name is not None:
            clauses.append("first_name = ?")
            params.append(update.first_name.strip())
        if update.last_name is not None:
            clauses.append("last_name = ?")
            params.append(update.last_name.strip())
        if update.email is not None:
            clauses.append("email = ?")
            params.append(update.email)
        if update.phone is not None:
            clauses.append("phone = ?")
            params.append(update.phone.strip() if update.phone else None)
        if update.status is not None:
            clauses.append("status = ?")
            params.append(update.status.value)

        if not clauses:
            return res

        now = _utc_now().isoformat()
        clauses.append("updated_at = ?")
        params.append(now)

        params.extend([resident_id, property_id])
        sql = f"UPDATE residents SET {', '.join(clauses)} WHERE resident_id = ? AND property_id = ?"

        try:
            with self._connect() as conn:
                conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError("Resident update violated database integrity.") from exc

        updated = self.get_resident(property_id, resident_id)
        if not updated:
            raise EntityNotFoundError(f"Resident '{resident_id}' not found.")
        return updated

    # -------------------------------------------------------------------------
    # Leases
    # -------------------------------------------------------------------------

    def create_lease(self, property_id: str, data: LeaseCreate) -> LeaseRecord:
        self._ensure_property_exists(property_id)

        # 1. Enforce unit belongs to the same property
        unit = self.get_unit(property_id, data.unit_id)
        if not unit:
            # Check if unit belongs to another property
            other_prop = self._find_property_for_entity("units", "unit_id", data.unit_id)
            if other_prop:
                raise CrossPropertyReferenceError(
                    f"Unit '{data.unit_id}' belongs to property '{other_prop}', not '{property_id}'."
                )
            raise EntityNotFoundError(f"Unit '{data.unit_id}' not found.")

        # 2. Enforce resident belongs to the same property
        resident = self.get_resident(property_id, data.resident_id)
        if not resident:
            other_prop = self._find_property_for_entity("residents", "resident_id", data.resident_id)
            if other_prop:
                raise CrossPropertyReferenceError(
                    f"Resident '{data.resident_id}' belongs to property '{other_prop}', not '{property_id}'."
                )
            raise EntityNotFoundError(f"Resident '{data.resident_id}' not found.")

        lid = str(uuid.uuid4())
        now = _utc_now().isoformat()
        rent_str = _format_decimal(data.rent_amount)
        if not rent_str:
            raise InvalidOperationError("Rent amount is required.")
        dep_str = _format_decimal(data.deposit_amount)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO leases (
                    lease_id, property_id, unit_id, resident_id, start_date, end_date,
                    rent_amount, deposit_amount, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lid,
                    property_id,
                    data.unit_id,
                    data.resident_id,
                    data.start_date.isoformat(),
                    data.end_date.isoformat(),
                    rent_str,
                    dep_str,
                    data.status.value,
                    now,
                    now,
                ),
            )

        return LeaseRecord(
            lease_id=lid,
            property_id=property_id,
            unit_id=data.unit_id,
            resident_id=data.resident_id,
            start_date=data.start_date,
            end_date=data.end_date,
            rent_amount=data.rent_amount,
            deposit_amount=data.deposit_amount,
            status=data.status,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_lease(self, property_id: str, lease_id: str) -> LeaseRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM leases WHERE lease_id = ? AND property_id = ?",
                (lease_id, property_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_lease(row)

    def list_leases(
        self,
        property_id: str,
        status: LeaseStatus | None = None,
        unit_id: str | None = None,
        resident_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LeaseRecord]:
        self._ensure_property_exists(property_id)
        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if unit_id is not None:
            clauses.append("unit_id = ?")
            params.append(unit_id)
        if resident_id is not None:
            clauses.append("resident_id = ?")
            params.append(resident_id)

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM leases {where_sql} ORDER BY start_date DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row_to_lease(r) for r in rows]

    def update_lease(
        self,
        property_id: str,
        lease_id: str,
        update: LeaseUpdate,
    ) -> LeaseRecord:
        lease = self.get_lease(property_id, lease_id)
        if not lease:
            raise EntityNotFoundError(f"Lease '{lease_id}' not found in property '{property_id}'.")

        clauses: list[str] = []
        params: list[Any] = []

        if update.end_date is not None:
            clauses.append("end_date = ?")
            params.append(update.end_date.isoformat())
        if update.rent_amount is not None:
            clauses.append("rent_amount = ?")
            params.append(_format_decimal(update.rent_amount))
        if update.deposit_amount is not None:
            clauses.append("deposit_amount = ?")
            params.append(_format_decimal(update.deposit_amount))
        if update.status is not None:
            clauses.append("status = ?")
            params.append(update.status.value)

        if not clauses:
            return lease

        now = _utc_now().isoformat()
        clauses.append("updated_at = ?")
        params.append(now)

        params.extend([lease_id, property_id])
        sql = f"UPDATE leases SET {', '.join(clauses)} WHERE lease_id = ? AND property_id = ?"

        try:
            with self._connect() as conn:
                conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError("Lease update violated database integrity.") from exc

        updated = self.get_lease(property_id, lease_id)
        if not updated:
            raise EntityNotFoundError(f"Lease '{lease_id}' not found.")
        return updated

    # -------------------------------------------------------------------------
    # Vendors
    # -------------------------------------------------------------------------

    def create_vendor(self, property_id: str, data: VendorCreate) -> VendorRecord:
        self._ensure_property_exists(property_id)
        vid = str(uuid.uuid4())
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vendors (vendor_id, property_id, name, category, email, phone, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vid,
                    property_id,
                    data.name.strip(),
                    data.category.value,
                    data.email.strip() if data.email else None,
                    data.phone.strip() if data.phone else None,
                    1 if data.is_active else 0,
                    now,
                    now,
                ),
            )

        return VendorRecord(
            vendor_id=vid,
            property_id=property_id,
            name=data.name.strip(),
            category=data.category,
            email=data.email.strip() if data.email else None,
            phone=data.phone.strip() if data.phone else None,
            is_active=data.is_active,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_vendor(self, property_id: str, vendor_id: str) -> VendorRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM vendors WHERE vendor_id = ? AND property_id = ?",
                (vendor_id, property_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_vendor(row)

    def list_vendors(
        self,
        property_id: str,
        category: VendorCategory | None = None,
        is_active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[VendorRecord]:
        self._ensure_property_exists(property_id)
        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if category is not None:
            clauses.append("category = ?")
            params.append(category.value)
        if is_active is not None:
            clauses.append("is_active = ?")
            params.append(1 if is_active else 0)

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM vendors {where_sql} ORDER BY name ASC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row_to_vendor(r) for r in rows]

    def update_vendor(
        self,
        property_id: str,
        vendor_id: str,
        update: VendorUpdate,
    ) -> VendorRecord:
        vendor = self.get_vendor(property_id, vendor_id)
        if not vendor:
            raise EntityNotFoundError(f"Vendor '{vendor_id}' not found in property '{property_id}'.")

        clauses: list[str] = []
        params: list[Any] = []

        if update.name is not None:
            clauses.append("name = ?")
            params.append(update.name.strip())
        if update.category is not None:
            clauses.append("category = ?")
            params.append(update.category.value)
        if update.email is not None:
            clauses.append("email = ?")
            params.append(update.email)
        if update.phone is not None:
            clauses.append("phone = ?")
            params.append(update.phone.strip() if update.phone else None)
        if update.is_active is not None:
            clauses.append("is_active = ?")
            params.append(1 if update.is_active else 0)

        if not clauses:
            return vendor

        now = _utc_now().isoformat()
        clauses.append("updated_at = ?")
        params.append(now)

        params.extend([vendor_id, property_id])
        sql = f"UPDATE vendors SET {', '.join(clauses)} WHERE vendor_id = ? AND property_id = ?"

        try:
            with self._connect() as conn:
                conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError("Vendor update violated database integrity.") from exc

        updated = self.get_vendor(property_id, vendor_id)
        if not updated:
            raise EntityNotFoundError(f"Vendor '{vendor_id}' not found.")
        return updated

    # -------------------------------------------------------------------------
    # Document Metadata
    # -------------------------------------------------------------------------

    def create_document_metadata(self, property_id: str, data: DocumentMetadataCreate) -> DocumentMetadataRecord:
        self._ensure_property_exists(property_id)
        did = str(uuid.uuid4())
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO document_metadata (
                    document_id, property_id, filename, file_type, storage_path,
                    file_size_bytes, sha256_checksum, associated_entity_type,
                    associated_entity_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    did,
                    property_id,
                    data.filename.strip(),
                    data.file_type.strip(),
                    data.storage_path.strip(),
                    data.file_size_bytes,
                    data.sha256_checksum.strip(),
                    data.associated_entity_type,
                    data.associated_entity_id,
                    now,
                ),
            )

        return DocumentMetadataRecord(
            document_id=did,
            property_id=property_id,
            filename=data.filename.strip(),
            file_type=data.file_type.strip(),
            storage_path=data.storage_path.strip(),
            file_size_bytes=data.file_size_bytes,
            sha256_checksum=data.sha256_checksum.strip(),
            associated_entity_type=data.associated_entity_type,
            associated_entity_id=data.associated_entity_id,
            created_at=datetime.fromisoformat(now),
        )

    def get_document_metadata(self, property_id: str, document_id: str) -> DocumentMetadataRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM document_metadata WHERE document_id = ? AND property_id = ?",
                (document_id, property_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_document(row)

    def list_document_metadata(
        self,
        property_id: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentMetadataRecord]:
        self._ensure_property_exists(property_id)
        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if entity_type is not None:
            clauses.append("associated_entity_type = ?")
            params.append(entity_type)
        if entity_id is not None:
            clauses.append("associated_entity_id = ?")
            params.append(entity_id)

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM document_metadata {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row_to_document(r) for r in rows]

    # -------------------------------------------------------------------------
    # Payments
    # -------------------------------------------------------------------------

    def create_payment(self, property_id: str, data: PaymentCreate) -> PaymentRecord:
        self._ensure_property_exists(property_id)

        if data.resident_id:
            resident = self.get_resident(property_id, data.resident_id)
            if not resident:
                other_prop = self._find_property_for_entity("residents", "resident_id", data.resident_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Resident '{data.resident_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Resident '{data.resident_id}' not found.")

        if data.lease_id:
            lease = self.get_lease(property_id, data.lease_id)
            if not lease:
                other_prop = self._find_property_for_entity("leases", "lease_id", data.lease_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Lease '{data.lease_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Lease '{data.lease_id}' not found.")

        if data.document_id:
            doc = self.get_document_metadata(property_id, data.document_id)
            if not doc:
                other_prop = self._find_property_for_entity("document_metadata", "document_id", data.document_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Document '{data.document_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Document '{data.document_id}' not found.")

        pid = str(uuid.uuid4())
        now = _utc_now().isoformat()
        amt_str = _format_decimal(data.amount)
        if not amt_str:
            raise InvalidOperationError("Amount is required.")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO payments (
                    payment_id, property_id, amount, currency, payment_date,
                    payment_method, status, resident_id, lease_id, reference_number,
                    document_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    property_id,
                    amt_str,
                    data.currency,
                    data.payment_date.isoformat(),
                    data.payment_method.value,
                    data.status.value,
                    data.resident_id,
                    data.lease_id,
                    data.reference_number,
                    data.document_id,
                    now,
                    now,
                ),
            )

        return PaymentRecord(
            payment_id=pid,
            property_id=property_id,
            amount=data.amount,
            currency=data.currency,
            payment_date=data.payment_date,
            payment_method=data.payment_method,
            status=data.status,
            resident_id=data.resident_id,
            lease_id=data.lease_id,
            reference_number=data.reference_number,
            document_id=data.document_id,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_payment(self, property_id: str, payment_id: str) -> PaymentRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payments WHERE payment_id = ? AND property_id = ?",
                (payment_id, property_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_payment(row)

    def list_payments(
        self,
        property_id: str,
        status: PaymentStatus | None = None,
        resident_id: str | None = None,
        lease_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PaymentRecord]:
        self._ensure_property_exists(property_id)
        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if resident_id is not None:
            clauses.append("resident_id = ?")
            params.append(resident_id)
        if lease_id is not None:
            clauses.append("lease_id = ?")
            params.append(lease_id)

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM payments {where_sql} ORDER BY payment_date DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row_to_payment(r) for r in rows]

    def update_payment(
        self,
        property_id: str,
        payment_id: str,
        update: PaymentUpdate,
    ) -> PaymentRecord:
        pmt = self.get_payment(property_id, payment_id)
        if not pmt:
            raise EntityNotFoundError(f"Payment '{payment_id}' not found in property '{property_id}'.")

        clauses: list[str] = []
        params: list[Any] = []

        if update.status is not None:
            clauses.append("status = ?")
            params.append(update.status.value)
        if update.reference_number is not None:
            clauses.append("reference_number = ?")
            params.append(update.reference_number.strip() if update.reference_number else None)

        if not clauses:
            return pmt

        now = _utc_now().isoformat()
        clauses.append("updated_at = ?")
        params.append(now)

        params.extend([payment_id, property_id])
        sql = f"UPDATE payments SET {', '.join(clauses)} WHERE payment_id = ? AND property_id = ?"

        try:
            with self._connect() as conn:
                conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError("Payment update violated database integrity.") from exc

        updated = self.get_payment(property_id, payment_id)
        if not updated:
            raise EntityNotFoundError(f"Payment '{payment_id}' not found.")
        return updated

    # -------------------------------------------------------------------------
    # Expenses
    # -------------------------------------------------------------------------

    def create_expense(self, property_id: str, data: ExpenseCreate) -> ExpenseRecord:
        self._ensure_property_exists(property_id)

        if data.vendor_id:
            vendor = self.get_vendor(property_id, data.vendor_id)
            if not vendor:
                other_prop = self._find_property_for_entity("vendors", "vendor_id", data.vendor_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Vendor '{data.vendor_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Vendor '{data.vendor_id}' not found.")

        if data.unit_id:
            unit = self.get_unit(property_id, data.unit_id)
            if not unit:
                other_prop = self._find_property_for_entity("units", "unit_id", data.unit_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Unit '{data.unit_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Unit '{data.unit_id}' not found.")

        if data.document_id:
            doc = self.get_document_metadata(property_id, data.document_id)
            if not doc:
                other_prop = self._find_property_for_entity("document_metadata", "document_id", data.document_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Document '{data.document_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Document '{data.document_id}' not found.")

        eid = str(uuid.uuid4())
        now = _utc_now().isoformat()
        amt_str = _format_decimal(data.amount)
        if not amt_str:
            raise InvalidOperationError("Amount is required.")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO expenses (
                    expense_id, property_id, category, amount, currency,
                    expense_date, status, description, vendor_id, unit_id,
                    document_id, approval_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    eid,
                    property_id,
                    data.category.value,
                    amt_str,
                    data.currency,
                    data.expense_date.isoformat(),
                    data.status.value,
                    data.description.strip(),
                    data.vendor_id,
                    data.unit_id,
                    data.document_id,
                    now,
                    now,
                ),
            )

        return ExpenseRecord(
            expense_id=eid,
            property_id=property_id,
            category=data.category,
            amount=data.amount,
            currency=data.currency,
            expense_date=data.expense_date,
            status=data.status,
            description=data.description.strip(),
            vendor_id=data.vendor_id,
            unit_id=data.unit_id,
            document_id=data.document_id,
            approval_status="draft",
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_expense(self, property_id: str, expense_id: str) -> ExpenseRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM expenses WHERE expense_id = ? AND property_id = ?",
                (expense_id, property_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_expense(row)

    def list_expenses(
        self,
        property_id: str,
        status: ExpenseStatus | None = None,
        category: ExpenseCategory | None = None,
        vendor_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExpenseRecord]:
        self._ensure_property_exists(property_id)
        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if category is not None:
            clauses.append("category = ?")
            params.append(category.value)
        if vendor_id is not None:
            clauses.append("vendor_id = ?")
            params.append(vendor_id)

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM expenses {where_sql} ORDER BY expense_date DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row_to_expense(r) for r in rows]

    def update_expense(
        self,
        property_id: str,
        expense_id: str,
        update: ExpenseUpdate,
    ) -> ExpenseRecord:
        exp = self.get_expense(property_id, expense_id)
        if not exp:
            raise EntityNotFoundError(f"Expense '{expense_id}' not found in property '{property_id}'.")

        clauses: list[str] = []
        params: list[Any] = []

        if update.status is not None:
            clauses.append("status = ?")
            params.append(update.status.value)
        if update.description is not None:
            clauses.append("description = ?")
            params.append(update.description.strip())

        if not clauses:
            return exp

        now = _utc_now().isoformat()
        clauses.append("updated_at = ?")
        params.append(now)

        params.extend([expense_id, property_id])
        sql = f"UPDATE expenses SET {', '.join(clauses)} WHERE expense_id = ? AND property_id = ?"

        try:
            with self._connect() as conn:
                conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError("Expense update violated database integrity.") from exc

        updated = self.get_expense(property_id, expense_id)
        if not updated:
            raise EntityNotFoundError(f"Expense '{expense_id}' not found.")
        return updated

    # -------------------------------------------------------------------------
    # Maintenance Work Orders
    # -------------------------------------------------------------------------

    def create_work_order(self, property_id: str, data: WorkOrderCreate) -> WorkOrderRecord:
        self._ensure_property_exists(property_id)

        # 1. Unit check
        unit = self.get_unit(property_id, data.unit_id)
        if not unit:
            other_prop = self._find_property_for_entity("units", "unit_id", data.unit_id)
            if other_prop:
                raise CrossPropertyReferenceError(
                    f"Unit '{data.unit_id}' belongs to property '{other_prop}', not '{property_id}'."
                )
            raise EntityNotFoundError(f"Unit '{data.unit_id}' not found.")

        # 2. Resident check if provided
        if data.resident_id:
            resident = self.get_resident(property_id, data.resident_id)
            if not resident:
                other_prop = self._find_property_for_entity("residents", "resident_id", data.resident_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Resident '{data.resident_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Resident '{data.resident_id}' not found.")

        # 3. Vendor check if provided
        if data.vendor_id:
            vendor = self.get_vendor(property_id, data.vendor_id)
            if not vendor:
                other_prop = self._find_property_for_entity("vendors", "vendor_id", data.vendor_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Vendor '{data.vendor_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Vendor '{data.vendor_id}' not found.")

        wid = str(uuid.uuid4())
        now = _utc_now().isoformat()
        est_str = _format_decimal(data.estimated_cost)
        act_str = _format_decimal(data.actual_cost)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO work_orders (
                    work_order_id, property_id, unit_id, resident_id, vendor_id,
                    title, description, priority, status, estimated_cost,
                    actual_cost, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    wid,
                    property_id,
                    data.unit_id,
                    data.resident_id,
                    data.vendor_id,
                    data.title.strip(),
                    data.description.strip(),
                    data.priority.value,
                    data.status.value,
                    est_str,
                    act_str,
                    now,
                    now,
                ),
            )

        return WorkOrderRecord(
            work_order_id=wid,
            property_id=property_id,
            unit_id=data.unit_id,
            resident_id=data.resident_id,
            vendor_id=data.vendor_id,
            title=data.title.strip(),
            description=data.description.strip(),
            priority=data.priority,
            status=data.status,
            estimated_cost=data.estimated_cost,
            actual_cost=data.actual_cost,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_work_order(self, property_id: str, work_order_id: str) -> WorkOrderRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM work_orders WHERE work_order_id = ? AND property_id = ?",
                (work_order_id, property_id),
            ).fetchone()
        if not row:
            return None
        return self._row_to_work_order(row)

    def list_work_orders(
        self,
        property_id: str,
        status: WorkOrderStatus | None = None,
        priority: WorkOrderPriority | None = None,
        unit_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkOrderRecord]:
        self._ensure_property_exists(property_id)
        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority.value)
        if unit_id is not None:
            clauses.append("unit_id = ?")
            params.append(unit_id)

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM work_orders {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row_to_work_order(r) for r in rows]

    def update_work_order(
        self,
        property_id: str,
        work_order_id: str,
        update: WorkOrderUpdate | None = None,
        data: WorkOrderUpdate | None = None,
    ) -> WorkOrderRecord:
        resolved_update = update or data
        if resolved_update is None:
            raise InvalidOperationError("Update payload is required.")
        update = resolved_update

        wo = self.get_work_order(property_id, work_order_id)
        if not wo:
            raise EntityNotFoundError(f"Work order '{work_order_id}' not found in property '{property_id}'.")


        if update.status is not None and update.status != wo.status:
            allowed_transitions = {
                WorkOrderStatus.OPEN: {
                    WorkOrderStatus.ASSIGNED,
                    WorkOrderStatus.CANCELLED,
                },
                WorkOrderStatus.ASSIGNED: {
                    WorkOrderStatus.IN_PROGRESS,
                    WorkOrderStatus.CANCELLED,
                },
                WorkOrderStatus.IN_PROGRESS: {
                    WorkOrderStatus.COMPLETED,
                    WorkOrderStatus.CANCELLED,
                },
                WorkOrderStatus.COMPLETED: set(),
                WorkOrderStatus.CANCELLED: set(),
            }

            allowed = allowed_transitions[wo.status]

            if update.status not in allowed:
                allowed_names = (
                    ", ".join(sorted(status.value for status in allowed))
                    or "none"
                )

                raise InvalidOperationError(
                    f"Invalid work-order status transition: "
                    f"{wo.status.value} -> {update.status.value}. "
                    f"Allowed next statuses: {allowed_names}."
                )

        clauses: list[str] = []
        params: list[Any] = []

        if update.title is not None:
            clauses.append("title = ?")
            params.append(update.title.strip())
        if update.description is not None:
            clauses.append("description = ?")
            params.append(update.description.strip())
        if update.priority is not None:
            clauses.append("priority = ?")
            params.append(update.priority.value)
        if update.status is not None:
            clauses.append("status = ?")
            params.append(update.status.value)
        if update.estimated_cost is not None:
            clauses.append("estimated_cost = ?")
            params.append(_format_decimal(update.estimated_cost))
        if update.actual_cost is not None:
            clauses.append("actual_cost = ?")
            params.append(_format_decimal(update.actual_cost))
        if update.vendor_id is not None:
            vendor = self.get_vendor(property_id, update.vendor_id)
            if not vendor:
                other_prop = self._find_property_for_entity("vendors", "vendor_id", update.vendor_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Vendor '{update.vendor_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Vendor '{update.vendor_id}' not found.")
            clauses.append("vendor_id = ?")
            params.append(update.vendor_id)

        if not clauses:
            return wo

        now = _utc_now().isoformat()
        clauses.append("updated_at = ?")
        params.append(now)

        params.extend([work_order_id, property_id])
        sql = f"UPDATE work_orders SET {', '.join(clauses)} WHERE work_order_id = ? AND property_id = ?"

        try:
            with self._connect() as conn:
                conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError("Work order update violated database integrity.") from exc

        updated = self.get_work_order(property_id, work_order_id)
        if not updated:
            raise EntityNotFoundError(f"Work order '{work_order_id}' not found.")
        return updated

    # -------------------------------------------------------------------------
    # Helper Validation and Row Mapping
    # -------------------------------------------------------------------------

    def _ensure_property_exists(self, property_id: str) -> None:
        if not self.get_property(property_id):
            raise EntityNotFoundError(f"Property '{property_id}' was not found.")

    def _find_property_for_entity(self, table: str, id_col: str, entity_id: str) -> str | None:
        """Find the property_id of an entity to give precise cross-property rejection diagnostics."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT property_id FROM {table} WHERE {id_col} = ?",
                (entity_id,),
            ).fetchone()
        return row["property_id"] if row else None

    @staticmethod
    def _row_to_unit(row: sqlite3.Row) -> UnitRecord:
        return UnitRecord(
            unit_id=row["unit_id"],
            property_id=row["property_id"],
            unit_number=row["unit_number"],
            status=UnitStatus(row["status"]),
            rent_amount=_parse_decimal(row["rent_amount"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_resident(row: sqlite3.Row) -> ResidentRecord:
        return ResidentRecord(
            resident_id=row["resident_id"],
            property_id=row["property_id"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            email=row["email"],
            phone=row["phone"],
            status=ResidentStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_lease(row: sqlite3.Row) -> LeaseRecord:
        return LeaseRecord(
            lease_id=row["lease_id"],
            property_id=row["property_id"],
            unit_id=row["unit_id"],
            resident_id=row["resident_id"],
            start_date=date.fromisoformat(row["start_date"]),
            end_date=date.fromisoformat(row["end_date"]),
            rent_amount=_parse_decimal(row["rent_amount"]) or Decimal("0.00"),
            deposit_amount=_parse_decimal(row["deposit_amount"]),
            status=LeaseStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_vendor(row: sqlite3.Row) -> VendorRecord:
        return VendorRecord(
            vendor_id=row["vendor_id"],
            property_id=row["property_id"],
            name=row["name"],
            category=VendorCategory(row["category"]),
            email=row["email"],
            phone=row["phone"],
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> DocumentMetadataRecord:
        return DocumentMetadataRecord(
            document_id=row["document_id"],
            property_id=row["property_id"],
            filename=row["filename"],
            file_type=row["file_type"],
            storage_path=row["storage_path"],
            file_size_bytes=row["file_size_bytes"],
            sha256_checksum=row["sha256_checksum"],
            associated_entity_type=row["associated_entity_type"],
            associated_entity_id=row["associated_entity_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_payment(row: sqlite3.Row) -> PaymentRecord:
        return PaymentRecord(
            payment_id=row["payment_id"],
            property_id=row["property_id"],
            amount=_parse_decimal(row["amount"]) or Decimal("0.00"),
            currency=row["currency"],
            payment_date=date.fromisoformat(row["payment_date"]),
            payment_method=PaymentMethod(row["payment_method"]),
            status=PaymentStatus(row["status"]),
            resident_id=row["resident_id"],
            lease_id=row["lease_id"],
            reference_number=row["reference_number"],
            document_id=row["document_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_expense(row: sqlite3.Row) -> ExpenseRecord:
        keys = row.keys()
        app_at = (
            datetime.fromisoformat(row["approved_at"])
            if "approved_at" in keys and row["approved_at"]
            else None
        )
        return ExpenseRecord(
            expense_id=row["expense_id"],
            property_id=row["property_id"],
            category=ExpenseCategory(row["category"]),
            amount=_parse_decimal(row["amount"]) or Decimal("0.00"),
            currency=row["currency"],
            expense_date=date.fromisoformat(row["expense_date"]),
            status=ExpenseStatus(row["status"]),
            description=row["description"],
            vendor_id=row["vendor_id"],
            unit_id=row["unit_id"],
            document_id=row["document_id"],
            approval_status=row["approval_status"] if "approval_status" in keys and row["approval_status"] else "pending",
            approved_by=row["approved_by"] if "approved_by" in keys else None,
            approved_at=app_at,
            decision_reason=row["decision_reason"] if "decision_reason" in keys else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_work_order(row: sqlite3.Row) -> WorkOrderRecord:
        return WorkOrderRecord(
            work_order_id=row["work_order_id"],
            property_id=row["property_id"],
            unit_id=row["unit_id"],
            resident_id=row["resident_id"],
            vendor_id=row["vendor_id"],
            title=row["title"],
            description=row["description"],
            priority=WorkOrderPriority(row["priority"]),
            status=WorkOrderStatus(row["status"]),
            estimated_cost=_parse_decimal(row["estimated_cost"]),
            actual_cost=_parse_decimal(row["actual_cost"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )



    # -------------------------------------------------------------------------
    # Maintenance Requests
    # -------------------------------------------------------------------------

    def create_maintenance_request(
        self,
        property_id: str,
        data: MaintenanceRequestCreate,
    ) -> MaintenanceRequestRecord:
        self._ensure_property_exists(property_id)

        unit = self.get_unit(property_id, data.unit_id)
        if not unit:
            other_property = self._find_property_for_entity(
                "units", "unit_id", data.unit_id
            )
            if other_property:
                raise CrossPropertyReferenceError(
                    f"Unit '{data.unit_id}' belongs to another property."
                )
            raise EntityNotFoundError(
                f"Unit '{data.unit_id}' not found."
            )

        if data.resident_id:
            resident = self.get_resident(property_id, data.resident_id)
            if not resident:
                other_property = self._find_property_for_entity(
                    "residents", "resident_id", data.resident_id
                )
                if other_property:
                    raise CrossPropertyReferenceError(
                        f"Resident '{data.resident_id}' belongs to "
                        "another property."
                    )
                raise EntityNotFoundError(
                    f"Resident '{data.resident_id}' not found."
                )

        request_id = str(uuid.uuid4())
        now = _utc_now().isoformat()
        description = data.description.strip()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO maintenance_requests (
                    request_id, property_id, unit_id, resident_id,
                    description, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    property_id,
                    data.unit_id,
                    data.resident_id,
                    description,
                    MaintenanceRequestStatus.SUBMITTED.value,
                    now,
                    now,
                ),
            )

        return MaintenanceRequestRecord(
            request_id=request_id,
            property_id=property_id,
            unit_id=data.unit_id,
            resident_id=data.resident_id,
            description=description,
            status=MaintenanceRequestStatus.SUBMITTED,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_maintenance_request(
        self,
        property_id: str,
        request_id: str,
    ) -> MaintenanceRequestRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM maintenance_requests
                WHERE property_id = ? AND request_id = ?
                """,
                (property_id, request_id),
            ).fetchone()

        return (
            self._row_to_maintenance_request(row)
            if row else None
        )

    def list_maintenance_requests(
        self,
        property_id: str,
        status: MaintenanceRequestStatus | None = None,
        unit_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MaintenanceRequestRecord]:
        self._ensure_property_exists(property_id)

        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        if unit_id is not None:
            clauses.append("unit_id = ?")
            params.append(unit_id)

        where_sql = " AND ".join(clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM maintenance_requests
                WHERE {where_sql}
                ORDER BY created_at DESC, request_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return [
            self._row_to_maintenance_request(row)
            for row in rows
        ]

    def update_maintenance_request(
        self,
        property_id: str,
        request_id: str,
        update: MaintenanceRequestUpdate,
    ) -> MaintenanceRequestRecord:
        current = self.get_maintenance_request(property_id, request_id)
        if current is None:
            raise EntityNotFoundError(
                f"Maintenance request '{request_id}' not found."
            )

        allowed_transitions = {
            MaintenanceRequestStatus.SUBMITTED: {
                MaintenanceRequestStatus.TRIAGED,
                MaintenanceRequestStatus.CANCELLED,
            },
            MaintenanceRequestStatus.TRIAGED: {
                MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER,
                MaintenanceRequestStatus.CANCELLED,
            },
            MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER: {
                MaintenanceRequestStatus.CLOSED,
            },
            MaintenanceRequestStatus.CLOSED: set(),
            MaintenanceRequestStatus.CANCELLED: set(),
        }

        if update.status is not None and update.status != current.status:
            if update.status not in allowed_transitions[current.status]:
                raise InvalidOperationError(
                    f"Invalid maintenance request status transition: "
                    f"{current.status.value} -> {update.status.value}."
                )

        if update.status == MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER:
            if not update.work_order_id and not current.work_order_id:
                raise InvalidOperationError(
                    "A work_order_id is required when converting a "
                    "maintenance request to a work order."
                )

        if update.work_order_id is not None:
            work_order = self.get_work_order(
                property_id, update.work_order_id
            )
            if work_order is None:
                other_property = self._find_property_for_entity(
                    "work_orders", "work_order_id", update.work_order_id
                )
                if other_property:
                    raise CrossPropertyReferenceError(
                        "The work order belongs to another property."
                    )
                raise EntityNotFoundError(
                    f"Work order '{update.work_order_id}' not found."
                )

        fields = update.model_fields_set
        changes: dict[str, Any] = {}

        if "status" in fields and update.status is not None:
            changes["status"] = update.status.value
        if "priority" in fields and update.priority is not None:
            changes["priority"] = update.priority.value
        if "category" in fields:
            changes["category"] = (
                update.category.strip() if update.category else None
            )
        if "is_emergency" in fields and update.is_emergency is not None:
            changes["is_emergency"] = int(update.is_emergency)
        
        if (
            "estimated_sla_hours" in fields
            and update.estimated_sla_hours is not None
        ):
            changes["estimated_sla_hours"] = update.estimated_sla_hours

        if "sla_deadline_at" in fields:
            changes["sla_deadline_at"] = (
                update.sla_deadline_at.isoformat()
                if update.sla_deadline_at is not None
                else None
            )

        if "work_order_id" in fields:
            changes["work_order_id"] = update.work_order_id


        if not changes:
            return current

        changes["updated_at"] = _utc_now().isoformat()
        assignments = ", ".join(
            f"{column} = ?" for column in changes
        )

        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE maintenance_requests
                SET {assignments}
                WHERE property_id = ? AND request_id = ?
                """,
                [
                    *changes.values(),
                    property_id,
                    request_id,
                ],
            )
            if cursor.rowcount != 1:
                raise EntityNotFoundError(
                    f"Maintenance request '{request_id}' not found."
                )

        result = self.get_maintenance_request(property_id, request_id)
        if result is None:
            raise EntityNotFoundError(
                f"Maintenance request '{request_id}' not found."
            )

        if update.status in (
            MaintenanceRequestStatus.CLOSED,
            MaintenanceRequestStatus.CANCELLED,
        ):
            self.resolve_active_escalations_for_request(
                property_id,
                request_id,
                notes=f"Resolved automatically when request transitioned to {update.status.value}.",
            )

        return result

    
    def convert_maintenance_request_to_work_order(
        self,
        property_id: str,
        request_id: str,
    ) -> WorkOrderRecord:
        """Atomically convert a triaged request into one work order.

        Repeated calls return the existing work order instead of creating
        duplicates. Work-order creation and request linking are atomic.
        """
        self._ensure_property_exists(property_id)

        with self.transaction() as conn:
            request_row = conn.execute(
                """
                SELECT *
                FROM maintenance_requests
                WHERE request_id = ? AND property_id = ?
                """,
                (request_id, property_id),
            ).fetchone()

            if request_row is None:
                raise EntityNotFoundError(
                    f"Maintenance request '{request_id}' not found "
                    f"in property '{property_id}'."
                )

            request = self._row_to_maintenance_request(request_row)

            # Idempotency: return the work order already linked to this request.
            if request.status == MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER:
                if not request.work_order_id:
                    raise InvalidOperationError(
                        "Converted maintenance request has no linked work order."
                    )

                work_order_row = conn.execute(
                    """
                    SELECT *
                    FROM work_orders
                    WHERE work_order_id = ? AND property_id = ?
                    """,
                    (request.work_order_id, property_id),
                ).fetchone()

                if work_order_row is None:
                    raise InvalidOperationError(
                        "The linked work order does not exist in this property."
                    )

                return self._row_to_work_order(work_order_row)

            if request.status != MaintenanceRequestStatus.TRIAGED:
                raise InvalidOperationError(
                    "Only triaged maintenance requests can be converted. "
                    f"Current status: {request.status.value}."
                )

            work_order_id = str(uuid.uuid4())
            now = _utc_now().isoformat()
            title = f"Maintenance Request: {request.description[:120].strip()}"

            priority = request.priority or WorkOrderPriority.MEDIUM

            conn.execute(
                """
                INSERT INTO work_orders (
                    work_order_id,
                    property_id,
                    unit_id,
                    resident_id,
                    vendor_id,
                    title,
                    description,
                    priority,
                    status,
                    estimated_cost,
                    actual_cost,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_order_id,
                    property_id,
                    request.unit_id,
                    request.resident_id,
                    None,
                    title,
                    request.description.strip(),
                    priority.value,
                    WorkOrderStatus.OPEN.value,
                    None,
                    None,
                    now,
                    now,
                ),
            )

            cursor = conn.execute(
                """
                UPDATE maintenance_requests
                SET status = ?,
                    work_order_id = ?,
                    updated_at = ?
                WHERE request_id = ?
                AND property_id = ?
                AND status = ?
                """,
                (
                    MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER.value,
                    work_order_id,
                    now,
                    request_id,
                    property_id,
                    MaintenanceRequestStatus.TRIAGED.value,
                ),
            )

            if cursor.rowcount != 1:
                raise InvalidOperationError(
                    "Maintenance request changed during conversion."
                )

            work_order_row = conn.execute(
                """
                SELECT *
                FROM work_orders
                WHERE work_order_id = ? AND property_id = ?
                """,
                (work_order_id, property_id),
            ).fetchone()

            if work_order_row is None:
                raise InvalidOperationError(
                    "Created work order could not be retrieved."
                )

            return self._row_to_work_order(work_order_row)



    @staticmethod
    def _row_to_maintenance_request(
        row: sqlite3.Row,
    ) -> MaintenanceRequestRecord:
        return MaintenanceRequestRecord(
            request_id=row["request_id"],
            property_id=row["property_id"],
            unit_id=row["unit_id"],
            resident_id=row["resident_id"],
            description=row["description"],
            status=MaintenanceRequestStatus(row["status"]),
            priority=(
                WorkOrderPriority(row["priority"])
                if row["priority"] else None
            ),
            category=row["category"],
            is_emergency=bool(row["is_emergency"]),
            estimated_sla_hours=row["estimated_sla_hours"],
            sla_deadline_at=(
                datetime.fromisoformat(row["sla_deadline_at"])
                if row["sla_deadline_at"]
                else None
            ),
            work_order_id=row["work_order_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # -------------------------------------------------------------------------
    # SLA Monitoring & Escalations
    # -------------------------------------------------------------------------

    def create_sla_escalation(
        self,
        property_id: str,
        data: SlaEscalationCreate,
    ) -> SlaEscalationRecord:
        self._ensure_property_exists(property_id)

        req = self.get_maintenance_request(property_id, data.request_id)
        if not req:
            other_property = self._find_property_for_entity(
                "maintenance_requests", "request_id", data.request_id
            )
            if other_property:
                raise CrossPropertyReferenceError(
                    f"Maintenance request '{data.request_id}' belongs to another property."
                )
            raise EntityNotFoundError(
                f"Maintenance request '{data.request_id}' not found."
            )

        escalation_id = str(uuid.uuid4())
        now = _utc_now().isoformat()
        detected_at = (
            data.detected_at.isoformat()
            if data.detected_at is not None
            else now
        )
        sla_deadline_at = data.sla_deadline_at.isoformat()

        with self.transaction() as conn:
            existing_row = conn.execute(
                """
                SELECT *
                FROM sla_escalations
                WHERE property_id = ?
                  AND request_id = ?
                  AND escalation_type = ?
                  AND status IN ('open', 'acknowledged')
                LIMIT 1
                """,
                (property_id, data.request_id, data.escalation_type.value),
            ).fetchone()

            if existing_row is not None:
                return self._row_to_sla_escalation(existing_row)

            conn.execute(
                """
                INSERT INTO sla_escalations (
                    escalation_id, property_id, request_id, escalation_type,
                    sla_deadline_at, detected_at, status, notes,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    escalation_id,
                    property_id,
                    data.request_id,
                    data.escalation_type.value,
                    sla_deadline_at,
                    detected_at,
                    SlaEscalationStatus.OPEN.value,
                    data.notes.strip() if data.notes else None,
                    now,
                    now,
                ),
            )

        created = self.get_sla_escalation(property_id, escalation_id)
        if created is None:
            raise InvalidOperationError("Failed to create SLA escalation record.")
        return created

    def get_sla_escalation(
        self,
        property_id: str,
        escalation_id: str,
    ) -> SlaEscalationRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM sla_escalations
                WHERE property_id = ? AND escalation_id = ?
                """,
                (property_id, escalation_id),
            ).fetchone()

        return self._row_to_sla_escalation(row) if row else None

    def list_sla_escalations(
        self,
        property_id: str,
        escalation_type: SlaEscalationType | None = None,
        status: SlaEscalationStatus | None = None,
        request_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SlaEscalationRecord]:
        self._ensure_property_exists(property_id)

        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if escalation_type is not None:
            clauses.append("escalation_type = ?")
            params.append(escalation_type.value)

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        if request_id is not None:
            clauses.append("request_id = ?")
            params.append(request_id)

        where_sql = " AND ".join(clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM sla_escalations
                WHERE {where_sql}
                ORDER BY created_at DESC, escalation_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return [self._row_to_sla_escalation(row) for row in rows]

    def count_sla_escalations(
        self,
        property_id: str,
        escalation_type: SlaEscalationType | None = None,
        status: SlaEscalationStatus | None = None,
        request_id: str | None = None,
    ) -> int:
        self._ensure_property_exists(property_id)

        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if escalation_type is not None:
            clauses.append("escalation_type = ?")
            params.append(escalation_type.value)

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        if request_id is not None:
            clauses.append("request_id = ?")
            params.append(request_id)

        where_sql = " AND ".join(clauses)

        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) as cnt
                FROM sla_escalations
                WHERE {where_sql}
                """,
                params,
            ).fetchone()

        return int(row["cnt"]) if row else 0

    def update_sla_escalation_status(
        self,
        property_id: str,
        escalation_id: str,
        update: SlaEscalationUpdate,
    ) -> SlaEscalationRecord:
        self._ensure_property_exists(property_id)

        current = self.get_sla_escalation(property_id, escalation_id)
        if current is None:
            other_property = self._find_property_for_entity(
                "sla_escalations", "escalation_id", escalation_id
            )
            if other_property:
                raise CrossPropertyReferenceError(
                    f"Escalation '{escalation_id}' belongs to another property."
                )
            raise EntityNotFoundError(
                f"SLA escalation '{escalation_id}' not found."
            )

        allowed_transitions = {
            SlaEscalationStatus.OPEN: {
                SlaEscalationStatus.OPEN,
                SlaEscalationStatus.ACKNOWLEDGED,
                SlaEscalationStatus.RESOLVED,
            },
            SlaEscalationStatus.ACKNOWLEDGED: {
                SlaEscalationStatus.ACKNOWLEDGED,
                SlaEscalationStatus.RESOLVED,
            },
            SlaEscalationStatus.RESOLVED: {
                SlaEscalationStatus.RESOLVED,
            },
        }

        if update.status not in allowed_transitions.get(current.status, set()):
            raise InvalidOperationError(
                f"Invalid escalation status transition: "
                f"{current.status.value} -> {update.status.value}."
            )

        now = _utc_now().isoformat()
        notes = update.notes.strip() if update.notes is not None else current.notes

        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE sla_escalations
                SET status = ?, notes = ?, updated_at = ?
                WHERE property_id = ? AND escalation_id = ?
                """,
                (update.status.value, notes, now, property_id, escalation_id),
            )
            if cursor.rowcount != 1:
                raise EntityNotFoundError(
                    f"SLA escalation '{escalation_id}' not found."
                )

        updated = self.get_sla_escalation(property_id, escalation_id)
        if updated is None:
            raise EntityNotFoundError(
                f"SLA escalation '{escalation_id}' not found."
            )
        return updated

    def acknowledge_sla_escalation(
        self,
        property_id: str,
        escalation_id: str,
        notes: str | None = None,
    ) -> SlaEscalationRecord:
        return self.update_sla_escalation_status(
            property_id,
            escalation_id,
            SlaEscalationUpdate(
                status=SlaEscalationStatus.ACKNOWLEDGED,
                notes=notes,
            ),
        )

    def resolve_sla_escalation(
        self,
        property_id: str,
        escalation_id: str,
        notes: str | None = None,
    ) -> SlaEscalationRecord:
        return self.update_sla_escalation_status(
            property_id,
            escalation_id,
            SlaEscalationUpdate(
                status=SlaEscalationStatus.RESOLVED,
                notes=notes,
            ),
        )

    def resolve_active_escalations_for_request(
        self,
        property_id: str,
        request_id: str,
        notes: str | None = None,
    ) -> list[SlaEscalationRecord]:
        self._ensure_property_exists(property_id)
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sla_escalations
                SET status = ?,
                    notes = COALESCE(?, notes),
                    updated_at = ?
                WHERE property_id = ?
                  AND request_id = ?
                  AND status IN ('open', 'acknowledged')
                """,
                (
                    SlaEscalationStatus.RESOLVED.value,
                    notes.strip() if notes else None,
                    now,
                    property_id,
                    request_id,
                ),
            )

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM sla_escalations
                WHERE property_id = ? AND request_id = ?
                ORDER BY created_at DESC
                """,
                (property_id, request_id),
            ).fetchall()

        return [self._row_to_sla_escalation(r) for r in rows]

    def get_eligible_maintenance_requests_for_sla(
        self,
        property_id: str,
    ) -> list[MaintenanceRequestRecord]:
        self._ensure_property_exists(property_id)

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM maintenance_requests
                WHERE property_id = ?
                  AND sla_deadline_at IS NOT NULL
                  AND status NOT IN ('closed', 'cancelled')
                ORDER BY sla_deadline_at ASC, created_at ASC
                """,
                (property_id,),
            ).fetchall()

        return [self._row_to_maintenance_request(row) for row in rows]

    @staticmethod
    def _row_to_sla_escalation(row: sqlite3.Row) -> SlaEscalationRecord:
        return SlaEscalationRecord(
            escalation_id=row["escalation_id"],
            property_id=row["property_id"],
            request_id=row["request_id"],
            escalation_type=SlaEscalationType(row["escalation_type"]),
            sla_deadline_at=datetime.fromisoformat(row["sla_deadline_at"]),
            detected_at=datetime.fromisoformat(row["detected_at"]),
            status=SlaEscalationStatus(row["status"]),
            notes=row["notes"] if "notes" in row.keys() else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # -------------------------------------------------------------------------
    # Invoices & Rent Obligations
    # -------------------------------------------------------------------------

    def create_invoice(self, property_id: str, data: InvoiceCreate) -> InvoiceRecord:
        self._ensure_property_exists(property_id)

        if data.lease_id:
            lease = self.get_lease(property_id, data.lease_id)
            if not lease:
                other_property = self._find_property_for_entity("leases", "lease_id", data.lease_id)
                if other_property:
                    raise CrossPropertyReferenceError("Lease belongs to another property.")
                raise EntityNotFoundError(f"Lease '{data.lease_id}' not found.")

        if data.resident_id:
            resident = self.get_resident(property_id, data.resident_id)
            if not resident:
                other_property = self._find_property_for_entity("residents", "resident_id", data.resident_id)
                if other_property:
                    raise CrossPropertyReferenceError("Resident belongs to another property.")
                raise EntityNotFoundError(f"Resident '{data.resident_id}' not found.")

        if data.vendor_id:
            vendor = self.get_vendor(property_id, data.vendor_id)
            if not vendor:
                other_property = self._find_property_for_entity("vendors", "vendor_id", data.vendor_id)
                if other_property:
                    raise CrossPropertyReferenceError("Vendor belongs to another property.")
                raise EntityNotFoundError(f"Vendor '{data.vendor_id}' not found.")

        invoice_id = str(uuid.uuid4())
        now = _utc_now().isoformat()
        amt_str = _format_decimal(data.amount)

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO invoices (
                        invoice_id, property_id, lease_id, resident_id, vendor_id,
                        invoice_number, invoice_type, amount, amount_paid, currency,
                        issue_date, due_date, status, description, document_id,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '0.00', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        invoice_id,
                        property_id,
                        data.lease_id,
                        data.resident_id,
                        data.vendor_id,
                        data.invoice_number.strip(),
                        data.invoice_type.value,
                        amt_str,
                        data.currency.upper(),
                        data.issue_date.isoformat(),
                        data.due_date.isoformat(),
                        InvoiceStatus.UNPAID.value,
                        data.description.strip() if data.description else None,
                        data.document_id,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise IntegrityConflictError(
                f"Invoice '{data.invoice_number}' already exists in property '{property_id}'."
            ) from exc

        created = self.get_invoice(property_id, invoice_id)
        if not created:
            raise InvalidOperationError("Failed to create invoice record.")
        return created

    def get_invoice(self, property_id: str, invoice_id: str) -> InvoiceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM invoices WHERE property_id = ? AND invoice_id = ?",
                (property_id, invoice_id),
            ).fetchone()
        return self._row_to_invoice(row) if row else None

    def list_invoices(
        self,
        property_id: str,
        status: InvoiceStatus | None = None,
        invoice_type: InvoiceType | None = None,
        lease_id: str | None = None,
        resident_id: str | None = None,
        vendor_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[InvoiceRecord]:
        self._ensure_property_exists(property_id)

        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if invoice_type is not None:
            clauses.append("invoice_type = ?")
            params.append(invoice_type.value)
        if lease_id is not None:
            clauses.append("lease_id = ?")
            params.append(lease_id)
        if resident_id is not None:
            clauses.append("resident_id = ?")
            params.append(resident_id)
        if vendor_id is not None:
            clauses.append("vendor_id = ?")
            params.append(vendor_id)

        where_sql = " AND ".join(clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM invoices
                WHERE {where_sql}
                ORDER BY due_date ASC, created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return [self._row_to_invoice(row) for row in rows]

    def update_invoice(
        self,
        property_id: str,
        invoice_id: str,
        update: InvoiceUpdate,
    ) -> InvoiceRecord:
        current = self.get_invoice(property_id, invoice_id)
        if not current:
            other_property = self._find_property_for_entity("invoices", "invoice_id", invoice_id)
            if other_property:
                raise CrossPropertyReferenceError("Invoice belongs to another property.")
            raise EntityNotFoundError(f"Invoice '{invoice_id}' not found.")

        clauses: list[str] = []
        params: list[Any] = []

        if update.status is not None:
            clauses.append("status = ?")
            params.append(update.status.value)
        if update.amount_paid is not None:
            clauses.append("amount_paid = ?")
            params.append(_format_decimal(update.amount_paid))
        if update.description is not None:
            clauses.append("description = ?")
            params.append(update.description.strip())
        if update.due_date is not None:
            clauses.append("due_date = ?")
            params.append(update.due_date.isoformat())

        if not clauses:
            return current

        now = _utc_now().isoformat()
        clauses.append("updated_at = ?")
        params.append(now)

        params.extend([property_id, invoice_id])

        with self._connect() as conn:
            conn.execute(
                f"UPDATE invoices SET {', '.join(clauses)} WHERE property_id = ? AND invoice_id = ?",
                params,
            )

        updated = self.get_invoice(property_id, invoice_id)
        if not updated:
            raise EntityNotFoundError(f"Invoice '{invoice_id}' not found.")
        return updated

    def record_invoice_payment(
        self,
        property_id: str,
        invoice_id: str,
        amount: Decimal,
    ) -> InvoiceRecord:
        """Increment amount_paid and update status based on remaining balance atomically."""
        if amount <= Decimal("0.00"):
            raise InvalidOperationError("Payment allocation amount must be greater than zero.")

        now = _utc_now().isoformat()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM invoices WHERE property_id = ? AND invoice_id = ?",
                (property_id, invoice_id),
            ).fetchone()
            if not row:
                raise EntityNotFoundError(f"Invoice '{invoice_id}' not found.")

            current_status = row["status"]
            current_amount = _parse_decimal(row["amount"]) or Decimal("0.00")
            current_paid = _parse_decimal(row["amount_paid"]) or Decimal("0.00")

            if current_status == InvoiceStatus.PAID.value or current_paid >= current_amount:
                raise InvalidOperationError(f"Invoice '{invoice_id}' is already fully paid.")

            new_paid = current_paid + amount
            if new_paid > current_amount:
                remaining = current_amount - current_paid
                raise InvalidOperationError(
                    f"Payment allocation of {amount} exceeds remaining outstanding balance of {remaining}."
                )

            new_status = InvoiceStatus.PAID.value if new_paid >= current_amount else InvoiceStatus.PARTIALLY_PAID.value

            conn.execute(
                """
                UPDATE invoices
                SET amount_paid = ?, status = ?, updated_at = ?
                WHERE property_id = ? AND invoice_id = ?
                """,
                (_format_decimal(new_paid), new_status, now, property_id, invoice_id),
            )

        return self.get_invoice(property_id, invoice_id)

    # -------------------------------------------------------------------------
    # Bank Reconciliation
    # -------------------------------------------------------------------------

    def create_bank_statement(
        self,
        property_id: str,
        filename: str,
        opening_balance: Decimal | None,
        closing_balance: Decimal | None,
        statement_start_date: date | None,
        statement_end_date: date | None,
        total_transactions: int,
        file_checksum: str | None = None,
        account_identifier: str | None = None,
    ) -> BankStatementRecord:
        self._ensure_property_exists(property_id)

        # Prevent duplicate file import for same property
        if file_checksum:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT statement_id FROM bank_statements WHERE property_id = ? AND file_checksum = ?",
                    (property_id, file_checksum),
                ).fetchone()
                if existing:
                    raise IntegrityConflictError(
                        f"Bank statement with checksum '{file_checksum}' has already been imported."
                    )

        statement_id = str(uuid.uuid4())
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bank_statements (
                    statement_id, property_id, account_identifier, filename,
                    opening_balance, closing_balance, statement_start_date,
                    statement_end_date, total_transactions, reconciliation_status,
                    file_checksum, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    statement_id,
                    property_id,
                    account_identifier,
                    filename,
                    _format_decimal(opening_balance),
                    _format_decimal(closing_balance),
                    statement_start_date.isoformat() if statement_start_date else None,
                    statement_end_date.isoformat() if statement_end_date else None,
                    total_transactions,
                    file_checksum,
                    now,
                ),
            )

        created = self.get_bank_statement(property_id, statement_id)
        if not created:
            raise InvalidOperationError("Failed to create bank statement record.")
        return created

    def get_bank_statement(self, property_id: str, statement_id: str) -> BankStatementRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bank_statements WHERE property_id = ? AND statement_id = ?",
                (property_id, statement_id),
            ).fetchone()
        return self._row_to_bank_statement(row) if row else None

    def list_bank_statements(self, property_id: str, limit: int = 100, offset: int = 0) -> list[BankStatementRecord]:
        self._ensure_property_exists(property_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bank_statements WHERE property_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (property_id, limit, offset),
            ).fetchall()
        return [self._row_to_bank_statement(r) for r in rows]

    def create_bank_transactions(
        self,
        property_id: str,
        statement_id: str,
        transactions: list[dict[str, Any]],
    ) -> list[BankTransactionRecord]:
        self._ensure_property_exists(property_id)
        now = _utc_now().isoformat()

        inserted: list[BankTransactionRecord] = []
        with self.transaction() as conn:
            for tx in transactions:
                tx_id = str(uuid.uuid4())
                amount_dec = abs(Decimal(str(tx["amount"])))
                tx_date = tx["transaction_date"]
                tx_date_str = tx_date.isoformat() if isinstance(tx_date, (date, datetime)) else str(tx_date)

                conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        transaction_id, statement_id, property_id, transaction_date,
                        description, reference, amount, transaction_type, currency,
                        match_status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unmatched', ?)
                    """,
                    (
                        tx_id,
                        statement_id,
                        property_id,
                        tx_date_str,
                        str(tx["description"]).strip(),
                        str(tx.get("reference") or "").strip() or None,
                        _format_decimal(amount_dec),
                        str(tx.get("transaction_type", "credit")).lower(),
                        str(tx.get("currency", "USD")).upper(),
                        now,
                    ),
                )

        return self.list_bank_transactions(property_id, statement_id=statement_id, limit=len(transactions) + 10)

    def get_bank_transaction(self, property_id: str, transaction_id: str) -> BankTransactionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bank_transactions WHERE property_id = ? AND transaction_id = ?",
                (property_id, transaction_id),
            ).fetchone()
        return self._row_to_bank_transaction(row) if row else None

    def list_bank_transactions(
        self,
        property_id: str,
        statement_id: str | None = None,
        match_status: BankTransactionMatchStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BankTransactionRecord]:
        self._ensure_property_exists(property_id)

        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if statement_id is not None:
            clauses.append("statement_id = ?")
            params.append(statement_id)
        if match_status is not None:
            clauses.append("match_status = ?")
            params.append(match_status.value)

        where_sql = " AND ".join(clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM bank_transactions
                WHERE {where_sql}
                ORDER BY transaction_date ASC, transaction_id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return [self._row_to_bank_transaction(r) for r in rows]

    def update_bank_transaction_match(
        self,
        property_id: str,
        transaction_id: str,
        match_status: BankTransactionMatchStatus,
        matched_entity_type: str | None = None,
        matched_entity_id: str | None = None,
        match_confidence: float | None = None,
        match_notes: str | None = None,
        reconciled_by: str | None = None,
    ) -> BankTransactionRecord:
        tx = self.get_bank_transaction(property_id, transaction_id)
        if not tx:
            other_property = self._find_property_for_entity("bank_transactions", "transaction_id", transaction_id)
            if other_property:
                raise CrossPropertyReferenceError("Bank transaction belongs to another property.")
            raise EntityNotFoundError(f"Bank transaction '{transaction_id}' not found.")

        now = _utc_now().isoformat()
        with self.transaction() as conn:
            if match_status == BankTransactionMatchStatus.MATCHED and matched_entity_id:
                conflicting = conn.execute(
                    """
                    SELECT transaction_id FROM bank_transactions
                    WHERE property_id = ? AND matched_entity_id = ? AND match_status = 'matched' AND transaction_id != ?
                    """,
                    (property_id, matched_entity_id, transaction_id),
                ).fetchone()
                if conflicting:
                    raise IntegrityConflictError(
                        f"Entity '{matched_entity_id}' is already reconciled to bank transaction '{conflicting['transaction_id']}'."
                    )

            conn.execute(
                """
                UPDATE bank_transactions
                SET match_status = ?,
                    matched_entity_type = ?,
                    matched_entity_id = ?,
                    match_confidence = ?,
                    match_notes = ?,
                    reconciled_at = ?,
                    reconciled_by = ?
                WHERE property_id = ? AND transaction_id = ?
                """,
                (
                    match_status.value,
                    matched_entity_type,
                    matched_entity_id,
                    match_confidence,
                    match_notes,
                    now if match_status == BankTransactionMatchStatus.MATCHED else None,
                    reconciled_by,
                    property_id,
                    transaction_id,
                ),
            )

        updated = self.get_bank_transaction(property_id, transaction_id)
        if not updated:
            raise EntityNotFoundError(f"Bank transaction '{transaction_id}' not found.")
        return updated

    # -------------------------------------------------------------------------
    # Expense Approvals & Workflow
    # -------------------------------------------------------------------------

    def submit_expense_for_approval(self, property_id: str, expense_id: str) -> ExpenseRecord:
        exp = self.get_expense(property_id, expense_id)
        if not exp:
            other_prop = self._find_property_for_entity("expenses", "expense_id", expense_id)
            if other_prop:
                raise CrossPropertyReferenceError("Expense belongs to another property.")
            raise EntityNotFoundError(f"Expense '{expense_id}' not found.")

        if exp.approval_status not in ("draft", "returned", "pending"):
            raise InvalidOperationError(f"Expense '{expense_id}' has already been processed (status: '{exp.approval_status}').")

        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE expenses
                SET approval_status = 'pending', status = ?, updated_at = ?
                WHERE property_id = ? AND expense_id = ?
                """,
                (ExpenseStatus.PENDING.value, now, property_id, expense_id),
            )

        return self.get_expense(property_id, expense_id)

    def decide_expense(
        self,
        property_id: str,
        expense_id: str,
        decision: str,
        approver_id: str,
        approver_role: UserRole,
        reason: str,
        manager_approval_limit: Decimal | None = None,
    ) -> ExpenseRecord:
        """Approve, reject, or return an expense enforcing approval thresholds."""
        if manager_approval_limit is None:
            env_limit = os.getenv("ENIGMA_MANAGER_EXPENSE_LIMIT", "1000.00")
            try:
                manager_approval_limit = Decimal(env_limit)
            except Exception:
                manager_approval_limit = Decimal("1000.00")

        exp = self.get_expense(property_id, expense_id)
        if not exp:
            other_prop = self._find_property_for_entity("expenses", "expense_id", expense_id)
            if other_prop:
                raise CrossPropertyReferenceError("Expense belongs to another property.")
            raise EntityNotFoundError(f"Expense '{expense_id}' not found.")

        decision = decision.strip().lower()
        if decision not in ("approve", "reject", "return"):
            raise InvalidOperationError(f"Invalid expense decision: '{decision}'. Must be approve, reject, or return.")

        if exp.approval_status != "pending":
            raise InvalidOperationError(f"Expense '{expense_id}' has already been decided (current status: '{exp.approval_status}').")

        # Enforce server-side threshold checks
        if decision == "approve":
            if approver_role == UserRole.PROPERTY_MANAGER and exp.amount > manager_approval_limit:
                raise PermissionError(
                    f"Expense amount (${exp.amount}) exceeds Property Manager approval threshold "
                    f"of ${manager_approval_limit}. Requires Accountant or Admin approval."
                )
            new_status = ExpenseStatus.APPROVED
            new_approval_status = "approved"
        elif decision == "reject":
            new_status = ExpenseStatus.CANCELLED
            new_approval_status = "rejected"
        else:  # return
            new_status = ExpenseStatus.PENDING
            new_approval_status = "returned"

        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE expenses
                SET status = ?,
                    approval_status = ?,
                    approved_by = ?,
                    approved_at = ?,
                    decision_reason = ?,
                    updated_at = ?
                WHERE property_id = ? AND expense_id = ?
                """,
                (
                    new_status.value,
                    new_approval_status,
                    approver_id,
                    now,
                    reason.strip(),
                    now,
                    property_id,
                    expense_id,
                ),
            )

        updated = self.get_expense(property_id, expense_id)
        if not updated:
            raise EntityNotFoundError(f"Expense '{expense_id}' not found.")
        return updated

    def detect_duplicate_expense(
        self,
        property_id: str,
        category: ExpenseCategory,
        amount: Decimal,
        expense_date: date,
        vendor_id: str | None = None,
        exclude_expense_id: str | None = None,
    ) -> ExpenseRecord | None:
        """Detect existing expense with matching property, category, amount, date, and vendor."""
        amt_str = _format_decimal(amount)
        query = """
            SELECT * FROM expenses
            WHERE property_id = ?
              AND category = ?
              AND amount = ?
              AND expense_date = ?
              AND status != 'cancelled'
        """
        params: list[Any] = [property_id, category.value, amt_str, expense_date.isoformat()]

        if vendor_id:
            query += " AND vendor_id = ?"
            params.append(vendor_id)
        if exclude_expense_id:
            query += " AND expense_id != ?"
            params.append(exclude_expense_id)

        query += " LIMIT 1"

        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return self._row_to_expense(row) if row else None

    # -------------------------------------------------------------------------
    # Compliance Findings
    # -------------------------------------------------------------------------

    def create_compliance_finding(
        self,
        property_id: str,
        category: ComplianceCategory,
        severity: ComplianceSeverity,
        title: str,
        description: str,
        triggering_rule: str,
        affected_record_ids: list[str],
        evidence: list[str],
        escalation_id: str | None = None,
    ) -> ComplianceFindingRecord:
        self._ensure_property_exists(property_id)
        finding_id = str(uuid.uuid4())
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO compliance_findings (
                    finding_id, property_id, category, severity, title,
                    description, triggering_rule, affected_record_ids, evidence,
                    status, escalation_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                (
                    finding_id,
                    property_id,
                    category.value,
                    severity.value,
                    title.strip(),
                    description.strip(),
                    triggering_rule.strip(),
                    json.dumps(affected_record_ids),
                    json.dumps(evidence),
                    escalation_id,
                    now,
                    now,
                ),
            )

        created = self.get_compliance_finding(property_id, finding_id)
        if not created:
            raise InvalidOperationError("Failed to create compliance finding.")
        return created

    def get_compliance_finding(self, property_id: str, finding_id: str) -> ComplianceFindingRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM compliance_findings WHERE property_id = ? AND finding_id = ?",
                (property_id, finding_id),
            ).fetchone()
        return self._row_to_compliance_finding(row) if row else None

    def list_compliance_findings(
        self,
        property_id: str,
        category: ComplianceCategory | None = None,
        severity: ComplianceSeverity | None = None,
        status: ComplianceStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ComplianceFindingRecord]:
        self._ensure_property_exists(property_id)

        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if category is not None:
            clauses.append("category = ?")
            params.append(category.value)
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity.value)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        where_sql = " AND ".join(clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM compliance_findings
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return [self._row_to_compliance_finding(r) for r in rows]

    def assign_compliance_finding(self, property_id: str, finding_id: str, assigned_to: str) -> ComplianceFindingRecord:
        f = self.get_compliance_finding(property_id, finding_id)
        if not f:
            other_prop = self._find_property_for_entity("compliance_findings", "finding_id", finding_id)
            if other_prop:
                raise CrossPropertyReferenceError("Compliance finding belongs to another property.")
            raise EntityNotFoundError(f"Compliance finding '{finding_id}' not found.")

        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE compliance_findings
                SET assigned_to = ?, status = 'under_review', updated_at = ?
                WHERE property_id = ? AND finding_id = ?
                """,
                (assigned_to.strip(), now, property_id, finding_id),
            )

        return self.get_compliance_finding(property_id, finding_id)

    def resolve_compliance_finding(
        self,
        property_id: str,
        finding_id: str,
        action: str,
        reason: str,
    ) -> ComplianceFindingRecord:
        f = self.get_compliance_finding(property_id, finding_id)
        if not f:
            other_prop = self._find_property_for_entity("compliance_findings", "finding_id", finding_id)
            if other_prop:
                raise CrossPropertyReferenceError("Compliance finding belongs to another property.")
            raise EntityNotFoundError(f"Compliance finding '{finding_id}' not found.")

        action = action.strip().lower()
        if action not in ("resolve", "dismiss"):
            raise InvalidOperationError(f"Invalid compliance action: '{action}'. Must be resolve or dismiss.")

        new_status = "resolved" if action == "resolve" else "dismissed"
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE compliance_findings
                SET status = ?, resolution_reason = ?, resolved_at = ?, updated_at = ?
                WHERE property_id = ? AND finding_id = ?
                """,
                (new_status, reason.strip(), now, now, property_id, finding_id),
            )

        return self.get_compliance_finding(property_id, finding_id)

    # -------------------------------------------------------------------------
    # Email & Notifications
    # -------------------------------------------------------------------------

    def create_notification(
        self,
        property_id: str,
        recipient_email: str,
        subject: str,
        body_text: str,
        notification_type: NotificationType,
        status: NotificationStatus = NotificationStatus.PENDING,
        delivery_mode: NotificationDeliveryMode = NotificationDeliveryMode.SIMULATED,
        recipient_name: str | None = None,
        body_html: str | None = None,
        idempotency_key: str | None = None,
    ) -> NotificationRecord:
        self._ensure_property_exists(property_id)

        if idempotency_key:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT * FROM notifications WHERE property_id = ? AND idempotency_key = ?",
                    (property_id, idempotency_key),
                ).fetchone()
                if existing:
                    return self._row_to_notification(existing)

        nid = str(uuid.uuid4())
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notifications (
                    notification_id, property_id, recipient_email, recipient_name,
                    subject, body_text, body_html, notification_type, status,
                    delivery_mode, idempotency_key, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nid,
                    property_id,
                    recipient_email.strip(),
                    recipient_name.strip() if recipient_name else None,
                    subject.strip(),
                    body_text.strip(),
                    body_html.strip() if body_html else None,
                    notification_type.value,
                    status.value,
                    delivery_mode.value,
                    idempotency_key,
                    now,
                    now,
                ),
            )

        created = self.get_notification(property_id, nid)
        if not created:
            raise InvalidOperationError("Failed to create notification record.")
        return created

    def get_notification(self, property_id: str, notification_id: str) -> NotificationRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM notifications WHERE property_id = ? AND notification_id = ?",
                (property_id, notification_id),
            ).fetchone()
        return self._row_to_notification(row) if row else None

    def list_notifications(
        self,
        property_id: str,
        notification_type: NotificationType | None = None,
        status: NotificationStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationRecord]:
        self._ensure_property_exists(property_id)

        clauses = ["property_id = ?"]
        params: list[Any] = [property_id]

        if notification_type is not None:
            clauses.append("notification_type = ?")
            params.append(notification_type.value)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        where_sql = " AND ".join(clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM notifications
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return [self._row_to_notification(r) for r in rows]

    def update_notification_status(
        self,
        property_id: str,
        notification_id: str,
        status: NotificationStatus,
        error_message: str | None = None,
        increment_retry: bool = False,
    ) -> NotificationRecord:
        n = self.get_notification(property_id, notification_id)
        if not n:
            raise EntityNotFoundError(f"Notification '{notification_id}' not found.")

        now = _utc_now().isoformat()
        sent_at = now if status in (NotificationStatus.SENT, NotificationStatus.SIMULATED) else n.sent_at
        retry_inc = 1 if increment_retry else 0

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notifications
                SET status = ?,
                    error_message = ?,
                    retry_count = retry_count + ?,
                    sent_at = ?,
                    updated_at = ?
                WHERE property_id = ? AND notification_id = ?
                """,
                (
                    status.value,
                    error_message,
                    retry_inc,
                    sent_at.isoformat() if isinstance(sent_at, datetime) else sent_at,
                    now,
                    property_id,
                    notification_id,
                ),
            )

        return self.get_notification(property_id, notification_id)

    # -------------------------------------------------------------------------
    # Row Converters
    # -------------------------------------------------------------------------

    @staticmethod
    def _row_to_invoice(row: sqlite3.Row) -> InvoiceRecord:
        return InvoiceRecord(
            invoice_id=row["invoice_id"],
            property_id=row["property_id"],
            lease_id=row["lease_id"],
            resident_id=row["resident_id"],
            vendor_id=row["vendor_id"],
            invoice_number=row["invoice_number"],
            invoice_type=InvoiceType(row["invoice_type"]),
            amount=_parse_decimal(row["amount"]) or Decimal("0.00"),
            amount_paid=_parse_decimal(row["amount_paid"]) or Decimal("0.00"),
            currency=row["currency"],
            issue_date=date.fromisoformat(row["issue_date"]),
            due_date=date.fromisoformat(row["due_date"]),
            status=InvoiceStatus(row["status"]),
            description=row["description"],
            document_id=row["document_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_bank_statement(row: sqlite3.Row) -> BankStatementRecord:
        s_start = date.fromisoformat(row["statement_start_date"]) if row["statement_start_date"] else None
        s_end = date.fromisoformat(row["statement_end_date"]) if row["statement_end_date"] else None
        return BankStatementRecord(
            statement_id=row["statement_id"],
            property_id=row["property_id"],
            account_identifier=row["account_identifier"],
            filename=row["filename"],
            opening_balance=_parse_decimal(row["opening_balance"]),
            closing_balance=_parse_decimal(row["closing_balance"]),
            statement_start_date=s_start,
            statement_end_date=s_end,
            total_transactions=row["total_transactions"],
            reconciliation_status=row["reconciliation_status"],
            file_checksum=row["file_checksum"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_bank_transaction(row: sqlite3.Row) -> BankTransactionRecord:
        rec_at = datetime.fromisoformat(row["reconciled_at"]) if row["reconciled_at"] else None
        conf = float(row["match_confidence"]) if row["match_confidence"] is not None else None
        return BankTransactionRecord(
            transaction_id=row["transaction_id"],
            statement_id=row["statement_id"],
            property_id=row["property_id"],
            transaction_date=date.fromisoformat(row["transaction_date"]),
            description=row["description"],
            reference=row["reference"],
            amount=_parse_decimal(row["amount"]) or Decimal("0.00"),
            transaction_type=row["transaction_type"],
            currency=row["currency"],
            match_status=BankTransactionMatchStatus(row["match_status"]),
            matched_entity_type=row["matched_entity_type"],
            matched_entity_id=row["matched_entity_id"],
            match_confidence=conf,
            match_notes=row["match_notes"],
            reconciled_at=rec_at,
            reconciled_by=row["reconciled_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_compliance_finding(row: sqlite3.Row) -> ComplianceFindingRecord:
        res_at = datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
        return ComplianceFindingRecord(
            finding_id=row["finding_id"],
            property_id=row["property_id"],
            category=ComplianceCategory(row["category"]),
            severity=ComplianceSeverity(row["severity"]),
            title=row["title"],
            description=row["description"],
            triggering_rule=row["triggering_rule"],
            affected_record_ids=json.loads(row["affected_record_ids"]),
            evidence=json.loads(row["evidence"]),
            status=ComplianceStatus(row["status"]),
            assigned_to=row["assigned_to"],
            resolution_reason=row["resolution_reason"],
            resolved_at=res_at,
            escalation_id=row["escalation_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_notification(row: sqlite3.Row) -> NotificationRecord:
        s_at = datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None
        return NotificationRecord(
            notification_id=row["notification_id"],
            property_id=row["property_id"],
            recipient_email=row["recipient_email"],
            recipient_name=row["recipient_name"],
            subject=row["subject"],
            body_text=row["body_text"],
            body_html=row["body_html"],
            notification_type=NotificationType(row["notification_type"]),
            status=NotificationStatus(row["status"]),
            delivery_mode=NotificationDeliveryMode(row["delivery_mode"]),
            idempotency_key=row["idempotency_key"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            error_message=row["error_message"],
            sent_at=s_at,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )






    # -------------------------------------------------------------------------
    # Assets & Equipment Tracking
    # -------------------------------------------------------------------------

    def create_asset(self, property_id: str, data: AssetCreate) -> AssetRecord:
        self._ensure_property_exists(property_id)

        if data.unit_id:
            unit = self.get_unit(property_id, data.unit_id)
            if not unit:
                other_prop = self._find_property_for_entity("units", "unit_id", data.unit_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Unit '{data.unit_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Unit '{data.unit_id}' not found.")

        aid = str(uuid.uuid4())
        now = _utc_now().isoformat()
        cost_str = _format_decimal(data.purchase_cost)
        inst_str = data.install_date.isoformat() if data.install_date else None
        warr_str = data.warranty_expiry_date.isoformat() if data.warranty_expiry_date else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO assets (
                    asset_id, property_id, unit_id, name, asset_category,
                    make_model, serial_number, install_date, warranty_expiry_date,
                    purchase_cost, expected_lifespan_years, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aid,
                    property_id,
                    data.unit_id,
                    data.name.strip(),
                    data.asset_category.value,
                    data.make_model.strip() if data.make_model else None,
                    data.serial_number.strip() if data.serial_number else None,
                    inst_str,
                    warr_str,
                    cost_str,
                    data.expected_lifespan_years,
                    now,
                    now,
                ),
            )

        return AssetRecord(
            asset_id=aid,
            property_id=property_id,
            unit_id=data.unit_id,
            name=data.name.strip(),
            asset_category=data.asset_category,
            make_model=data.make_model.strip() if data.make_model else None,
            serial_number=data.serial_number.strip() if data.serial_number else None,
            install_date=data.install_date,
            warranty_expiry_date=data.warranty_expiry_date,
            purchase_cost=data.purchase_cost,
            expected_lifespan_years=data.expected_lifespan_years,
            total_maintenance_spend=Decimal("0.00"),
            work_orders_count=0,
            is_warranty_active=bool(data.warranty_expiry_date and data.warranty_expiry_date >= date.today()),
            health_status="good",
        )

    def get_asset(self, property_id: str, asset_id: str) -> AssetRecord | None:
        self._ensure_property_exists(property_id)
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM assets WHERE property_id = ? AND asset_id = ?",
                (property_id, asset_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_asset(row)

    def list_assets(
        self,
        property_id: str,
        unit_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AssetRecord]:
        self._ensure_property_exists(property_id)
        query = "SELECT * FROM assets WHERE property_id = ?"
        params: list[Any] = [property_id]
        if unit_id:
            query += " AND unit_id = ?"
            params.append(unit_id)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._connect() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_asset(row) for row in cursor.fetchall()]

    def delete_asset(self, property_id: str, asset_id: str) -> bool:
        self._ensure_property_exists(property_id)
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM assets WHERE property_id = ? AND asset_id = ?",
                (property_id, asset_id),
            )
            return cursor.rowcount > 0

    # -------------------------------------------------------------------------
    # Preventive Maintenance Schedules
    # -------------------------------------------------------------------------

    def create_preventive_schedule(
        self, property_id: str, data: PreventiveTaskCreate
    ) -> PreventiveTaskRecord:
        self._ensure_property_exists(property_id)

        if data.unit_id:
            unit = self.get_unit(property_id, data.unit_id)
            if not unit:
                other_prop = self._find_property_for_entity("units", "unit_id", data.unit_id)
                if other_prop:
                    raise CrossPropertyReferenceError(
                        f"Unit '{data.unit_id}' belongs to property '{other_prop}', not '{property_id}'."
                    )
                raise EntityNotFoundError(f"Unit '{data.unit_id}' not found.")

        if data.asset_id:
            asset = self.get_asset(property_id, data.asset_id)
            if not asset:
                raise EntityNotFoundError(f"Asset '{data.asset_id}' not found.")

        sid = str(uuid.uuid4())
        now = _utc_now().isoformat()
        next_due_str = data.next_due_date.isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO preventive_schedules (
                    schedule_id, property_id, unit_id, asset_id, title, category,
                    frequency, interval_days, description, last_performed_date,
                    next_due_date, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    sid,
                    property_id,
                    data.unit_id,
                    data.asset_id,
                    data.title.strip(),
                    data.category.value,
                    data.frequency.value,
                    data.interval_days,
                    data.description.strip(),
                    next_due_str,
                    now,
                    now,
                ),
            )

        today = date.today()
        days_until_due = (data.next_due_date - today).days

        return PreventiveTaskRecord(
            task_id=sid,
            property_id=property_id,
            title=data.title.strip(),
            category=data.category,
            frequency=data.frequency,
            interval_days=data.interval_days,
            unit_id=data.unit_id,
            asset_id=data.asset_id,
            description=data.description.strip(),
            last_performed_date=None,
            next_due_date=data.next_due_date,
            is_overdue=days_until_due < 0,
            days_until_due=days_until_due,
        )

    def get_preventive_schedule(
        self, property_id: str, schedule_id: str
    ) -> PreventiveTaskRecord | None:
        self._ensure_property_exists(property_id)
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM preventive_schedules WHERE property_id = ? AND schedule_id = ?",
                (property_id, schedule_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_preventive_schedule(row)

    def list_preventive_schedules(
        self, property_id: str, limit: int = 100, offset: int = 0
    ) -> list[PreventiveTaskRecord]:
        self._ensure_property_exists(property_id)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM preventive_schedules
                WHERE property_id = ?
                ORDER BY next_due_date ASC
                LIMIT ? OFFSET ?
                """,
                (property_id, limit, offset),
            )
            return [self._row_to_preventive_schedule(row) for row in cursor.fetchall()]

    def update_preventive_schedule(
        self,
        property_id: str,
        schedule_id: str,
        last_performed_date: date,
        next_due_date: date,
    ) -> PreventiveTaskRecord | None:
        self._ensure_property_exists(property_id)
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE preventive_schedules
                SET last_performed_date = ?, next_due_date = ?, updated_at = ?
                WHERE property_id = ? AND schedule_id = ?
                """,
                (
                    last_performed_date.isoformat(),
                    next_due_date.isoformat(),
                    now,
                    property_id,
                    schedule_id,
                ),
            )
        return self.get_preventive_schedule(property_id, schedule_id)

    # -------------------------------------------------------------------------
    # Inspections
    # -------------------------------------------------------------------------

    def create_inspection(
        self, property_id: str, data: InspectionReportCreate
    ) -> dict[str, Any]:
        self._ensure_property_exists(property_id)

        unit = self.get_unit(property_id, data.unit_id)
        if not unit:
            other_prop = self._find_property_for_entity("units", "unit_id", data.unit_id)
            if other_prop:
                raise CrossPropertyReferenceError(
                    f"Unit '{data.unit_id}' belongs to property '{other_prop}', not '{property_id}'."
                )
            raise EntityNotFoundError(f"Unit '{data.unit_id}' not found.")

        iid = str(uuid.uuid4())
        insp_date = (data.inspection_date or date.today()).isoformat()
        items_payload = [item.model_dump(mode="json") for item in data.items]
        now = _utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO inspections (
                    inspection_id, property_id, unit_id, inspection_type,
                    inspector_name, inspection_date, items_json, overall_notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    iid,
                    property_id,
                    data.unit_id,
                    data.inspection_type.value,
                    data.inspector_name.strip(),
                    insp_date,
                    json.dumps(items_payload),
                    data.overall_notes.strip() if data.overall_notes else None,
                    now,
                ),
            )

        return {
            "inspection_id": iid,
            "property_id": property_id,
            "unit_id": data.unit_id,
            "inspection_type": data.inspection_type.value,
            "inspector_name": data.inspector_name.strip(),
            "inspection_date": insp_date,
            "items": items_payload,
            "overall_notes": data.overall_notes,
            "created_at": now,
        }

    def get_inspection(self, property_id: str, inspection_id: str) -> dict[str, Any] | None:
        self._ensure_property_exists(property_id)
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM inspections WHERE property_id = ? AND inspection_id = ?",
                (property_id, inspection_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "inspection_id": row["inspection_id"],
                "property_id": row["property_id"],
                "unit_id": row["unit_id"],
                "inspection_type": row["inspection_type"],
                "inspector_name": row["inspector_name"],
                "inspection_date": row["inspection_date"],
                "items": json.loads(row["items_json"]),
                "overall_notes": row["overall_notes"],
                "created_at": row["created_at"],
            }

    def list_inspections(
        self,
        property_id: str,
        unit_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self._ensure_property_exists(property_id)
        query = "SELECT * FROM inspections WHERE property_id = ?"
        params: list[Any] = [property_id]
        if unit_id:
            query += " AND unit_id = ?"
            params.append(unit_id)
        query += " ORDER BY inspection_date DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._connect() as conn:
            cursor = conn.execute(query, params)
            results = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "inspection_id": row["inspection_id"],
                        "property_id": row["property_id"],
                        "unit_id": row["unit_id"],
                        "inspection_type": row["inspection_type"],
                        "inspector_name": row["inspector_name"],
                        "inspection_date": row["inspection_date"],
                        "items": json.loads(row["items_json"]),
                        "overall_notes": row["overall_notes"],
                        "created_at": row["created_at"],
                    }
                )
            return results

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> AssetRecord:
        inst_d = date.fromisoformat(row["install_date"]) if row["install_date"] else None
        warr_d = date.fromisoformat(row["warranty_expiry_date"]) if row["warranty_expiry_date"] else None
        return AssetRecord(
            asset_id=row["asset_id"],
            property_id=row["property_id"],
            unit_id=row["unit_id"],
            name=row["name"],
            asset_category=AssetCategory(row["asset_category"]),
            make_model=row["make_model"],
            serial_number=row["serial_number"],
            install_date=inst_d,
            warranty_expiry_date=warr_d,
            purchase_cost=_parse_decimal(row["purchase_cost"]),
            expected_lifespan_years=row["expected_lifespan_years"],
            total_maintenance_spend=Decimal("0.00"),
            work_orders_count=0,
            is_warranty_active=bool(warr_d and warr_d >= date.today()),
            health_status="good",
        )

    @staticmethod
    def _row_to_preventive_schedule(row: sqlite3.Row) -> PreventiveTaskRecord:
        next_d = date.fromisoformat(row["next_due_date"])
        last_d = date.fromisoformat(row["last_performed_date"]) if row["last_performed_date"] else None
        today = date.today()
        days_until_due = (next_d - today).days
        return PreventiveTaskRecord(
            task_id=row["schedule_id"],
            property_id=row["property_id"],
            title=row["title"],
            category=MaintenanceCategory(row["category"]),
            frequency=PreventiveFrequency(row["frequency"]),
            interval_days=row["interval_days"],
            unit_id=row["unit_id"],
            asset_id=row["asset_id"],
            description=row["description"] or "",
            last_performed_date=last_d,
            next_due_date=next_d,
            is_overdue=days_until_due < 0,
            days_until_due=days_until_due,
        )

