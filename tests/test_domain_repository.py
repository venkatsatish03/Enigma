"""Comprehensive tests for Phase 2 Shared Data Model and Repository Layer.

Validates:
- Entity creation, retrieval, update, and listing for all 9 entities.
- SQLite foreign-key enforcement and referential integrity.
- Cross-property reference rejections (leases, payments, expenses, work orders).
- Strict property data isolation across tenants.
- Atomic multi-record transaction commit and rollback.
- Decimal-safe monetary precision without floating-point drift.
- Persistence after closing and reopening the SQLite database.
- Non-destructive migration and backward compatibility with Phase 1 auth, audit, and approvals.
- Property-scoped CRUD API integration with JWT auth and role permission enforcement.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from repository import (
    CrossPropertyReferenceError,
    DomainRepository,
    EntityNotFoundError,
    IntegrityConflictError,
    InvalidOperationError,
)
from schemas.approvals import ApprovalRequestCreate
from schemas.audit import AuditEventCreate
from schemas.auth import Permission, UserRole
from schemas.domain import (
    DocumentMetadataCreate,
    ExpenseCategory,
    ExpenseCreate,
    ExpenseStatus,
    ExpenseUpdate,
    LeaseCreate,
    LeaseStatus,
    LeaseUpdate,
    PaymentCreate,
    PaymentMethod,
    PaymentStatus,
    PaymentUpdate,
    PropertyUpdate,
    ResidentCreate,
    ResidentStatus,
    ResidentUpdate,
    UnitCreate,
    UnitStatus,
    UnitUpdate,
    VendorCategory,
    VendorCreate,
    VendorUpdate,
    WorkOrderCreate,
    WorkOrderPriority,
    WorkOrderStatus,
    WorkOrderUpdate,
)
from tools.approval_workflow import ApprovalWorkflow
from tools.audit_trail import AuditTrail
from tools.auth_config import AuthConfig
from tools.auth_crypto import create_access_token, hash_password
from tools.auth_dependencies import (
    get_auth_config,
    get_auth_repository,
    get_domain_repository,
)
from tools.auth_repository import AuthRepository

TEST_SECRET = "phase2-test-secret-at-least-32-chars-long-987654321"


@pytest.fixture
def repo_fixture(tmp_path: Path):
    db_path = tmp_path / "test_domain.sqlite3"
    repo = DomainRepository(db_path)
    return repo, db_path


@pytest.fixture
def api_setup(tmp_path: Path):
    auth_db = tmp_path / "api_auth.sqlite3"
    audit_db = tmp_path / "api_audit.sqlite3"
    approvals_db = tmp_path / "api_approvals.sqlite3"
    domain_db = tmp_path / "api_domain.sqlite3"

    auth_repo = AuthRepository(auth_db)
    audit = AuditTrail(audit_db)
    workflow = ApprovalWorkflow(approvals_db, audit)
    domain_repo = DomainRepository(domain_db)

    config = AuthConfig(
        secret_key=TEST_SECRET,
        algorithm="HS256",
        access_token_expire_minutes=60,
        auth_db_path=auth_db,
        domain_db_path=domain_db,
    )

    main.app.dependency_overrides[get_auth_config] = lambda: config
    main.app.dependency_overrides[get_auth_repository] = lambda: auth_repo
    main.app.dependency_overrides[get_domain_repository] = lambda: domain_repo
    main.app.dependency_overrides[main.get_audit_trail] = lambda: audit
    main.app.dependency_overrides[main.get_approval_workflow] = lambda: workflow

    # Properties
    prop_a = auth_repo.create_property(name="Sunset Ridge", property_id="prop-sunset")
    prop_b = auth_repo.create_property(name="Ocean Breeze", property_id="prop-ocean")

    # Users
    pwd_hash = hash_password("SecurePass123!")
    manager = auth_repo.create_user(email="manager@example.com", password_hash=pwd_hash)
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=manager.user_id,
        role=UserRole.PROPERTY_MANAGER,
    )

    accountant = auth_repo.create_user(email="accountant@example.com", password_hash=pwd_hash)
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=accountant.user_id,
        role=UserRole.ACCOUNTANT,
    )

    resident_user = auth_repo.create_user(email="tenant@example.com", password_hash=pwd_hash)
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=resident_user.user_id,
        role=UserRole.RESIDENT,
    )

    super_user = auth_repo.create_user(
        email="admin@example.com", password_hash=pwd_hash, is_superuser=True
    )

    client = TestClient(main.app)

    try:
        yield {
            "client": client,
            "auth_repo": auth_repo,
            "domain_repo": domain_repo,
            "audit": audit,
            "workflow": workflow,
            "prop_a": prop_a,
            "prop_b": prop_b,
            "manager": manager,
            "accountant": accountant,
            "resident_user": resident_user,
            "super_user": super_user,
        }
    finally:
        main.app.dependency_overrides.clear()


def make_auth_headers(user_id: str, email: str) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        email=email,
        secret_key=TEST_SECRET,
        expires_delta=timedelta(minutes=60),
    )
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# 1. Domain Repository: Entity CRUD & Lifecycle Tests
# =============================================================================


def test_entity_crud_lifecycle_all_entities(repo_fixture):
    repo, _ = repo_fixture

    # 1. Property
    prop = repo.create_property(name="Grand Oaks", property_id="prop-1", address="123 Oak St")
    assert prop.property_id == "prop-1"
    assert prop.name == "Grand Oaks"
    assert prop.address == "123 Oak St"

    prop_updated = repo.update_property("prop-1", PropertyUpdate(name="Grand Oaks Luxury"))
    assert prop_updated.name == "Grand Oaks Luxury"
    assert prop_updated.address == "123 Oak St"

    # 2. Unit
    unit = repo.create_unit(
        "prop-1",
        UnitCreate(unit_number="101", status=UnitStatus.VACANT, rent_amount=Decimal("1500.00")),
    )
    assert unit.unit_id is not None
    assert unit.unit_number == "101"
    assert unit.rent_amount == Decimal("1500.00")

    unit_up = repo.update_unit(
        "prop-1", unit.unit_id, UnitUpdate(status=UnitStatus.OCCUPIED, rent_amount=Decimal("1550.50"))
    )
    assert unit_up.status == UnitStatus.OCCUPIED
    assert unit_up.rent_amount == Decimal("1550.50")

    units_list = repo.list_units("prop-1")
    assert len(units_list) == 1
    assert units_list[0].unit_id == unit.unit_id

    # 3. Resident
    res = repo.create_resident(
        "prop-1",
        ResidentCreate(
            first_name="Jane",
            last_name="Doe",
            email="jane.doe@example.com",
            phone="555-0100",
            status=ResidentStatus.ACTIVE,
        ),
    )
    assert res.resident_id is not None
    assert res.first_name == "Jane"
    assert res.email == "jane.doe@example.com"

    res_up = repo.update_resident("prop-1", res.resident_id, ResidentUpdate(phone="555-0199"))
    assert res_up.phone == "555-0199"

    # 4. Vendor
    vendor = repo.create_vendor(
        "prop-1",
        VendorCreate(
            name="Apex Plumbing",
            category=VendorCategory.PLUMBING,
            email="info@apexplumbing.com",
            phone="555-0200",
        ),
    )
    assert vendor.vendor_id is not None
    assert vendor.name == "Apex Plumbing"
    assert vendor.category == VendorCategory.PLUMBING

    vendor_up = repo.update_vendor("prop-1", vendor.vendor_id, VendorUpdate(is_active=False))
    assert vendor_up.is_active is False

    # 5. Document Metadata
    doc = repo.create_document_metadata(
        "prop-1",
        DocumentMetadataCreate(
            filename="lease_agreement.pdf",
            file_type="application/pdf",
            storage_path="documents/prop-1/lease_agreement.pdf",
            file_size_bytes=1048576,
            sha256_checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            associated_entity_type="unit",
            associated_entity_id=unit.unit_id,
        ),
    )
    assert doc.document_id is not None
    assert doc.filename == "lease_agreement.pdf"
    assert doc.file_size_bytes == 1048576

    # 6. Lease
    lease = repo.create_lease(
        "prop-1",
        LeaseCreate(
            unit_id=unit.unit_id,
            resident_id=res.resident_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            rent_amount=Decimal("1550.50"),
            deposit_amount=Decimal("1550.50"),
            status=LeaseStatus.ACTIVE,
        ),
    )
    assert lease.lease_id is not None
    assert lease.rent_amount == Decimal("1550.50")
    assert lease.deposit_amount == Decimal("1550.50")

    lease_up = repo.update_lease("prop-1", lease.lease_id, LeaseUpdate(status=LeaseStatus.TERMINATED))
    assert lease_up.status == LeaseStatus.TERMINATED

    # 7. Payment
    payment = repo.create_payment(
        "prop-1",
        PaymentCreate(
            amount=Decimal("1550.50"),
            currency="USD",
            payment_date=date(2026, 2, 1),
            payment_method=PaymentMethod.ACH,
            status=PaymentStatus.SETTLED,
            resident_id=res.resident_id,
            lease_id=lease.lease_id,
            reference_number="ACH-889911",
            document_id=doc.document_id,
        ),
    )
    assert payment.payment_id is not None
    assert payment.amount == Decimal("1550.50")
    assert payment.reference_number == "ACH-889911"

    payment_up = repo.update_payment(
        "prop-1", payment.payment_id, PaymentUpdate(status=PaymentStatus.RECONCILED)
    )
    assert payment_up.status == PaymentStatus.RECONCILED

    # 8. Expense
    expense = repo.create_expense(
        "prop-1",
        ExpenseCreate(
            category=ExpenseCategory.MAINTENANCE,
            amount=Decimal("350.25"),
            currency="USD",
            expense_date=date(2026, 2, 5),
            status=ExpenseStatus.PAID,
            description="Fixed emergency pipe burst",
            vendor_id=vendor.vendor_id,
            unit_id=unit.unit_id,
            document_id=doc.document_id,
        ),
    )
    assert expense.expense_id is not None
    assert expense.amount == Decimal("350.25")
    assert expense.description == "Fixed emergency pipe burst"

    expense_up = repo.update_expense("prop-1", expense.expense_id, ExpenseUpdate(status=ExpenseStatus.RECONCILED))
    assert expense_up.status == ExpenseStatus.RECONCILED

    # 9. Maintenance Work Order
    wo = repo.create_work_order(
        "prop-1",
        WorkOrderCreate(
            unit_id=unit.unit_id,
            resident_id=res.resident_id,
            vendor_id=vendor.vendor_id,
            title="Leaking Faucet",
            description="Bathroom sink is steadily dripping",
            priority=WorkOrderPriority.MEDIUM,
            status=WorkOrderStatus.ASSIGNED,
            estimated_cost=Decimal("120.00"),
        ),
    )
    assert wo.work_order_id is not None
    assert wo.title == "Leaking Faucet"
    assert wo.estimated_cost == Decimal("120.00")

    repo.update_work_order(
        "prop-1",
        wo.work_order_id,
        WorkOrderUpdate(status=WorkOrderStatus.IN_PROGRESS),
    )
    wo_up = repo.update_work_order(
        "prop-1",
        wo.work_order_id,
        WorkOrderUpdate(status=WorkOrderStatus.COMPLETED, actual_cost=Decimal("115.50")),
    )
    assert wo_up.status == WorkOrderStatus.COMPLETED
    assert wo_up.actual_cost == Decimal("115.50")


# =============================================================================
# 2. Decimal-Safe Monetary Precision Tests
# =============================================================================


def test_decimal_safe_monetary_precision(repo_fixture):
    repo, _ = repo_fixture
    repo.create_property(name="Precision Towers", property_id="prop-prec")

    # High precision cents test
    unit = repo.create_unit(
        "prop-prec",
        UnitCreate(unit_number="909", rent_amount=Decimal("1234.56")),
    )
    retrieved_unit = repo.get_unit("prop-prec", unit.unit_id)
    assert retrieved_unit.rent_amount == Decimal("1234.56")
    assert isinstance(retrieved_unit.rent_amount, Decimal)

    res = repo.create_resident(
        "prop-prec",
        ResidentCreate(first_name="Alice", last_name="Smith", email="alice@example.com"),
    )

    lease = repo.create_lease(
        "prop-prec",
        LeaseCreate(
            unit_id=unit.unit_id,
            resident_id=res.resident_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            rent_amount=Decimal("9999.99"),
            deposit_amount=Decimal("0.05"),
        ),
    )
    assert lease.rent_amount == Decimal("9999.99")
    assert lease.deposit_amount == Decimal("0.05")

    # Test payment and expense amounts
    pmt = repo.create_payment(
        "prop-prec",
        PaymentCreate(
            amount=Decimal("1000000.01"),
            currency="USD",
            payment_date=date(2026, 1, 15),
            payment_method=PaymentMethod.WIRE,
        ),
    )
    assert pmt.amount == Decimal("1000000.01")
    assert isinstance(pmt.amount, Decimal)

    exp = repo.create_expense(
        "prop-prec",
        ExpenseCreate(
            category=ExpenseCategory.UTILITIES,
            amount=Decimal("456.78"),
            currency="USD",
            expense_date=date(2026, 1, 16),
            description="Water bill",
        ),
    )
    assert exp.amount == Decimal("456.78")


# =============================================================================
# 3. SQLite Foreign-Key Enforcement Tests
# =============================================================================


def test_sqlite_foreign_key_enforcement(repo_fixture):
    repo, db_path = repo_fixture

    # Verify PRAGMA foreign_keys = ON is active
    conn = repo._connect()
    fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.close()
    assert fk_status == 1

    # Attempt inserting unit with nonexistent property_id directly in SQL
    raw_conn = sqlite3.connect(db_path)
    raw_conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        raw_conn.execute(
            """
            INSERT INTO units (unit_id, property_id, unit_number, status, created_at, updated_at)
            VALUES ('u-fail', 'nonexistent-prop', '999', 'vacant', '2026-01-01', '2026-01-01')
            """
        )
    raw_conn.close()

    # Via repository API
    with pytest.raises(EntityNotFoundError):
        repo.create_unit(
            "nonexistent-prop",
            UnitCreate(unit_number="999", rent_amount=Decimal("1000.00")),
        )


# =============================================================================
# 4. Cross-Property Integrity Rejection Tests
# =============================================================================


def test_cross_property_references_rejection(repo_fixture):
    repo, _ = repo_fixture

    prop_a = repo.create_property(name="Property Alpha", property_id="prop-a")
    prop_b = repo.create_property(name="Property Beta", property_id="prop-b")

    unit_a = repo.create_unit("prop-a", UnitCreate(unit_number="A-101"))
    unit_b = repo.create_unit("prop-b", UnitCreate(unit_number="B-101"))

    res_a = repo.create_resident(
        "prop-a", ResidentCreate(first_name="John", last_name="Alpha", email="ja@example.com")
    )
    res_b = repo.create_resident(
        "prop-b", ResidentCreate(first_name="Jane", last_name="Beta", email="jb@example.com")
    )

    vendor_a = repo.create_vendor("prop-a", VendorCreate(name="Vendor A", category=VendorCategory.HVAC))
    vendor_b = repo.create_vendor("prop-b", VendorCreate(name="Vendor B", category=VendorCategory.ELECTRICAL))

    doc_a = repo.create_document_metadata(
        "prop-a",
        DocumentMetadataCreate(
            filename="a.pdf",
            file_type="application/pdf",
            storage_path="a.pdf",
            file_size_bytes=100,
            sha256_checksum="a" * 64,
        ),
    )
    doc_b = repo.create_document_metadata(
        "prop-b",
        DocumentMetadataCreate(
            filename="b.pdf",
            file_type="application/pdf",
            storage_path="b.pdf",
            file_size_bytes=100,
            sha256_checksum="b" * 64,
        ),
    )

    # 1. Lease with resident from Property B and unit from Property A
    with pytest.raises(CrossPropertyReferenceError) as exc_info:
        repo.create_lease(
            "prop-a",
            LeaseCreate(
                unit_id=unit_a.unit_id,
                resident_id=res_b.resident_id,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
                rent_amount=Decimal("1000.00"),
            ),
        )
    assert "belongs to property 'prop-b', not 'prop-a'" in str(exc_info.value)

    # 2. Lease with unit from Property B
    with pytest.raises(CrossPropertyReferenceError):
        repo.create_lease(
            "prop-a",
            LeaseCreate(
                unit_id=unit_b.unit_id,
                resident_id=res_a.resident_id,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
                rent_amount=Decimal("1000.00"),
            ),
        )

    # 3. Payment in Property A referencing resident in Property B
    with pytest.raises(CrossPropertyReferenceError):
        repo.create_payment(
            "prop-a",
            PaymentCreate(
                amount=Decimal("500.00"),
                currency="USD",
                payment_date=date(2026, 1, 1),
                payment_method=PaymentMethod.CHECK,
                resident_id=res_b.resident_id,
            ),
        )

    # 4. Payment in Property A referencing document in Property B
    with pytest.raises(CrossPropertyReferenceError):
        repo.create_payment(
            "prop-a",
            PaymentCreate(
                amount=Decimal("500.00"),
                currency="USD",
                payment_date=date(2026, 1, 1),
                payment_method=PaymentMethod.CHECK,
                document_id=doc_b.document_id,
            ),
        )

    # 5. Expense in Property A referencing vendor in Property B
    with pytest.raises(CrossPropertyReferenceError):
        repo.create_expense(
            "prop-a",
            ExpenseCreate(
                category=ExpenseCategory.REPAIRS,
                amount=Decimal("150.00"),
                currency="USD",
                expense_date=date(2026, 1, 1),
                description="Cross vendor repair",
                vendor_id=vendor_b.vendor_id,
            ),
        )

    # 6. Work order in Property A referencing unit in Property B
    with pytest.raises(CrossPropertyReferenceError):
        repo.create_work_order(
            "prop-a",
            WorkOrderCreate(
                unit_id=unit_b.unit_id,
                title="Cross-property repair",
                description="Illegal work order",
            ),
        )

    # 7. Work order in Property A referencing vendor in Property B
    with pytest.raises(CrossPropertyReferenceError):
        repo.create_work_order(
            "prop-a",
            WorkOrderCreate(
                unit_id=unit_a.unit_id,
                vendor_id=vendor_b.vendor_id,
                title="Cross vendor work order",
                description="Illegal vendor assignment",
            ),
        )


# =============================================================================
# 5. Tenant Property Isolation Tests
# =============================================================================


def test_property_tenant_isolation(repo_fixture):
    repo, _ = repo_fixture

    repo.create_property(name="North Property", property_id="prop-north")
    repo.create_property(name="South Property", property_id="prop-south")

    unit_n = repo.create_unit("prop-north", UnitCreate(unit_number="N-1"))
    unit_s = repo.create_unit("prop-south", UnitCreate(unit_number="S-1"))

    # Attempting to read North's unit under South's context returns None
    assert repo.get_unit("prop-south", unit_n.unit_id) is None
    assert repo.get_unit("prop-north", unit_n.unit_id) is not None

    # Updating North's unit under South's context raises EntityNotFoundError
    with pytest.raises(EntityNotFoundError):
        repo.update_unit("prop-south", unit_n.unit_id, UnitUpdate(status=UnitStatus.MAINTENANCE))

    # Listing units under North only returns North's unit
    north_units = repo.list_units("prop-north")
    assert len(north_units) == 1
    assert north_units[0].unit_id == unit_n.unit_id

    south_units = repo.list_units("prop-south")
    assert len(south_units) == 1
    assert south_units[0].unit_id == unit_s.unit_id


# =============================================================================
# 6. Uniqueness and Constraint Violation Tests
# =============================================================================


def test_uniqueness_and_constraint_violations(repo_fixture):
    repo, _ = repo_fixture

    repo.create_property(name="Downtown Lofts", property_id="prop-dt")
    repo.create_property(name="Uptown Lofts", property_id="prop-ut")

    # Creating unit 101 in Downtown
    repo.create_unit("prop-dt", UnitCreate(unit_number="101"))

    # Creating duplicate unit 101 in Downtown must fail
    with pytest.raises(IntegrityConflictError):
        repo.create_unit("prop-dt", UnitCreate(unit_number="101"))

    # Creating unit 101 in Uptown must succeed (scoped per-property)
    unit_ut = repo.create_unit("prop-ut", UnitCreate(unit_number="101"))
    assert unit_ut.unit_number == "101"

    # Creating duplicate property ID must fail
    with pytest.raises(IntegrityConflictError):
        repo.create_property(name="Duplicate", property_id="prop-dt")


# =============================================================================
# 7. Atomic Multi-Record Transactions & Rollback
# =============================================================================


def test_atomic_transaction_commit_and_rollback(repo_fixture):
    repo, db_path = repo_fixture
    repo.create_property(name="Transaction Property", property_id="prop-tx")

    # 1. Successful atomic transaction
    with repo.transaction() as conn:
        conn.execute(
            """
            INSERT INTO units (unit_id, property_id, unit_number, status, created_at, updated_at)
            VALUES ('u-tx-1', 'prop-tx', 'TX-1', 'vacant', '2026-01-01', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO units (unit_id, property_id, unit_number, status, created_at, updated_at)
            VALUES ('u-tx-2', 'prop-tx', 'TX-2', 'vacant', '2026-01-01', '2026-01-01')
            """
        )

    assert repo.get_unit("prop-tx", "u-tx-1") is not None
    assert repo.get_unit("prop-tx", "u-tx-2") is not None

    # 2. Failed atomic transaction rolled back completely
    try:
        with repo.transaction() as conn:
            conn.execute(
                """
                INSERT INTO units (unit_id, property_id, unit_number, status, created_at, updated_at)
                VALUES ('u-tx-3', 'prop-tx', 'TX-3', 'vacant', '2026-01-01', '2026-01-01')
                """
            )
            # Intentionally cause a foreign key violation
            conn.execute(
                """
                INSERT INTO units (unit_id, property_id, unit_number, status, created_at, updated_at)
                VALUES ('u-tx-4', 'invalid-property-id', 'TX-4', 'vacant', '2026-01-01', '2026-01-01')
                """
            )
    except sqlite3.IntegrityError:
        pass

    # u-tx-3 must NOT exist because the transaction rolled back
    assert repo.get_unit("prop-tx", "u-tx-3") is None


# =============================================================================
# 8. Persistence across Process Reopen
# =============================================================================


def test_persistence_across_process_reopen(tmp_path: Path):
    db_file = tmp_path / "persistent_domain.sqlite3"

    # Process 1: instantiate repo and write data
    repo1 = DomainRepository(db_file)
    prop = repo1.create_property(name="Persistent Estates", property_id="prop-pers")
    unit = repo1.create_unit("prop-pers", UnitCreate(unit_number="P-100", rent_amount=Decimal("2000.00")))
    del repo1

    # Process 2: reopen database with a fresh repository instance
    repo2 = DomainRepository(db_file)
    persisted_prop = repo2.get_property("prop-pers")
    assert persisted_prop is not None
    assert persisted_prop.name == "Persistent Estates"

    persisted_unit = repo2.get_unit("prop-pers", unit.unit_id)
    assert persisted_unit is not None
    assert persisted_unit.unit_number == "P-100"
    assert persisted_unit.rent_amount == Decimal("2000.00")


# =============================================================================
# 9. Coexistence with Phase 1 Auth, Approvals, and Audit
# =============================================================================


def test_phase1_and_phase2_coexistence(api_setup):
    client = api_setup["client"]
    manager = api_setup["manager"]
    prop_a = api_setup["prop_a"]
    audit = api_setup["audit"]
    workflow = api_setup["workflow"]

    headers = make_auth_headers(manager.user_id, manager.email)

    # 1. Audit trail functionality remains intact
    audit.append(
        AuditEventCreate(
            actor_id=manager.user_id,
            actor_type="user",
            event_type="domain.unit_created",
            entity_type="unit",
            entity_id="unit-test",
            property_id=prop_a.property_id,
        )
    )
    verification = audit.verify_chain()
    assert verification.valid is True

    # 2. Approval workflow functionality remains intact
    req = workflow.create_request(
        ApprovalRequestCreate(
            action_id="act-domain-1",
            action_type="propose_expense",
            requested_by="analyst",
            approver_ids=[manager.user_id],
            rationale="Coexistence verification",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            property_id=prop_a.property_id,
        )
    )
    assert req.approval_id is not None

    # 3. Domain API works concurrently with same tokens
    res_unit = client.post(
        f"/api/v1/properties/{prop_a.property_id}/units",
        headers=headers,
        json={"unit_number": "COEXIST-1", "status": "vacant", "rent_amount": "1200.00"},
    )
    assert res_unit.status_code == 201
    assert res_unit.json()["unit_number"] == "COEXIST-1"


# =============================================================================
# 10. Property-Scoped CRUD API & Permission Enforcement Tests
# =============================================================================


def test_api_property_scoped_crud_and_permissions(api_setup):
    client = api_setup["client"]
    prop_a = api_setup["prop_a"]
    prop_b = api_setup["prop_b"]
    manager = api_setup["manager"]
    accountant = api_setup["accountant"]
    resident_user = api_setup["resident_user"]

    mgr_headers = make_auth_headers(manager.user_id, manager.email)
    acct_headers = make_auth_headers(accountant.user_id, accountant.email)
    res_headers = make_auth_headers(resident_user.user_id, resident_user.email)

    # 1. Manager creates a unit on Property A -> 201
    res_u = client.post(
        f"/api/v1/properties/{prop_a.property_id}/units",
        headers=mgr_headers,
        json={"unit_number": "MGR-101", "status": "vacant", "rent_amount": "1450.00"},
    )
    assert res_u.status_code == 201
    unit_id = res_u.json()["unit_id"]

    # 2. Accountant attempts to create a unit -> 403 Forbidden (lacks PROPERTY_MANAGE)
    res_acct_u = client.post(
        f"/api/v1/properties/{prop_a.property_id}/units",
        headers=acct_headers,
        json={"unit_number": "ACCT-102", "status": "vacant"},
    )
    assert res_acct_u.status_code == 403

    # 3. Manager creates a resident on Property A -> 201
    res_r = client.post(
        f"/api/v1/properties/{prop_a.property_id}/residents",
        headers=mgr_headers,
        json={"first_name": "Carol", "last_name": "Danvers", "email": "carol@example.com"},
    )
    assert res_r.status_code == 201
    resident_id = res_r.json()["resident_id"]

    # 4. Resident can list residents -> 200 OK (has RESIDENT_READ)
    res_list_r = client.get(
        f"/api/v1/properties/{prop_a.property_id}/residents",
        headers=res_headers,
    )
    assert res_list_r.status_code == 200
    assert len(res_list_r.json()) >= 1

    # 5. Resident attempts to create a lease -> 403 Forbidden (lacks PROPERTY_MANAGE)
    res_lease_fail = client.post(
        f"/api/v1/properties/{prop_a.property_id}/leases",
        headers=res_headers,
        json={
            "unit_id": unit_id,
            "resident_id": resident_id,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "rent_amount": "1450.00",
        },
    )
    assert res_lease_fail.status_code == 403

    # 6. Manager creates lease -> 201
    res_lease = client.post(
        f"/api/v1/properties/{prop_a.property_id}/leases",
        headers=mgr_headers,
        json={
            "unit_id": unit_id,
            "resident_id": resident_id,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "rent_amount": "1450.00",
        },
    )
    assert res_lease.status_code == 201
    lease_id = res_lease.json()["lease_id"]

    # 7. Accountant creates payment -> 201 (has FINANCIAL_OPERATIONS)
    res_pmt = client.post(
        f"/api/v1/properties/{prop_a.property_id}/payments",
        headers=acct_headers,
        json={
            "amount": "1450.00",
            "currency": "USD",
            "payment_date": "2026-01-05",
            "payment_method": "ach",
            "resident_id": resident_id,
            "lease_id": lease_id,
        },
    )
    assert res_pmt.status_code == 201
    payment_id = res_pmt.json()["payment_id"]

    # 8. Resident attempts to view payments -> 403 Forbidden (lacks FINANCIAL_OPERATIONS)
    res_pmt_view = client.get(
        f"/api/v1/properties/{prop_a.property_id}/payments",
        headers=res_headers,
    )
    assert res_pmt_view.status_code == 403

    # 9. Cross-property authorization rejection:
    # Manager for Property A attempts to access Property B -> 403 Forbidden
    res_cross = client.get(
        f"/api/v1/properties/{prop_b.property_id}/units",
        headers=mgr_headers,
    )
    assert res_cross.status_code == 403
    assert "not authorized" in res_cross.json()["detail"].lower()

    # 10. Manager creates maintenance work order -> 201
    res_wo = client.post(
        f"/api/v1/properties/{prop_a.property_id}/work-orders",
        headers=mgr_headers,
        json={
            "unit_id": unit_id,
            "resident_id": resident_id,
            "title": "Fix lock",
            "description": "Front door deadbolt is sticking",
            "priority": "medium",
        },
    )
    assert res_wo.status_code == 201
    wo_id = res_wo.json()["work_order_id"]

    # Retrieve work order -> 200
    res_wo_get = client.get(
        f"/api/v1/properties/{prop_a.property_id}/work-orders/{wo_id}",
        headers=mgr_headers,
    )
    assert res_wo_get.status_code == 200
    assert res_wo_get.json()["title"] == "Fix lock"


def test_work_order_lifecycle_transitions(repo_fixture):
    repo, _ = repo_fixture
    repo.create_property(name="Transition Manor", property_id="prop-trans")
    unit = repo.create_unit("prop-trans", UnitCreate(unit_number="101"))

    # 1. Create work order with OPEN status
    wo = repo.create_work_order(
        "prop-trans",
        WorkOrderCreate(
            unit_id=unit.unit_id,
            title="HVAC repair",
            description="AC not blowing cold",
            priority=WorkOrderPriority.MEDIUM,
            status=WorkOrderStatus.OPEN,
        ),
    )
    assert wo.status == WorkOrderStatus.OPEN

    # 2. Invalid transition: OPEN -> COMPLETED is rejected
    with pytest.raises(InvalidOperationError) as exc_info:
        repo.update_work_order(
            "prop-trans",
            wo.work_order_id,
            WorkOrderUpdate(status=WorkOrderStatus.COMPLETED),
        )
    assert "Invalid work-order status transition" in str(exc_info.value)
    # Verify original status is unchanged
    unchanged_wo = repo.get_work_order("prop-trans", wo.work_order_id)
    assert unchanged_wo.status == WorkOrderStatus.OPEN

    # 3. Valid transition: OPEN -> ASSIGNED
    assigned_wo = repo.update_work_order(
        "prop-trans",
        wo.work_order_id,
        WorkOrderUpdate(status=WorkOrderStatus.ASSIGNED),
    )
    assert assigned_wo.status == WorkOrderStatus.ASSIGNED

    # 4. Invalid transition: ASSIGNED -> COMPLETED is rejected
    with pytest.raises(InvalidOperationError):
        repo.update_work_order(
            "prop-trans",
            wo.work_order_id,
            WorkOrderUpdate(status=WorkOrderStatus.COMPLETED),
        )
    assert repo.get_work_order("prop-trans", wo.work_order_id).status == WorkOrderStatus.ASSIGNED

    # 5. Valid transition: ASSIGNED -> IN_PROGRESS
    in_prog_wo = repo.update_work_order(
        "prop-trans",
        wo.work_order_id,
        WorkOrderUpdate(status=WorkOrderStatus.IN_PROGRESS),
    )
    assert in_prog_wo.status == WorkOrderStatus.IN_PROGRESS

    # 6. Valid transition: IN_PROGRESS -> COMPLETED
    completed_wo = repo.update_work_order(
        "prop-trans",
        wo.work_order_id,
        WorkOrderUpdate(status=WorkOrderStatus.COMPLETED),
    )
    assert completed_wo.status == WorkOrderStatus.COMPLETED

    # 7. Invalid transition: COMPLETED -> OPEN is rejected
    with pytest.raises(InvalidOperationError):
        repo.update_work_order(
            "prop-trans",
            wo.work_order_id,
            WorkOrderUpdate(status=WorkOrderStatus.OPEN),
        )
    assert repo.get_work_order("prop-trans", wo.work_order_id).status == WorkOrderStatus.COMPLETED

    # 8. Cancellation transitions: OPEN -> CANCELLED
    wo_cancel = repo.create_work_order(
        "prop-trans",
        WorkOrderCreate(
            unit_id=unit.unit_id,
            title="Cancelled job",
            description="No longer needed",
            priority=WorkOrderPriority.LOW,
            status=WorkOrderStatus.OPEN,
        ),
    )
    cancelled_wo = repo.update_work_order(
        "prop-trans",
        wo_cancel.work_order_id,
        WorkOrderUpdate(status=WorkOrderStatus.CANCELLED),
    )
    assert cancelled_wo.status == WorkOrderStatus.CANCELLED

    # Invalid transition: CANCELLED -> IN_PROGRESS is rejected
    with pytest.raises(InvalidOperationError):
        repo.update_work_order(
            "prop-trans",
            wo_cancel.work_order_id,
            WorkOrderUpdate(status=WorkOrderStatus.IN_PROGRESS),
        )
    assert repo.get_work_order("prop-trans", wo_cancel.work_order_id).status == WorkOrderStatus.CANCELLED

    # Invalid transitions from CANCELLED (terminal state)
    for invalid_target in [WorkOrderStatus.OPEN, WorkOrderStatus.ASSIGNED, WorkOrderStatus.COMPLETED]:
        with pytest.raises(InvalidOperationError):
            repo.update_work_order(
                "prop-trans",
                wo_cancel.work_order_id,
                WorkOrderUpdate(status=invalid_target),
            )
        assert repo.get_work_order("prop-trans", wo_cancel.work_order_id).status == WorkOrderStatus.CANCELLED

    # 9. Valid cancellation from ASSIGNED: OPEN -> ASSIGNED -> CANCELLED
    wo_assigned = repo.create_work_order(
        "prop-trans",
        WorkOrderCreate(
            unit_id=unit.unit_id,
            title="Assigned to cancel",
            description="Testing cancellation from assigned",
            priority=WorkOrderPriority.MEDIUM,
        ),
    )
    repo.update_work_order("prop-trans", wo_assigned.work_order_id, WorkOrderUpdate(status=WorkOrderStatus.ASSIGNED))
    res_cancel_assigned = repo.update_work_order("prop-trans", wo_assigned.work_order_id, WorkOrderUpdate(status=WorkOrderStatus.CANCELLED))
    assert res_cancel_assigned.status == WorkOrderStatus.CANCELLED
    assert repo.get_work_order("prop-trans", wo_assigned.work_order_id).status == WorkOrderStatus.CANCELLED

    # 10. Valid cancellation from IN_PROGRESS: OPEN -> ASSIGNED -> IN_PROGRESS -> CANCELLED
    wo_inp = repo.create_work_order(
        "prop-trans",
        WorkOrderCreate(
            unit_id=unit.unit_id,
            title="In progress to cancel",
            description="Testing cancellation from in progress",
            priority=WorkOrderPriority.HIGH,
        ),
    )
    repo.update_work_order("prop-trans", wo_inp.work_order_id, WorkOrderUpdate(status=WorkOrderStatus.ASSIGNED))
    repo.update_work_order("prop-trans", wo_inp.work_order_id, WorkOrderUpdate(status=WorkOrderStatus.IN_PROGRESS))
    res_cancel_inp = repo.update_work_order("prop-trans", wo_inp.work_order_id, WorkOrderUpdate(status=WorkOrderStatus.CANCELLED))
    assert res_cancel_inp.status == WorkOrderStatus.CANCELLED
    assert repo.get_work_order("prop-trans", wo_inp.work_order_id).status == WorkOrderStatus.CANCELLED

    # 11. Invalid transitions from COMPLETED (terminal state)
    for invalid_target in [WorkOrderStatus.OPEN, WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS, WorkOrderStatus.CANCELLED]:
        with pytest.raises(InvalidOperationError):
            repo.update_work_order(
                "prop-trans",
                wo.work_order_id,
                WorkOrderUpdate(status=invalid_target),
            )
        assert repo.get_work_order("prop-trans", wo.work_order_id).status == WorkOrderStatus.COMPLETED

