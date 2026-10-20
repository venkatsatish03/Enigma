"""Comprehensive test suite for Finance, Bank Reconciliation, Expense Approvals,
Compliance Monitoring, Email Notifications, and Financial Reporting.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import main
from repository.domain_repository import DomainRepository
from schemas.auth import UserRole
from schemas.domain import (
    BankTransactionMatchStatus,
    ComplianceCategory,
    ComplianceSeverity,
    ComplianceStatus,
    ExpenseApprovalStatus,
    ExpenseCategory,
    ExpenseCreate,
    ExpenseStatus,
    InvoiceCreate,
    InvoiceStatus,
    InvoiceType,
    InvoiceUpdate,
    MatchDecisionRequest,
    NotificationDeliveryMode,
    NotificationStatus,
    NotificationType,
    PaymentCreate,
    PaymentMethod,
    PaymentStatus,
    ResidentCreate,
    UnitCreate,
    VendorCategory,
    VendorCreate,
)
from tools.audit_trail import AuditTrail
from tools.auth_config import AuthConfig
from tools.auth_crypto import create_access_token, hash_password
from tools.auth_dependencies import (
    get_auth_config,
    get_auth_repository,
    get_domain_repository,
)
from tools.auth_repository import AuthRepository
from tools.financial_record_adapter import adapt_domain_records


@pytest.fixture
def test_setup(tmp_path):
    """Setup isolated test databases, auth, and client."""
    auth_db = tmp_path / "test_auth.sqlite3"
    domain_db = tmp_path / "test_domain.sqlite3"
    audit_db = tmp_path / "test_audit.sqlite3"

    auth_repo = AuthRepository(auth_db)
    domain_repo = DomainRepository(domain_db)
    audit_trail = AuditTrail(audit_db)

    config = AuthConfig(
        secret_key="a" * 32,
        algorithm="HS256",
        access_token_expire_minutes=60,
        auth_db_path=auth_db,
    )

    # Create users
    pm_user = auth_repo.create_user(email="pm@example.com", password_hash=hash_password("Pass123!"))
    acct_user = auth_repo.create_user(email="acct@example.com", password_hash=hash_password("Pass123!"))

    # Create properties
    prop_id = "prop-alpha"
    prop_other = "prop-beta"
    auth_repo.create_property(name="Alpha Towers", property_id=prop_id)
    auth_repo.create_property(name="Beta Court", property_id=prop_other)

    domain_repo.ensure_property(property_id=prop_id, name="Alpha Towers")
    domain_repo.ensure_property(property_id=prop_other, name="Beta Court")

    # Property memberships
    auth_repo.create_membership(property_id=prop_id, user_id=pm_user.user_id, role=UserRole.PROPERTY_MANAGER)
    auth_repo.create_membership(property_id=prop_id, user_id=acct_user.user_id, role=UserRole.ACCOUNTANT)

    # Dependency overrides
    main.app.dependency_overrides[get_auth_config] = lambda: config
    main.app.dependency_overrides[get_auth_repository] = lambda: auth_repo
    main.app.dependency_overrides[get_domain_repository] = lambda: domain_repo
    main.app.dependency_overrides[main.get_audit_trail] = lambda: audit_trail

    pm_token, _ = create_access_token(user_id=pm_user.user_id, email=pm_user.email, secret_key=config.secret_key)
    acct_token, _ = create_access_token(user_id=acct_user.user_id, email=acct_user.email, secret_key=config.secret_key)

    client = TestClient(main.app)

    yield {
        "client": client,
        "domain_repo": domain_repo,
        "audit_trail": audit_trail,
        "prop_id": prop_id,
        "prop_other": prop_other,
        "pm_user": pm_user,
        "acct_user": acct_user,
        "pm_token": pm_token,
        "acct_token": acct_token,
    }

    main.app.dependency_overrides.clear()


def _headers(token: str, prop_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Property-ID": prop_id}


# =============================================================================
# 1. Financial Records & Payment Allocation Tests
# =============================================================================

def test_invoice_creation_and_listing(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]

    # Create resident & unit
    unit_res = client.post(
        f"/api/v1/properties/{prop_id}/units",
        headers=_headers(token, prop_id),
        json={"unit_number": "101", "rent_amount": "1200.00"},
    )
    assert unit_res.status_code == 201

    res_res = client.post(
        f"/api/v1/properties/{prop_id}/residents",
        headers=_headers(token, prop_id),
        json={"first_name": "Alice", "last_name": "Smith", "email": "alice@example.com"},
    )
    assert res_res.status_code == 201
    resident_id = res_res.json()["resident_id"]

    # Create Invoice
    inv_payload = {
        "invoice_number": "INV-2026-001",
        "invoice_type": "rent",
        "amount": "1200.00",
        "currency": "USD",
        "issue_date": str(date.today()),
        "due_date": str(date.today() + timedelta(days=10)),
        "resident_id": resident_id,
        "description": "Monthly rent for Unit 101",
    }
    create_res = client.post(
        f"/api/v1/properties/{prop_id}/invoices",
        headers=_headers(token, prop_id),
        json=inv_payload,
    )
    assert create_res.status_code == 201
    invoice = create_res.json()
    assert invoice["invoice_number"] == "INV-2026-001"
    assert invoice["status"] == "unpaid"
    assert Decimal(invoice["outstanding_amount"]) == Decimal("1200.00")

    # List Invoices
    list_res = client.get(
        f"/api/v1/properties/{prop_id}/invoices",
        headers=_headers(token, prop_id),
    )
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1


def test_payment_allocation_full_and_partial(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]

    # Create invoice
    create_res = client.post(
        f"/api/v1/properties/{prop_id}/invoices",
        headers=_headers(token, prop_id),
        json={
            "invoice_number": "INV-2026-002",
            "invoice_type": "rent",
            "amount": "1000.00",
            "currency": "USD",
            "issue_date": str(date.today()),
            "due_date": str(date.today() + timedelta(days=5)),
        },
    )
    assert create_res.status_code == 201
    invoice_id = create_res.json()["invoice_id"]

    # Partial allocation of $400
    alloc1 = client.post(
        f"/api/v1/properties/{prop_id}/invoices/{invoice_id}/payments",
        headers=_headers(token, prop_id),
        json={"amount": "400.00"},
    )
    assert alloc1.status_code == 200
    inv1 = alloc1.json()
    assert inv1["status"] == "partially_paid"
    assert Decimal(inv1["amount_paid"]) == Decimal("400.00")
    assert Decimal(inv1["outstanding_amount"]) == Decimal("600.00")

    # Final payment of remaining $600
    alloc2 = client.post(
        f"/api/v1/properties/{prop_id}/invoices/{invoice_id}/payments",
        headers=_headers(token, prop_id),
        json={"amount": "600.00"},
    )
    assert alloc2.status_code == 200
    inv2 = alloc2.json()
    assert inv2["status"] == "paid"
    assert Decimal(inv2["amount_paid"]) == Decimal("1000.00")
    assert Decimal(inv2["outstanding_amount"]) == Decimal("0.00")


# =============================================================================
# 2. Bank Reconciliation & CSV Import Tests
# =============================================================================

def test_bank_statement_import_and_reconciliation(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]

    # Pre-record a resident payment to match against
    client.post(
        f"/api/v1/properties/{prop_id}/payments",
        headers=_headers(token, prop_id),
        json={
            "amount": "1500.00",
            "payment_date": str(date.today()),
            "payment_method": "ach",
            "reference": "CHK-9988",
            "notes": "Rent check",
        },
    )

    # Valid CSV bank statement
    csv_data = (
        "Date,Description,Reference,Amount,Balance\n"
        f"{date.today().isoformat()},Direct Deposit Rent,CHK-9988,1500.00,11500.00\n"
        f"{date.today().isoformat()},Maintenance Supply,DEBIT-11,-250.00,11250.00\n"
    )

    files = {"file": ("statement_march.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    res = client.post(
        f"/api/v1/properties/{prop_id}/bank-statements/import",
        headers={"Authorization": f"Bearer {token}", "X-Property-ID": prop_id},
        files=files,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["transaction_count"] == 2
    statement = data["statement"]
    assert statement["filename"] == "statement_march.csv"
    assert statement["total_transactions"] == 2

    transactions = data["transactions"]
    credit_tx = next(t for t in transactions if t["amount"] == 1500.0 or Decimal(str(t["amount"])) == Decimal("1500.00"))
    assert credit_tx["match_status"] in ("matched", "partially_matched")
    assert credit_tx["matched_entity_type"] == "payment"
    assert credit_tx["match_confidence"] is not None and credit_tx["match_confidence"] > 0.8

    # Prevent duplicate import of identical file
    files_dup = {"file": ("statement_march.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    dup_res = client.post(
        f"/api/v1/properties/{prop_id}/bank-statements/import",
        headers={"Authorization": f"Bearer {token}", "X-Property-ID": prop_id},
        files=files_dup,
    )
    assert dup_res.status_code == 409


def test_malformed_bank_statement_csv(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]

    # CSV missing amount column
    invalid_csv = "Date,Description\n2026-03-01,Test Entry\n"
    files = {"file": ("bad.csv", io.BytesIO(invalid_csv.encode("utf-8")), "text/csv")}
    res = client.post(
        f"/api/v1/properties/{prop_id}/bank-statements/import",
        headers={"Authorization": f"Bearer {token}", "X-Property-ID": prop_id},
        files=files,
    )
    assert res.status_code == 400
    assert "Statement must have 'Amount'" in res.json()["detail"]


def test_reconciliation_decision_flow(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]

    csv_data = (
        "Date,Description,Reference,Amount\n"
        f"{date.today().isoformat()},Unrecognized Transfer,UNKNOWN-99,500.00\n"
    )
    files = {"file": ("stmt.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    res = client.post(
        f"/api/v1/properties/{prop_id}/bank-statements/import",
        headers={"Authorization": f"Bearer {token}", "X-Property-ID": prop_id},
        files=files,
    )
    assert res.status_code == 201
    tx_id = res.json()["transactions"][0]["transaction_id"]

    # Manual correction decision
    correct_res = client.post(
        f"/api/v1/properties/{prop_id}/bank-statements/transactions/{tx_id}/decide",
        headers=_headers(token, prop_id),
        json={
            "decision": "correct",
            "matched_entity_type": "payment",
            "matched_entity_id": "pay-custom-123",
            "notes": "Verified offline with resident bank receipt",
        },
    )
    assert correct_res.status_code == 200
    updated_tx = correct_res.json()
    assert updated_tx["match_status"] == "matched"
    assert updated_tx["matched_entity_type"] == "payment"
    assert updated_tx["matched_entity_id"] == "pay-custom-123"


# =============================================================================
# 3. Expense Approval Workflow & Threshold Enforcement Tests
# =============================================================================

def test_expense_approval_threshold_enforcement(test_setup):
    client = test_setup["client"]
    pm_token = test_setup["pm_token"]
    acct_token = test_setup["acct_token"]
    prop_id = test_setup["prop_id"]

    # 1. Low expense ($500) -> PM can approve
    low_exp = client.post(
        f"/api/v1/properties/{prop_id}/expenses",
        headers=_headers(pm_token, prop_id),
        json={
            "category": "maintenance",
            "amount": "500.00",
            "expense_date": str(date.today()),
            "description": "Plumbing repair",
        },
    ).json()

    submit_low = client.post(
        f"/api/v1/properties/{prop_id}/expenses/{low_exp['expense_id']}/submit",
        headers=_headers(pm_token, prop_id),
    )
    assert submit_low.status_code == 200
    assert submit_low.json()["approval_status"] == "pending"

    approve_low = client.post(
        f"/api/v1/properties/{prop_id}/expenses/{low_exp['expense_id']}/decide",
        headers=_headers(pm_token, prop_id),
        json={"decision": "approve", "reason": "Within routine maintenance budget"},
    )
    assert approve_low.status_code == 200
    assert approve_low.json()["approval_status"] == "approved"

    # 2. High expense ($2,500) -> PM approval is rejected due to threshold
    high_exp = client.post(
        f"/api/v1/properties/{prop_id}/expenses",
        headers=_headers(pm_token, prop_id),
        json={
            "category": "repairs",
            "amount": "2500.00",
            "expense_date": str(date.today()),
            "description": "Roof repair",
        },
    ).json()

    client.post(
        f"/api/v1/properties/{prop_id}/expenses/{high_exp['expense_id']}/submit",
        headers=_headers(pm_token, prop_id),
    )

    pm_attempt = client.post(
        f"/api/v1/properties/{prop_id}/expenses/{high_exp['expense_id']}/decide",
        headers=_headers(pm_token, prop_id),
        json={"decision": "approve", "reason": "PM attempting large approval"},
    )
    assert pm_attempt.status_code in (400, 403)
    assert "exceeds Property Manager approval threshold" in pm_attempt.json()["detail"]

    # 3. Accountant can approve the high expense
    acct_approval = client.post(
        f"/api/v1/properties/{prop_id}/expenses/{high_exp['expense_id']}/decide",
        headers=_headers(acct_token, prop_id),
        json={"decision": "approve", "reason": "Accountant authorized capital expenditure"},
    )
    assert acct_approval.status_code == 200
    assert acct_approval.json()["approval_status"] == "approved"


def test_detect_duplicate_expense(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]

    # Pre-record an expense
    client.post(
        f"/api/v1/properties/{prop_id}/expenses",
        headers=_headers(token, prop_id),
        json={
            "category": "maintenance",
            "amount": "350.00",
            "expense_date": str(date.today()),
            "description": "Lawn care",
        },
    )

    # Check duplicate endpoint with identical values
    dup_res = client.post(
        f"/api/v1/properties/{prop_id}/expenses/check-duplicate",
        headers=_headers(token, prop_id),
        json={
            "category": "maintenance",
            "amount": "350.00",
            "expense_date": str(date.today()),
            "description": "Second submission",
        },
    )
    assert dup_res.status_code == 200
    assert dup_res.json()["is_duplicate"] is True


# =============================================================================
# 4. Compliance Monitoring & SLA Escalation Tests
# =============================================================================

def test_compliance_monitoring_and_sla_escalation(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]

    # Create an overdue invoice (20 days ago) with high balance ($2,000)
    client.post(
        f"/api/v1/properties/{prop_id}/invoices",
        headers=_headers(token, prop_id),
        json={
            "invoice_number": "OVERDUE-001",
            "invoice_type": "rent",
            "amount": "2000.00",
            "currency": "USD",
            "issue_date": str(date.today() - timedelta(days=25)),
            "due_date": str(date.today() - timedelta(days=20)),
        },
    )

    # Run compliance audit
    audit_res = client.post(
        f"/api/v1/properties/{prop_id}/compliance/audit",
        headers=_headers(token, prop_id),
    )
    assert audit_res.status_code == 200
    findings = audit_res.json()
    assert len(findings) >= 1

    overdue_finding = next((f for f in findings if f["category"] == "overdue_rent"), None)
    assert overdue_finding is not None
    assert overdue_finding["severity"] in ("high", "critical")

    finding_id = overdue_finding["finding_id"]

    # Assign finding
    assign_res = client.post(
        f"/api/v1/properties/{prop_id}/compliance/findings/{finding_id}/assign",
        headers=_headers(token, prop_id),
        json={"assigned_to": "pm@example.com"},
    )
    assert assign_res.status_code == 200
    assert assign_res.json()["status"] == "under_review"

    # Resolve finding
    resolve_res = client.post(
        f"/api/v1/properties/{prop_id}/compliance/findings/{finding_id}/resolve",
        headers=_headers(token, prop_id),
        json={"action": "resolve", "reason": "Payment arrangement confirmed with tenant"},
    )
    assert resolve_res.status_code == 200
    assert resolve_res.json()["status"] == "resolved"


# =============================================================================
# 5. Email Notification Services & Simulation Tests
# =============================================================================

def test_email_notification_preview_and_simulated_dispatch(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]

    # Preview notification
    preview_res = client.post(
        f"/api/v1/properties/{prop_id}/notifications/preview",
        headers=_headers(token, prop_id),
        json={
            "notification_type": "overdue_rent",
            "recipient_email": "tenant@example.com",
            "recipient_name": "Bob Resident",
            "context_data": {
                "unit_id": "4B",
                "invoice_number": "INV-100",
                "amount": "1250.00",
                "due_date": "2026-03-01",
                "days_overdue": "15",
            },
        },
    )
    assert preview_res.status_code == 200
    preview = preview_res.json()
    assert "Overdue Rent Notice" in preview["subject"]
    assert "Bob Resident" in preview["body_text"]
    assert "1250.00" in preview["body_text"]
    assert "<html>" in preview["body_html"]

    # Dispatch notification (in Simulation Mode)
    send_res = client.post(
        f"/api/v1/properties/{prop_id}/notifications/send",
        headers=_headers(token, prop_id),
        json={
            "notification_type": "overdue_rent",
            "recipient_email": "tenant@example.com",
            "recipient_name": "Bob Resident",
            "context_data": {
                "unit_id": "4B",
                "invoice_number": "INV-100",
                "amount": "1250.00",
                "due_date": "2026-03-01",
                "days_overdue": "15",
            },
            "idempotency_key": "idemp-key-999",
        },
    )
    assert send_res.status_code == 201
    record = send_res.json()
    assert record["status"] == "simulated"
    assert record["delivery_mode"] == "simulated"
    assert record["recipient_email"] == "tenant@example.com"

    # Idempotent re-send returns the exact same record without duplication
    resend_res = client.post(
        f"/api/v1/properties/{prop_id}/notifications/send",
        headers=_headers(token, prop_id),
        json={
            "notification_type": "overdue_rent",
            "recipient_email": "tenant@example.com",
            "recipient_name": "Bob Resident",
            "context_data": {"amount": "1250.00"},
            "idempotency_key": "idemp-key-999",
        },
    )
    assert resend_res.status_code == 201
    assert resend_res.json()["notification_id"] == record["notification_id"]


# =============================================================================
# 6. Financial Reporting & CSV Exports Tests
# =============================================================================

def test_financial_summary_and_owner_statement_reporting(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]

    # Create billed rent
    client.post(
        f"/api/v1/properties/{prop_id}/invoices",
        headers=_headers(token, prop_id),
        json={
            "invoice_number": "INV-REP-01",
            "invoice_type": "rent",
            "amount": "3000.00",
            "issue_date": str(date.today()),
            "due_date": str(date.today() + timedelta(days=5)),
        },
    )
    # Record payment collected (settled)
    client.post(
        f"/api/v1/properties/{prop_id}/payments",
        headers=_headers(token, prop_id),
        json={
            "amount": "2000.00",
            "payment_date": str(date.today()),
            "payment_method": "ach",
            "status": "settled",
            "reference": "DEP-01",
        },
    )
    # Record expense
    client.post(
        f"/api/v1/properties/{prop_id}/expenses",
        headers=_headers(token, prop_id),
        json={
            "category": "utilities",
            "amount": "400.00",
            "expense_date": str(date.today()),
            "description": "Water and electric",
        },
    )

    # 1. Financial Summary JSON
    sum_res = client.post(
        f"/api/v1/properties/{prop_id}/reports/financial-summary",
        headers=_headers(token, prop_id),
    ) if False else client.get(
        f"/api/v1/properties/{prop_id}/reports/financial-summary",
        headers=_headers(token, prop_id),
    )
    assert sum_res.status_code == 200
    report = sum_res.json()
    assert Decimal(str(report["total_rent_billed"])) >= Decimal("3000.00")
    assert Decimal(str(report["total_rent_collected"])) >= Decimal("2000.00")
    assert Decimal(str(report["total_expenses"])) >= Decimal("400.00")
    assert Decimal(str(report["net_operating_income"])) == (
        Decimal(str(report["total_rent_collected"])) - Decimal(str(report["total_expenses"]))
    )

    # 2. Financial Summary CSV
    csv_res = client.get(
        f"/api/v1/properties/{prop_id}/reports/financial-summary/csv",
        headers=_headers(token, prop_id),
    )
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert "Property Financial Summary" in csv_res.text
    assert "Net Operating Income" in csv_res.text

    # 3. Owner Statement JSON
    stmt_res = client.get(
        f"/api/v1/properties/{prop_id}/reports/owner-statement",
        headers=_headers(token, prop_id),
    )
    assert stmt_res.status_code == 200
    stmt = stmt_res.json()
    assert "Alpha Towers" in stmt["property_name"]
    assert Decimal(str(stmt["gross_rental_income"])) >= Decimal("2000.00")
    assert Decimal(str(stmt["management_fee_rate"])) == Decimal("0.08")
    assert Decimal(str(stmt["management_fee_amount"])) > Decimal("0.00")

    # 4. Owner Statement CSV
    stmt_csv = client.get(
        f"/api/v1/properties/{prop_id}/reports/owner-statement/csv",
        headers=_headers(token, prop_id),
    )
    assert stmt_csv.status_code == 200
    assert "text/csv" in stmt_csv.headers["content-type"]
    assert "Owner Statement" in stmt_csv.text


# =============================================================================
# 7. Property Isolation and Adapter Contract Tests
# =============================================================================

def test_property_level_data_isolation(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop_id = test_setup["prop_id"]
    prop_other = test_setup["prop_other"]

    # User authorized for prop_id attempting to access prop_other gets 403
    bad_access = client.get(
        f"/api/v1/properties/{prop_other}/invoices",
        headers=_headers(token, prop_other),
    )
    assert bad_access.status_code == 403


def test_financial_record_adapter_domain_integration(test_setup):
    domain_repo = test_setup["domain_repo"]
    prop_id = test_setup["prop_id"]

    inv = domain_repo.create_invoice(
        prop_id,
        InvoiceCreate(
            invoice_number="ADAPT-01",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("1500.00"),
            currency="USD",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=10),
        ),
    )
    pay = domain_repo.create_payment(
        prop_id,
        PaymentCreate(
            amount=Decimal("1500.00"),
            payment_date=date.today(),
            payment_method=PaymentMethod.ACH,
            reference="ADAPT-PAY",
        ),
    )
    exp = domain_repo.create_expense(
        prop_id,
        ExpenseCreate(
            category=ExpenseCategory.MAINTENANCE,
            amount=Decimal("200.00"),
            expense_date=date.today(),
            description="Filter replacements",
        ),
    )

    records = adapt_domain_records(
        invoices=[inv],
        payments=[pay],
        expenses=[exp],
    )
    assert len(records) == 3
    types = [r.record_type.value for r in records]
    assert "invoice" in types
    assert "rent_receipt" in types
