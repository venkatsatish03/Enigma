from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import uuid
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
    ExpenseApprovalDecision,
    ExpenseCategory,
    ExpenseCreate,
    ExpenseStatus,
    InvoiceCreate,
    InvoiceStatus,
    InvoiceType,
    MatchDecisionRequest,
    NotificationDeliveryMode,
    NotificationPreviewRequest,
    NotificationSendRequest,
    NotificationStatus,
    NotificationType,
    PaymentCreate,
    PaymentMethod,
    PaymentStatus,
)
from services.financial_reporting_service import FinancialReportingService
from tools.audit_trail import AuditTrail
from tools.auth_config import AuthConfig
from tools.auth_crypto import create_access_token, hash_password
from tools.auth_dependencies import (
    get_auth_config,
    get_auth_repository,
    get_domain_repository,
)
from tools.auth_repository import AuthRepository


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
    beta_pm = auth_repo.create_user(email="pm_beta@example.com", password_hash=hash_password("Pass123!"))

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
    auth_repo.create_membership(property_id=prop_other, user_id=beta_pm.user_id, role=UserRole.PROPERTY_MANAGER)

    # Dependency overrides
    main.app.dependency_overrides[get_auth_config] = lambda: config
    main.app.dependency_overrides[get_auth_repository] = lambda: auth_repo
    main.app.dependency_overrides[get_domain_repository] = lambda: domain_repo
    main.app.dependency_overrides[main.get_audit_trail] = lambda: audit_trail

    pm_token, _ = create_access_token(user_id=pm_user.user_id, email=pm_user.email, secret_key=config.secret_key)
    acct_token, _ = create_access_token(user_id=acct_user.user_id, email=acct_user.email, secret_key=config.secret_key)
    beta_token, _ = create_access_token(user_id=beta_pm.user_id, email=beta_pm.email, secret_key=config.secret_key)

    client = TestClient(main.app)

    yield {
        "client": client,
        "domain_repo": domain_repo,
        "audit_trail": audit_trail,
        "prop_id": prop_id,
        "prop_other": prop_other,
        "pm_token": pm_token,
        "acct_token": acct_token,
        "beta_token": beta_token,
    }

    main.app.dependency_overrides.clear()


def _headers(token: str, prop_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Property-ID": prop_id}


# =============================================================================
# 1. ROUTE ORDERING REGRESSION TESTS
# =============================================================================

def test_route_ordering_bank_statement_transactions(test_setup):
    """Confirm GET /bank-statements/transactions is not shadowed by /bank-statements/{id}."""
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop = test_setup["prop_id"]

    res = client.get(
        f"/api/v1/properties/{prop}/bank-statements/transactions",
        headers=_headers(token, prop),
    )
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_route_ordering_expenses_check_duplicate(test_setup):
    """Confirm POST /expenses/check-duplicate routes to duplicate check and not item endpoint."""
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop = test_setup["prop_id"]

    payload = {
        "category": "maintenance",
        "amount": "150.00",
        "expense_date": str(date.today()),
        "description": "Routine filter cleaning",
    }
    res = client.post(
        f"/api/v1/properties/{prop}/expenses/check-duplicate",
        headers=_headers(token, prop),
        json=payload,
    )
    assert res.status_code == 200
    data = res.json()
    assert "is_duplicate" in data
    assert data["is_duplicate"] is False


# =============================================================================
# 2. PROPERTY-LEVEL AUTHORIZATION & DATA ISOLATION TESTS
# =============================================================================

def test_property_level_isolation_mismatches(test_setup):
    client = test_setup["client"]
    alpha_token = test_setup["pm_token"]
    beta_token = test_setup["beta_token"]
    prop_a = test_setup["prop_id"]
    prop_b = test_setup["prop_other"]

    # 1. Token has no role on prop_b
    res = client.get(
        f"/api/v1/properties/{prop_b}/invoices",
        headers=_headers(alpha_token, prop_b),
    )
    assert res.status_code == 403

    # 2. Path prop-b vs X-Property-ID prop-a mismatch
    res = client.get(
        f"/api/v1/properties/{prop_b}/invoices",
        headers=_headers(alpha_token, prop_a),
    )
    assert res.status_code == 403

    # 3. Create invoice in Prop B, try to access via Prop A
    inv_b = client.post(
        f"/api/v1/properties/{prop_b}/invoices",
        headers=_headers(beta_token, prop_b),
        json={
            "invoice_number": "BETA-INV-1",
            "invoice_type": "rent",
            "amount": "950.00",
            "currency": "USD",
            "issue_date": str(date.today()),
            "due_date": str(date.today() + timedelta(days=5)),
        },
    ).json()

    # Querying invoice_b under property A must 404 (isolated)
    cross_res = client.get(
        f"/api/v1/properties/{prop_a}/invoices/{inv_b['invoice_id']}",
        headers=_headers(alpha_token, prop_a),
    )
    assert cross_res.status_code == 404


# =============================================================================
# 3. EXISTING DATABASE MIGRATION COMPATIBILITY TEST
# =============================================================================

def test_existing_database_migration_compatibility(tmp_path):
    """
    Simulate an older Enigma database that has properties and expenses
    without approval columns, and lacks invoices, bank statements,
    compliance findings, or notifications.
    """
    old_db = tmp_path / "legacy_enigma.db"
    conn = sqlite3.connect(str(old_db))
    conn.execute(
        """
        CREATE TABLE properties (
            property_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            address TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE expenses (
            expense_id TEXT PRIMARY KEY,
            property_id TEXT NOT NULL,
            category TEXT NOT NULL,
            amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            expense_date TEXT NOT NULL,
            status TEXT NOT NULL,
            description TEXT NOT NULL,
            vendor_id TEXT,
            unit_id TEXT,
            document_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO properties VALUES ('legacy-prop', 'Legacy Manor', '123 Old St', '2025-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO expenses VALUES (
            'legacy-exp-1', 'legacy-prop', 'repairs', '350.00', 'USD',
            '2025-02-01', 'pending', 'Old roof repair', NULL, NULL, NULL,
            '2025-02-01T00:00:00Z', '2025-02-01T00:00:00Z'
        )
        """
    )
    conn.commit()
    conn.close()

    # Open with DomainRepository — safe migration should run automatically
    migrated_repo = DomainRepository(database_path=str(old_db))

    # 1. Existing expense still present and has approval_status initialized to 'pending'
    exp = migrated_repo.get_expense("legacy-prop", "legacy-exp-1")
    assert exp is not None
    assert exp.amount == Decimal("350.00")
    assert exp.approval_status == "pending"

    # 2. New tables exist and can be written to
    inv = migrated_repo.create_invoice(
        "legacy-prop",
        InvoiceCreate(
            invoice_number="MIG-001",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("1200.00"),
            currency="USD",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=10),
        ),
    )
    assert inv.invoice_number == "MIG-001"
    assert inv.outstanding_amount == Decimal("1200.00")


# =============================================================================
# 4. FINANCIAL INTEGRITY: OVERPAYMENT, STATUS & ALLOCATION
# =============================================================================

def test_financial_integrity_payment_allocations(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop = test_setup["prop_id"]

    # 1. Create invoice for $1000.00
    inv = client.post(
        f"/api/v1/properties/{prop}/invoices",
        headers=_headers(token, prop),
        json={
            "invoice_number": "INTEG-001",
            "invoice_type": "rent",
            "amount": "1000.00",
            "currency": "USD",
            "issue_date": str(date.today()),
            "due_date": str(date.today() + timedelta(days=10)),
        },
    ).json()
    inv_id = inv["invoice_id"]

    # 2. Rejection of zero or negative payment
    bad_zero = client.post(
        f"/api/v1/properties/{prop}/invoices/{inv_id}/payments",
        headers=_headers(token, prop),
        json={"amount": "0.00"},
    )
    assert bad_zero.status_code == 400

    bad_neg = client.post(
        f"/api/v1/properties/{prop}/invoices/{inv_id}/payments",
        headers=_headers(token, prop),
        json={"amount": "-50.00"},
    )
    assert bad_neg.status_code == 400

    # 3. Partial payment of $400.00
    p1 = client.post(
        f"/api/v1/properties/{prop}/invoices/{inv_id}/payments",
        headers=_headers(token, prop),
        json={"amount": "400.00"},
    )
    assert p1.status_code == 200
    p1_data = p1.json()
    assert Decimal(p1_data["amount_paid"]) == Decimal("400.00")
    assert Decimal(p1_data["outstanding_amount"]) == Decimal("600.00")
    assert p1_data["status"] == "partially_paid"

    # 4. Overpayment rejection: attempt to allocate $700 (exceeds $600 remaining)
    overpay = client.post(
        f"/api/v1/properties/{prop}/invoices/{inv_id}/payments",
        headers=_headers(token, prop),
        json={"amount": "700.00"},
    )
    assert overpay.status_code == 400
    assert "exceeds" in overpay.json()["detail"].lower()

    # 5. Full remaining payment of $600.00
    p2 = client.post(
        f"/api/v1/properties/{prop}/invoices/{inv_id}/payments",
        headers=_headers(token, prop),
        json={"amount": "600.00"},
    )
    assert p2.status_code == 200
    p2_data = p2.json()
    assert Decimal(p2_data["amount_paid"]) == Decimal("1000.00")
    assert Decimal(p2_data["outstanding_amount"]) == Decimal("0.00")
    assert p2_data["status"] == "paid"

    # 6. Allocation towards already fully paid invoice rejected
    post_full = client.post(
        f"/api/v1/properties/{prop}/invoices/{inv_id}/payments",
        headers=_headers(token, prop),
        json={"amount": "50.00"},
    )
    assert post_full.status_code == 400
    assert "already fully paid" in post_full.json()["detail"].lower()


# =============================================================================
# 5. RECONCILIATION: DUPLICATE PREVENTION & MATCH VALIDATION
# =============================================================================

def test_reconciliation_duplicate_matching_and_validation(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop = test_setup["prop_id"]
    repo = test_setup["domain_repo"]

    # Pre-record a single payment of $1200
    pay = repo.create_payment(
        prop,
        PaymentCreate(
            amount=Decimal("1200.00"),
            payment_date=date.today(),
            payment_method=PaymentMethod.ACH,
            reference_number="REC-SINGLE-REF",
        ),
    )

    # Bank statement with TWO identical transaction lines of $1200
    csv_content = (
        "Date,Description,Reference,Amount,Balance\n"
        f"{date.today().isoformat()},Direct Deposit Rent,REC-SINGLE-REF,1200.00,5000.00\n"
        f"{date.today().isoformat()},Direct Deposit Rent 2,REC-SINGLE-REF,1200.00,6200.00\n"
    )
    files = {"file": ("dup_tx_statement.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    res = client.post(
        f"/api/v1/properties/{prop}/bank-statements/import",
        headers={"Authorization": f"Bearer {token}", "X-Property-ID": prop},
        files=files,
    )
    assert res.status_code == 201
    data = res.json()
    txs = data["transactions"]
    assert len(txs) == 2

    # First transaction matched the payment
    assert txs[0]["match_status"] == "matched"
    assert txs[0]["matched_entity_id"] == pay.payment_id

    # Second transaction MUST NOT match the same payment (duplicate match prevention)
    assert txs[1]["match_status"] == "unmatched"

    # Attempting to approve an unmatched transaction without target entity fails
    bad_approve = client.post(
        f"/api/v1/properties/{prop}/bank-statements/transactions/{txs[1]['transaction_id']}/decide",
        headers=_headers(token, prop),
        json={"decision": "approve"},
    )
    assert bad_approve.status_code == 400

    # Attempting to manually correct second transaction to the ALREADY matched payment fails
    conflict_match = client.post(
        f"/api/v1/properties/{prop}/bank-statements/transactions/{txs[1]['transaction_id']}/decide",
        headers=_headers(token, prop),
        json={
            "decision": "correct",
            "matched_entity_type": "payment",
            "matched_entity_id": pay.payment_id,
        },
    )
    assert conflict_match.status_code == 400
    assert "already reconciled" in conflict_match.json()["detail"].lower()


# =============================================================================
# 6. EXPENSE APPROVAL: ROLE PERMISSIONS & STATE TRANSITIONS
# =============================================================================

def test_expense_approval_role_and_state_transitions(test_setup):
    client = test_setup["client"]
    pm_token = test_setup["pm_token"]
    acct_token = test_setup["acct_token"]
    prop = test_setup["prop_id"]

    # 1. Create large expense ($3500.00)
    exp = client.post(
        f"/api/v1/properties/{prop}/expenses",
        headers=_headers(pm_token, prop),
        json={
            "category": "utilities",
            "amount": "3500.00",
            "expense_date": str(date.today()),
            "description": "Boiler emergency repairs",
        },
    ).json()
    exp_id = exp["expense_id"]

    # 2. Cannot decide before submit
    pre_decide = client.post(
        f"/api/v1/properties/{prop}/expenses/{exp_id}/decide",
        headers=_headers(pm_token, prop),
        json={"decision": "approve", "reason": "Try prematurely"},
    )
    assert pre_decide.status_code == 400

    # 3. Submit
    submit_res = client.post(
        f"/api/v1/properties/{prop}/expenses/{exp_id}/submit",
        headers=_headers(pm_token, prop),
    )
    assert submit_res.status_code == 200
    assert submit_res.json()["approval_status"] == "pending"

    # 4. Property Manager attempts to approve $3500 -> 403 Forbidden (Threshold is $2,500)
    pm_attempt = client.post(
        f"/api/v1/properties/{prop}/expenses/{exp_id}/decide",
        headers=_headers(pm_token, prop),
        json={"decision": "approve", "reason": "PM trying to approve above limit"},
    )
    assert pm_attempt.status_code == 403

    # 5. Accountant approves -> 200 OK
    acct_approve = client.post(
        f"/api/v1/properties/{prop}/expenses/{exp_id}/decide",
        headers=_headers(acct_token, prop),
        json={"decision": "approve", "reason": "Accountant verified capex allocation"},
    )
    assert acct_approve.status_code == 200
    assert acct_approve.json()["approval_status"] == "approved"

    # 6. Re-deciding an already approved expense fails (invalid state transition)
    re_decide = client.post(
        f"/api/v1/properties/{prop}/expenses/{exp_id}/decide",
        headers=_headers(acct_token, prop),
        json={"decision": "reject", "reason": "Changed my mind"},
    )
    assert re_decide.status_code == 400


# =============================================================================
# 7. NOTIFICATION PREVIEW, SIMULATION & IDEMPOTENCY
# =============================================================================

def test_notification_preview_simulation_and_idempotency(test_setup):
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop = test_setup["prop_id"]

    # 1. Preview without sending
    preview = client.post(
        f"/api/v1/properties/{prop}/notifications/preview",
        headers=_headers(token, prop),
        json={
            "notification_type": "overdue_rent",
            "recipient_email": "bob@stone.com",
            "recipient_name": "Bob Stone",
            "context_data": {
                "unit_id": "304",
                "invoice_number": "INV-304",
                "amount": "1450.00",
                "due_date": "2026-10-01",
                "days_overdue": "18",
            },
        },
    )
    assert preview.status_code == 200
    prev_data = preview.json()
    assert "Overdue Rent Notice" in prev_data["subject"]
    assert "Bob Stone" in prev_data["body_text"]
    assert "<html>" in prev_data["body_html"]

    # Confirm preview did not create records
    list_prev = client.get(
        f"/api/v1/properties/{prop}/notifications",
        headers=_headers(token, prop),
    ).json()
    assert len(list_prev) == 0

    # 2. Send in simulation mode with idempotency key
    idemp_key = f"notif-test-{uuid.uuid4()}"
    send_payload = {
        "notification_type": "overdue_rent",
        "recipient_email": "bob@stone.com",
        "recipient_name": "Bob Stone",
        "context_data": {
            "unit_id": "304",
            "amount": "1450.00",
        },
        "idempotency_key": idemp_key,
    }
    send_res = client.post(
        f"/api/v1/properties/{prop}/notifications/send",
        headers=_headers(token, prop),
        json=send_payload,
    )
    assert send_res.status_code == 201
    sent_record = send_res.json()
    assert sent_record["delivery_mode"] == "simulated"
    assert sent_record["status"] == "simulated"
    first_id = sent_record["notification_id"]

    # 3. Duplicate send with identical idempotency key returns existing record
    dup_res = client.post(
        f"/api/v1/properties/{prop}/notifications/send",
        headers=_headers(token, prop),
        json=send_payload,
    )
    assert dup_res.status_code == 201
    assert dup_res.json()["notification_id"] == first_id

    # Confirm only one notification exists
    list_after = client.get(
        f"/api/v1/properties/{prop}/notifications",
        headers=_headers(token, prop),
    ).json()
    assert len(list_after) == 1


# =============================================================================
# 8. FINANCIAL REPORTING INDEPENDENT VERIFICATION
# =============================================================================

def test_financial_reporting_exact_formula_verification(test_setup):
    """
    Independent verification of collection rate, NOI, cash flow, and owner distributions
    against exact hand-calculated figures.
    """
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop = test_setup["prop_id"]
    repo = test_setup["domain_repo"]

    # Scenario:
    # Rent Invoiced:
    #   INV-1: $2,000.00
    #   INV-2: $1,000.00
    #   Total Billed = $3,000.00
    inv1 = repo.create_invoice(
        prop,
        InvoiceCreate(
            invoice_number="REP-INV-1",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("2000.00"),
            currency="USD",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=5),
        ),
    )
    inv2 = repo.create_invoice(
        prop,
        InvoiceCreate(
            invoice_number="REP-INV-2",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("1000.00"),
            currency="USD",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=5),
        ),
    )

    # Payments Collected (settled) and Allocated:
    #   Pay-1: $2,000.00 (settled -> allocated to REP-INV-1, remaining $0.00)
    #   Pay-2: $400.00 (settled -> allocated to REP-INV-2, remaining $600.00)
    #   Total Collected = $2,400.00
    #   Total Outstanding Obligations = $600.00
    repo.create_payment(
        prop,
        PaymentCreate(
            amount=Decimal("2000.00"),
            payment_date=date.today(),
            payment_method=PaymentMethod.ACH,
            status=PaymentStatus.SETTLED,
            reference_number="PAY-R1",
        ),
    )
    repo.record_invoice_payment(prop, inv1.invoice_id, Decimal("2000.00"))

    repo.create_payment(
        prop,
        PaymentCreate(
            amount=Decimal("400.00"),
            payment_date=date.today(),
            payment_method=PaymentMethod.CHECK,
            status=PaymentStatus.SETTLED,
            reference_number="PAY-R2",
        ),
    )
    repo.record_invoice_payment(prop, inv2.invoice_id, Decimal("400.00"))

    # Expenses Incurred:
    #   Exp-1: $500.00 (repairs)
    #   Exp-2: $300.00 (utilities)
    #   Total Expenses = $800.00
    repo.create_expense(
        prop,
        ExpenseCreate(
            category=ExpenseCategory.REPAIRS,
            amount=Decimal("500.00"),
            expense_date=date.today(),
            description="Plumbing repair",
        ),
    )
    repo.create_expense(
        prop,
        ExpenseCreate(
            category=ExpenseCategory.UTILITIES,
            amount=Decimal("300.00"),
            expense_date=date.today(),
            description="Water bill",
        ),
    )

    # Hand calculations:
    # Total Rent Billed = $3,000.00
    # Total Rent Collected = $2,400.00
    # Total Rent Outstanding = $3,000.00 - $2,400.00 = $600.00
    # Collection Rate = (2400 / 3000) * 100 = 80.00%
    # Total Expenses = $800.00
    # Net Operating Income (NOI) = $2,400.00 - $800.00 = $1,600.00
    # Management Fee (8% of gross collected) = $2,400.00 * 0.08 = $192.00
    # Net Owner Distribution = $1,600.00 - $192.00 = $1,408.00

    summary_res = client.get(
        f"/api/v1/properties/{prop}/reports/financial-summary",
        headers=_headers(token, prop),
    )
    assert summary_res.status_code == 200
    summary = summary_res.json()

    assert Decimal(summary["total_rent_billed"]) == Decimal("3000.00")
    assert Decimal(summary["total_rent_collected"]) == Decimal("2400.00")
    assert Decimal(summary["total_rent_outstanding"]) == Decimal("600.00")
    assert Decimal(summary["collection_rate_percentage"]) == Decimal("80.00")
    assert Decimal(summary["total_expenses"]) == Decimal("800.00")
    assert Decimal(summary["net_operating_income"]) == Decimal("1600.00")

    owner_res = client.get(
        f"/api/v1/properties/{prop}/reports/owner-statement",
        headers=_headers(token, prop),
    )
    assert owner_res.status_code == 200
    owner_stmt = owner_res.json()

    assert Decimal(owner_stmt["gross_rental_income"]) == Decimal("2400.00")
    assert Decimal(owner_stmt["total_operating_expenses"]) == Decimal("800.00")
    assert Decimal(owner_stmt["management_fee_amount"]) == Decimal("192.00")
    assert Decimal(owner_stmt["net_operating_income"]) == Decimal("1600.00")
    assert Decimal(owner_stmt["owner_distribution_amount"]) == Decimal("1408.00")


# =============================================================================
# 9. CONCURRENCY: SIMULTANEOUS PAYMENT ALLOCATIONS CANNOT OVERPAY
# =============================================================================

def test_concurrent_payment_allocations_cannot_overpay(test_setup):
    """
    Ensure two simultaneous payment allocation requests on a $1000 invoice
    cannot both allocate $700 (which would total $1400 overpayment).
    Exactly one request must succeed and the other must be rejected.
    """
    import concurrent.futures
    client = test_setup["client"]
    token = test_setup["pm_token"]
    prop = test_setup["prop_id"]

    inv = client.post(
        f"/api/v1/properties/{prop}/invoices",
        headers=_headers(token, prop),
        json={
            "invoice_number": f"CONCUR-INV-{uuid.uuid4()}",
            "invoice_type": "rent",
            "amount": "1000.00",
            "currency": "USD",
            "issue_date": str(date.today()),
            "due_date": str(date.today() + timedelta(days=10)),
        },
    ).json()
    inv_id = inv["invoice_id"]

    results = []

    def allocate_payment():
        res = client.post(
            f"/api/v1/properties/{prop}/invoices/{inv_id}/payments",
            headers=_headers(token, prop),
            json={"amount": "700.00"},
        )
        return res.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(allocate_payment)
        f2 = executor.submit(allocate_payment)
        results = [f1.result(), f2.result()]

    # One must succeed (200), one must fail (400)
    assert 200 in results
    assert 400 in results

    # Final outstanding balance must be exactly $300 ($700 paid), not negative
    final_inv = client.get(
        f"/api/v1/properties/{prop}/invoices/{inv_id}",
        headers=_headers(token, prop),
    ).json()
    assert Decimal(final_inv["amount_paid"]) == Decimal("700.00")
    assert Decimal(final_inv["outstanding_amount"]) == Decimal("300.00")


# =============================================================================
# 10. AUDIT EVENT PERSISTENCE FOR ALL FINANCIAL & COMPLIANCE ACTIONS
# =============================================================================

def test_audit_event_persistence_for_all_financial_operations(test_setup):
    """
    Confirm audit events are persisted for:
    1. Financial payment allocation (financial.invoice_payment_allocated)
    2. Expense decision (financial.expense_approval_decided)
    3. Reconciliation decision (financial.reconciliation_match_decided)
    4. Compliance resolution (compliance.finding_resolved)
    5. Notification dispatch (notification.dispatched)
    """
    client = test_setup["client"]
    token = test_setup["pm_token"]
    acct_token = test_setup["acct_token"]
    prop = test_setup["prop_id"]
    audit_trail = test_setup["audit_trail"]
    repo = test_setup["domain_repo"]

    # 1. Invoice Payment Allocation
    inv = client.post(
        f"/api/v1/properties/{prop}/invoices",
        headers=_headers(token, prop),
        json={
            "invoice_number": f"AUDIT-INV-{uuid.uuid4()}",
            "invoice_type": "rent",
            "amount": "500.00",
            "currency": "USD",
            "issue_date": str(date.today()),
            "due_date": str(date.today() + timedelta(days=5)),
        },
    ).json()
    client.post(
        f"/api/v1/properties/{prop}/invoices/{inv['invoice_id']}/payments",
        headers=_headers(token, prop),
        json={"amount": "250.00"},
    )

    # 2. Expense Decision
    exp = client.post(
        f"/api/v1/properties/{prop}/expenses",
        headers=_headers(token, prop),
        json={
            "category": "maintenance",
            "amount": "200.00",
            "expense_date": str(date.today()),
            "description": "HVAC filter",
        },
    ).json()
    client.post(f"/api/v1/properties/{prop}/expenses/{exp['expense_id']}/submit", headers=_headers(token, prop))
    client.post(
        f"/api/v1/properties/{prop}/expenses/{exp['expense_id']}/decide",
        headers=_headers(token, prop),
        json={"decision": "approve", "reason": "Standard replacement"},
    )

    # 3. Reconciliation Decision
    stmt = repo.create_bank_statement(
        property_id=prop,
        filename="stmt.csv",
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("1500.00"),
        statement_start_date=date.today(),
        statement_end_date=date.today(),
        total_transactions=1,
    )
    txs = repo.create_bank_transactions(
        property_id=prop,
        statement_id=stmt.statement_id,
        transactions=[{
            "transaction_date": date.today(),
            "description": "Resident payment",
            "reference": "REF-AUDIT",
            "amount": Decimal("500.00"),
            "transaction_type": "credit",
        }],
    )
    client.post(
        f"/api/v1/properties/{prop}/bank-statements/transactions/{txs[0].transaction_id}/decide",
        headers=_headers(token, prop),
        json={"decision": "reject", "notes": "Audited rejection"},
    )

    # 4. Compliance Resolution
    audit_findings = client.post(
        f"/api/v1/properties/{prop}/compliance/audit",
        headers=_headers(token, prop),
    ).json()
    finding_id = audit_findings[0]["finding_id"] if audit_findings else None
    if finding_id:
        client.post(
            f"/api/v1/properties/{prop}/compliance/findings/{finding_id}/resolve",
            headers=_headers(token, prop),
            json={"action": "resolve", "reason": "Verified and resolved in audit"},
        )

    # 5. Notification Dispatch
    client.post(
        f"/api/v1/properties/{prop}/notifications/send",
        headers=_headers(token, prop),
        json={
            "notification_type": "general",
            "recipient_email": "auditor@enigma.com",
            "recipient_name": "Lead Auditor",
            "subject": "Audit Check",
            "context_data": {"note": "Test dispatch"},
        },
    )

    # Inspect audit trail entries
    events = audit_trail.list_events(property_id=prop, limit=100)
    event_types = {e.event_type for e in events}

    assert "financial.invoice_payment_allocated" in event_types
    assert "financial.expense_approval_decided" in event_types
    assert "financial.reconciliation_match_decided" in event_types
    assert "notification.dispatched" in event_types
    if finding_id:
        assert "compliance.finding_resolved" in event_types


# =============================================================================
# 11. CONFIGURABLE PROPERTY MANAGER APPROVAL THRESHOLD
# =============================================================================

def test_configurable_manager_expense_threshold(test_setup, monkeypatch):
    """
    Verify Property Manager approval threshold is configurable via ENIGMA_MANAGER_EXPENSE_LIMIT:
    1. Default is $1,000.00 -> $1,800 expense is rejected (403).
    2. Setting ENIGMA_MANAGER_EXPENSE_LIMIT=2500.00 -> PM can approve $1,800 expense.
    """
    client = test_setup["client"]
    pm_token = test_setup["pm_token"]
    prop = test_setup["prop_id"]

    # 1. With default threshold ($1,000.00), PM cannot approve $1,800.00
    exp1 = client.post(
        f"/api/v1/properties/{prop}/expenses",
        headers=_headers(pm_token, prop),
        json={
            "category": "maintenance",
            "amount": "1800.00",
            "expense_date": str(date.today()),
            "description": "AC unit service",
        },
    ).json()
    client.post(f"/api/v1/properties/{prop}/expenses/{exp1['expense_id']}/submit", headers=_headers(pm_token, prop))

    attempt1 = client.post(
        f"/api/v1/properties/{prop}/expenses/{exp1['expense_id']}/decide",
        headers=_headers(pm_token, prop),
        json={"decision": "approve", "reason": "PM trying over default limit"},
    )
    assert attempt1.status_code == 403

    # 2. Reconfigure threshold via environment variable to $2,500.00
    monkeypatch.setenv("ENIGMA_MANAGER_EXPENSE_LIMIT", "2500.00")

    attempt2 = client.post(
        f"/api/v1/properties/{prop}/expenses/{exp1['expense_id']}/decide",
        headers=_headers(pm_token, prop),
        json={"decision": "approve", "reason": "PM approving within updated $2,500 threshold"},
    )
    assert attempt2.status_code == 200
    assert attempt2.json()["approval_status"] == "approved"


# =============================================================================
# 12. FINANCIAL REPORTING RULE REVIEW TARGETED TESTS
# =============================================================================

def test_reporting_payment_statuses_realized_vs_unsettled(test_setup):
    """
    Verify payment status handling in financial summary:
    - SETTLED and RECONCILED are counted as realized rent collected and cash inflows.
    - PENDING payments are excluded from realized cash and exposed in pending_rent_amount & pending_payments_count.
    - FAILED and REFUNDED payments are completely excluded from cash inflows.
    """
    client = test_setup["client"]
    token = test_setup["pm_token"]
    repo = test_setup["domain_repo"]

    # Use dedicated property for isolated test
    test_prop = repo.create_property(name="Payment Status Test Property")
    pid = test_prop.property_id

    # Create rent invoice: $5,000.00
    inv = repo.create_invoice(
        pid,
        InvoiceCreate(
            invoice_number="INV-STATUS-01",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("5000.00"),
            currency="USD",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
        ),
    )

    # 1. Settled payment: $2,000.00
    p_settled = repo.create_payment(
        pid,
        PaymentCreate(
            amount=Decimal("2000.00"),
            currency="USD",
            payment_date=date.today(),
            payment_method=PaymentMethod.ACH,
            status=PaymentStatus.SETTLED,
            reference_number="PAY-SETTLED",
        ),
    )
    repo.record_invoice_payment(pid, inv.invoice_id, Decimal("2000.00"))

    # 2. Reconciled payment: $1,000.00
    repo.create_payment(
        pid,
        PaymentCreate(
            amount=Decimal("1000.00"),
            currency="USD",
            payment_date=date.today(),
            payment_method=PaymentMethod.WIRE,
            status=PaymentStatus.RECONCILED,
            reference_number="PAY-RECONCILED",
        ),
    )
    repo.record_invoice_payment(pid, inv.invoice_id, Decimal("1000.00"))

    # 3. Pending payment: $800.00
    repo.create_payment(
        pid,
        PaymentCreate(
            amount=Decimal("800.00"),
            currency="USD",
            payment_date=date.today(),
            payment_method=PaymentMethod.ACH,
            status=PaymentStatus.PENDING,
            reference_number="PAY-PENDING",
        ),
    )

    # 4. Failed payment: $500.00
    repo.create_payment(
        pid,
        PaymentCreate(
            amount=Decimal("500.00"),
            currency="USD",
            payment_date=date.today(),
            payment_method=PaymentMethod.CREDIT_CARD,
            status=PaymentStatus.FAILED,
            reference_number="PAY-FAILED",
        ),
    )

    # 5. Refunded payment: $300.00
    repo.create_payment(
        pid,
        PaymentCreate(
            amount=Decimal("300.00"),
            currency="USD",
            payment_date=date.today(),
            payment_method=PaymentMethod.ACH,
            status=PaymentStatus.REFUNDED,
            reference_number="PAY-REFUNDED",
        ),
    )

    reporting_service = FinancialReportingService(repo)
    report = reporting_service.generate_financial_summary(pid)

    # Rent billed = $5,000.00
    assert report.total_rent_billed == Decimal("5000.00")

    # Realized rent collected = $2,000 (settled) + $1,000 (reconciled) = $3,000.00
    # Pending ($800), Failed ($500), Refunded ($300) MUST be excluded
    assert report.total_rent_collected == Decimal("3000.00")
    assert report.cash_inflows == Decimal("3000.00")

    # Pending metrics properly exposed
    assert report.pending_rent_amount == Decimal("800.00")
    assert report.pending_payments_count == 1

    # Outstanding obligation = $5,000.00 billed - $3,000.00 allocated = $2,000.00
    assert report.total_rent_outstanding == Decimal("2000.00")

    # Collection rate = (3000 / 5000) * 100 = 60.00%
    assert report.collection_rate_percentage == Decimal("60.00")


def test_reporting_zero_rent_period_collection_rate(test_setup):
    """
    Verify zero-billed-period behavior:
    - When total_rent_billed is $0.00, collection_rate_percentage is None (N/A) rather than misleading 100%.
    - CSV export displays 'N/A' for Collection Rate.
    """
    repo = test_setup["domain_repo"]
    test_prop = repo.create_property(name="Zero Rent Property")
    pid = test_prop.property_id

    reporting_service = FinancialReportingService(repo)
    report = reporting_service.generate_financial_summary(pid)

    assert report.total_rent_billed == Decimal("0.00")
    assert report.total_rent_collected == Decimal("0.00")
    assert report.collection_rate_percentage is None

    csv_output = reporting_service.export_summary_csv(report)
    assert "Collection Rate,N/A" in csv_output


def test_reporting_outstanding_rent_invoice_obligations(test_setup):
    """
    Verify outstanding rent is calculated directly from invoice obligations minus allocations,
    preventing double counting and date range mismatch.
    """
    repo = test_setup["domain_repo"]
    test_prop = repo.create_property(name="Outstanding Obligations Property")
    pid = test_prop.property_id

    # Create Invoice 1: $1,500.00, with $1,000 allocated -> $500 outstanding
    inv1 = repo.create_invoice(
        pid,
        InvoiceCreate(
            invoice_number="INV-OBL-1",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("1500.00"),
            currency="USD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 5),
        ),
    )
    repo.record_invoice_payment(pid, inv1.invoice_id, Decimal("1000.00"))

    # Create Invoice 2: $1,200.00, fully paid -> $0 outstanding
    inv2 = repo.create_invoice(
        pid,
        InvoiceCreate(
            invoice_number="INV-OBL-2",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("1200.00"),
            currency="USD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 5),
        ),
    )
    repo.record_invoice_payment(pid, inv2.invoice_id, Decimal("1200.00"))

    # Create Invoice 3: $800.00, zero payment -> $800 outstanding
    repo.create_invoice(
        pid,
        InvoiceCreate(
            invoice_number="INV-OBL-3",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("800.00"),
            currency="USD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 5),
        ),
    )

    reporting_service = FinancialReportingService(repo)
    report = reporting_service.generate_financial_summary(
        pid,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )

    # Total billed: $1,500 + $1,200 + $800 = $3,500.00
    assert report.total_rent_billed == Decimal("3500.00")
    # Total outstanding obligation: $500 + $0 + $800 = $1,300.00
    assert report.total_rent_outstanding == Decimal("1300.00")


def test_reporting_management_fee_excluded_vs_included(test_setup):
    """
    Verify NOI and owner distributions:
    1. Management fee excluded from operating expenses:
       - Calculated as gross_rental * effective_rate
       - Deducted from owner distribution
    2. Management fee ALREADY included in operating expenses:
       - Recognized from ExpenseCategory.MANAGEMENT expense
       - NOT deducted a second time from owner distribution (preventing double counting)
    """
    repo = test_setup["domain_repo"]
    reporting_service = FinancialReportingService(repo)

    # --- Scenario 1: Management fee NOT in operating expenses ---
    prop1 = repo.create_property(name="Fee Excluded Property")
    p1 = prop1.property_id

    # Rent collected: $10,000.00
    repo.create_payment(
        p1,
        PaymentCreate(
            amount=Decimal("10000.00"),
            currency="USD",
            payment_date=date.today(),
            payment_method=PaymentMethod.ACH,
            status=PaymentStatus.SETTLED,
        ),
    )
    # Operating expenses: $2,000.00 (repairs)
    repo.create_expense(
        p1,
        ExpenseCreate(
            category=ExpenseCategory.REPAIRS,
            amount=Decimal("2000.00"),
            currency="USD",
            expense_date=date.today(),
            description="HVAC repair",
        ),
    )

    stmt1 = reporting_service.generate_owner_statement(p1)
    # Gross rental: $10,000.00
    assert stmt1.gross_rental_income == Decimal("10000.00")
    # Operating expenses: $2,000.00
    assert stmt1.total_operating_expenses == Decimal("2000.00")
    # NOI: $10,000 - $2,000 = $8,000.00
    assert stmt1.net_operating_income == Decimal("8000.00")
    # Management fee (8% default): $800.00
    assert stmt1.management_fee_amount == Decimal("800.00")
    # Owner distribution: $8,000 - $800 = $7,200.00
    assert stmt1.owner_distribution_amount == Decimal("7200.00")

    # --- Scenario 2: Management fee ALREADY in operating expenses ---
    prop2 = repo.create_property(name="Fee Included Property")
    p2 = prop2.property_id

    # Rent collected: $10,000.00
    repo.create_payment(
        p2,
        PaymentCreate(
            amount=Decimal("10000.00"),
            currency="USD",
            payment_date=date.today(),
            payment_method=PaymentMethod.ACH,
            status=PaymentStatus.SETTLED,
        ),
    )
    # Operating expenses: $2,000 repairs + $800 management fee
    repo.create_expense(
        p2,
        ExpenseCreate(
            category=ExpenseCategory.REPAIRS,
            amount=Decimal("2000.00"),
            currency="USD",
            expense_date=date.today(),
            description="HVAC repair",
        ),
    )
    repo.create_expense(
        p2,
        ExpenseCreate(
            category=ExpenseCategory.MANAGEMENT,
            amount=Decimal("800.00"),
            currency="USD",
            expense_date=date.today(),
            description="Monthly management fee bill",
        ),
    )

    stmt2 = reporting_service.generate_owner_statement(p2)
    # Gross rental: $10,000.00
    assert stmt2.gross_rental_income == Decimal("10000.00")
    # Total operating expenses include management fee: $2,800.00
    assert stmt2.total_operating_expenses == Decimal("2800.00")
    # Recognized management fee amount: $800.00
    assert stmt2.management_fee_amount == Decimal("800.00")
    # NOI: $10,000 - $2,800 = $7,200.00
    assert stmt2.net_operating_income == Decimal("7200.00")
    # Owner distribution must NOT deduct $800 again! Distribution = $7,200.00
    assert stmt2.owner_distribution_amount == Decimal("7200.00")


def test_reporting_configurable_management_fee_rate(test_setup, monkeypatch):
    """
    Verify management fee rate configuration precedence:
    1. Per-request query param / argument override
    2. Property-level configuration
    3. Environment variable ENIGMA_MANAGEMENT_FEE_RATE
    4. Documented default: 0.08 (8%)
    """
    repo = test_setup["domain_repo"]
    reporting_service = FinancialReportingService(repo)

    # 1. Property with custom 6% configured rate
    prop = repo.create_property(name="Custom Rate Property", management_fee_rate=Decimal("0.06"))
    pid = prop.property_id
    repo.create_payment(
        pid,
        PaymentCreate(
            amount=Decimal("5000.00"),
            currency="USD",
            payment_date=date.today(),
            status=PaymentStatus.SETTLED,
        ),
    )

    stmt_prop = reporting_service.generate_owner_statement(pid)
    assert stmt_prop.management_fee_rate == Decimal("0.06")
    assert stmt_prop.management_fee_amount == Decimal("300.00")  # 6% of $5,000

    # 2. Per-request override: 10%
    stmt_override = reporting_service.generate_owner_statement(pid, management_fee_rate=Decimal("0.10"))
    assert stmt_override.management_fee_rate == Decimal("0.10")
    assert stmt_override.management_fee_amount == Decimal("500.00")  # 10% of $5,000

    # 3. Environment variable fallback for property without custom rate
    prop_default = repo.create_property(name="Env Rate Property")
    pid_env = prop_default.property_id
    repo.create_payment(
        pid_env,
        PaymentCreate(
            amount=Decimal("5000.00"),
            currency="USD",
            payment_date=date.today(),
            status=PaymentStatus.SETTLED,
        ),
    )
    monkeypatch.setenv("ENIGMA_MANAGEMENT_FEE_RATE", "0.075")
    stmt_env = reporting_service.generate_owner_statement(pid_env)
    assert stmt_env.management_fee_rate == Decimal("0.075")
    assert stmt_env.management_fee_amount == Decimal("375.00")  # 7.5% of $5,000


def test_reporting_currency_isolation(test_setup):
    """
    Verify multi-currency isolation:
    USD reports only aggregate USD records, completely excluding foreign currencies (e.g. EUR).
    """
    repo = test_setup["domain_repo"]
    prop = repo.create_property(name="Multi Currency Property")
    pid = prop.property_id

    # Create USD invoice and payment
    repo.create_invoice(
        pid,
        InvoiceCreate(
            invoice_number="INV-USD-1",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("2500.00"),
            currency="USD",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=5),
        ),
    )
    repo.create_payment(
        pid,
        PaymentCreate(
            amount=Decimal("2500.00"),
            currency="USD",
            payment_date=date.today(),
            status=PaymentStatus.SETTLED,
        ),
    )

    # Create EUR invoice and payment on the same property
    repo.create_invoice(
        pid,
        InvoiceCreate(
            invoice_number="INV-EUR-1",
            invoice_type=InvoiceType.RENT,
            amount=Decimal("3000.00"),
            currency="EUR",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=5),
        ),
    )
    repo.create_payment(
        pid,
        PaymentCreate(
            amount=Decimal("3000.00"),
            currency="EUR",
            payment_date=date.today(),
            status=PaymentStatus.SETTLED,
        ),
    )

    reporting_service = FinancialReportingService(repo)

    # USD Summary
    usd_report = reporting_service.generate_financial_summary(pid, currency="USD")
    assert usd_report.currency == "USD"
    assert usd_report.total_rent_billed == Decimal("2500.00")
    assert usd_report.total_rent_collected == Decimal("2500.00")

    # EUR Summary
    eur_report = reporting_service.generate_financial_summary(pid, currency="EUR")
    assert eur_report.currency == "EUR"
    assert eur_report.total_rent_billed == Decimal("3000.00")
    assert eur_report.total_rent_collected == Decimal("3000.00")


