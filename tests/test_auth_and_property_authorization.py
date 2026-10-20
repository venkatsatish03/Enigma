"""Comprehensive tests for authentication and property-level authorization."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

import main
from schemas.approvals import ApprovalRequestCreate
from schemas.audit import AuditEventCreate
from schemas.auth import Permission, UserRole
from tools.approval_workflow import ApprovalWorkflow
from tools.audit_trail import AuditTrail
from tools.auth_config import AuthConfig
from tools.auth_crypto import create_access_token, hash_password
from tools.auth_dependencies import (
    get_auth_config,
    get_auth_repository,
)
from tools.auth_repository import AuthRepository

TEST_SECRET = "test-secret-at-least-32-chars-long-1234567890"


@pytest.fixture
def auth_setup(tmp_path):
    auth_db = tmp_path / "test_auth.sqlite3"
    audit_db = tmp_path / "test_audit.sqlite3"
    approvals_db = tmp_path / "test_approvals.sqlite3"

    auth_repo = AuthRepository(auth_db)
    audit = AuditTrail(audit_db)
    workflow = ApprovalWorkflow(approvals_db, audit)

    config = AuthConfig(
        secret_key=TEST_SECRET,
        algorithm="HS256",
        access_token_expire_minutes=60,
        auth_db_path=auth_db,
    )

    main.app.dependency_overrides[get_auth_config] = lambda: config
    main.app.dependency_overrides[get_auth_repository] = lambda: auth_repo
    main.app.dependency_overrides[main.get_audit_trail] = lambda: audit
    main.app.dependency_overrides[main.get_approval_workflow] = lambda: workflow

    # Create properties
    prop_a = auth_repo.create_property(name="Property Alpha", property_id="prop-alpha")
    prop_b = auth_repo.create_property(name="Property Beta", property_id="prop-beta")

    # Create users
    pwd_hash = hash_password("ValidPassword123!")

    # Superuser
    super_user = auth_repo.create_user(
        email="super@example.com",
        password_hash=pwd_hash,
        is_superuser=True,
    )

    # Manager for Property Alpha
    manager_a = auth_repo.create_user(
        email="manager.a@example.com",
        password_hash=pwd_hash,
    )
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=manager_a.user_id,
        role=UserRole.PROPERTY_MANAGER,
    )

    # Accountant for Property Alpha
    accountant_a = auth_repo.create_user(
        email="accountant.a@example.com",
        password_hash=pwd_hash,
    )
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=accountant_a.user_id,
        role=UserRole.ACCOUNTANT,
    )

    # Resident for Property Alpha
    resident_a = auth_repo.create_user(
        email="resident.a@example.com",
        password_hash=pwd_hash,
    )
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=resident_a.user_id,
        role=UserRole.RESIDENT,
    )

    # Disabled User
    disabled_user = auth_repo.create_user(
        email="disabled@example.com",
        password_hash=pwd_hash,
        is_active=False,
    )
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=disabled_user.user_id,
        role=UserRole.PROPERTY_MANAGER,
    )

    client = TestClient(main.app)

    try:
        yield {
            "client": client,
            "auth_repo": auth_repo,
            "audit": audit,
            "workflow": workflow,
            "config": config,
            "prop_a": prop_a,
            "prop_b": prop_b,
            "super_user": super_user,
            "manager_a": manager_a,
            "accountant_a": accountant_a,
            "resident_a": resident_a,
            "disabled_user": disabled_user,
        }
    finally:
        main.app.dependency_overrides.clear()


def make_token(user_id: str, email: str, secret: str = TEST_SECRET, delta=None) -> str:
    token, _ = create_access_token(
        user_id=user_id,
        email=email,
        secret_key=secret,
        expires_delta=delta or timedelta(minutes=60),
    )
    return token


def auth_header(user_id: str, email: str, secret: str = TEST_SECRET) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id, email, secret)}"}


# 1. Authentication Tests
def test_login_successful(auth_setup):
    client = auth_setup["client"]
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "manager.a@example.com", "password": "ValidPassword123!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0


def test_login_invalid_password_returns_generic_401(auth_setup):
    client = auth_setup["client"]
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "manager.a@example.com", "password": "WrongPassword"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid email or password."


def test_login_unknown_email_returns_identical_generic_401(auth_setup):
    client = auth_setup["client"]
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "AnyPassword123!"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid email or password."


def test_login_disabled_user_returns_403(auth_setup):
    client = auth_setup["client"]
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "disabled@example.com", "password": "ValidPassword123!"},
    )
    assert res.status_code == 403
    assert "disabled" in res.json()["detail"].lower()


def test_current_user_profile(auth_setup):
    client = auth_setup["client"]
    manager_a = auth_setup["manager_a"]
    headers = auth_header(manager_a.user_id, manager_a.email)

    res = client.get("/api/v1/auth/me", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == manager_a.user_id
    assert data["email"] == manager_a.email
    assert data["is_active"] is True
    assert len(data["memberships"]) == 1
    m = data["memberships"][0]
    assert m["property_id"] == "prop-alpha"
    assert m["property_name"] == "Property Alpha"
    assert m["role"] == "property_manager"
    assert "documents:analyze" in m["permissions"]


def test_token_validation_failures(auth_setup):
    client = auth_setup["client"]
    manager_a = auth_setup["manager_a"]

    # 1. Missing Authorization header
    res1 = client.get("/api/v1/auth/me")
    assert res1.status_code == 401

    # 2. Malformed token
    res2 = client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-valid-token"}
    )
    assert res2.status_code == 401

    # 3. Invalid signature
    bad_token = make_token(
        manager_a.user_id, manager_a.email, secret="wrong-secret-key-0000000000000000000000"
    )
    res3 = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_token}"}
    )
    assert res3.status_code == 401

    # 4. Expired token
    expired_token = make_token(
        manager_a.user_id, manager_a.email, delta=timedelta(seconds=-10)
    )
    res4 = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert res4.status_code == 401
    assert "expired" in res4.json()["detail"].lower()

    # 5. Token for disabled user
    disabled = auth_setup["disabled_user"]
    dis_token = make_token(disabled.user_id, disabled.email)
    res5 = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {dis_token}"}
    )
    assert res5.status_code == 403


# 2. Property-Level Authorization Tests
def test_property_authorization_assigned_vs_unassigned(auth_setup):
    client = auth_setup["client"]
    manager_a = auth_setup["manager_a"]
    headers = auth_header(manager_a.user_id, manager_a.email)

    # Authorized property Alpha
    res_a = client.get(
        "/api/v1/approvals",
        headers={**headers, "X-Property-ID": "prop-alpha"},
    )
    assert res_a.status_code == 200

    # Cross-property attempt on Property Beta (unassigned)
    res_b = client.get(
        "/api/v1/approvals",
        headers={**headers, "X-Property-ID": "prop-beta"},
    )
    assert res_b.status_code == 403
    assert "not authorized" in res_b.json()["detail"].lower()


def test_property_missing_or_nonexistent(auth_setup):
    client = auth_setup["client"]
    manager_a = auth_setup["manager_a"]
    headers = auth_header(manager_a.user_id, manager_a.email)

    # Missing property context
    res_missing = client.get("/api/v1/approvals", headers=headers)
    assert res_missing.status_code == 400

    # Non-existent property
    res_nonexistent = client.get(
        "/api/v1/approvals",
        headers={**headers, "X-Property-ID": "non-existent-prop"},
    )
    assert res_nonexistent.status_code == 404


def test_role_permissions_rejection(auth_setup):
    client = auth_setup["client"]
    resident_a = auth_setup["resident_a"]
    headers = {
        **auth_header(resident_a.user_id, resident_a.email),
        "X-Property-ID": "prop-alpha",
    }

    # Resident cannot list approvals
    res_app = client.get("/api/v1/approvals", headers=headers)
    assert res_app.status_code == 403
    assert "lacks required permission" in res_app.json()["detail"].lower()

    # Resident cannot run financial operations
    res_fin = client.post(
        "/api/v1/financial-operations",
        headers=headers,
        json={"document_result": {"status": "completed", "documents": []}},
    )
    assert res_fin.status_code == 403


def test_approval_decision_enforces_authenticated_identity(auth_setup):
    client = auth_setup["client"]
    workflow = auth_setup["workflow"]
    manager_a = auth_setup["manager_a"]
    prop_a = auth_setup["prop_a"]

    # Create approval request for prop-alpha with manager_a as approver
    req = workflow.create_request(
        ApprovalRequestCreate(
            action_id="act-auth-1",
            action_type="propose_payment",
            requested_by="analyst-system",
            approver_ids=[manager_a.user_id],
            rationale="Review invoice payment",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
            property_id=prop_a.property_id,
        )
    )

    headers = {
        **auth_header(manager_a.user_id, manager_a.email),
        "X-Property-ID": prop_a.property_id,
    }

    # 1. Attempting to supply conflicting approver_id (spoofing) is rejected with 403
    res_spoof = client.post(
        f"/api/v1/approvals/{req.approval_id}/decision",
        headers=headers,
        json={
            "approver_id": "different-user-id",
            "decision": "approve",
            "reason": "Legitimate reason",
        },
    )
    assert res_spoof.status_code == 403
    assert "does not match" in res_spoof.json()["detail"]

    # 2. Valid decision using authenticated identity
    res_valid = client.post(
        f"/api/v1/approvals/{req.approval_id}/decision",
        headers=headers,
        json={
            "decision": "approve",
            "reason": "Verified invoice and payment records",
        },
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["approval"]["status"] == "approved"
    assert res_valid.json()["approval"]["decided_by"] == manager_a.user_id


def test_cross_property_approval_decision_rejected(auth_setup):
    client = auth_setup["client"]
    workflow = auth_setup["workflow"]
    manager_a = auth_setup["manager_a"]
    prop_b = auth_setup["prop_b"]

    # Create approval in Property Beta
    req_b = workflow.create_request(
        ApprovalRequestCreate(
            action_id="act-b-1",
            action_type="propose_payment",
            requested_by="analyst-system",
            approver_ids=[manager_a.user_id],
            rationale="Review beta payment",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
            property_id=prop_b.property_id,
        )
    )

    # Manager A (only member of Alpha) tries to get/decide Beta approval
    headers_a = auth_header(manager_a.user_id, manager_a.email)

    res_get = client.get(
        f"/api/v1/approvals/{req_b.approval_id}",
        headers=headers_a,
    )
    assert res_get.status_code == 403

    res_decide = client.post(
        f"/api/v1/approvals/{req_b.approval_id}/decision",
        headers=headers_a,
        json={"decision": "approve", "reason": "Attempting beta decision"},
    )
    assert res_decide.status_code == 403


def test_property_scoped_audit_isolation(auth_setup):
    client = auth_setup["client"]
    audit = auth_setup["audit"]
    manager_a = auth_setup["manager_a"]

    # Append events for Prop Alpha and Prop Beta
    audit.append(
        AuditEventCreate(
            actor_id="agent-1",
            actor_type="agent",
            event_type="document.analyzed",
            entity_type="document",
            entity_id="doc-a-1",
            property_id="prop-alpha",
        )
    )
    audit.append(
        AuditEventCreate(
            actor_id="agent-2",
            actor_type="agent",
            event_type="document.analyzed",
            entity_type="document",
            entity_id="doc-b-1",
            property_id="prop-beta",
        )
    )

    headers_a = {
        **auth_header(manager_a.user_id, manager_a.email),
        "X-Property-ID": "prop-alpha",
    }

    res = client.get("/api/v1/audit/events", headers=headers_a)
    assert res.status_code == 200
    events = res.json()
    assert len(events) == 1
    assert events[0]["entity_id"] == "doc-a-1"
    assert events[0]["property_id"] == "prop-alpha"


def test_legacy_record_without_property_restricted_to_admin(auth_setup):
    client = auth_setup["client"]
    workflow = auth_setup["workflow"]
    manager_a = auth_setup["manager_a"]
    super_user = auth_setup["super_user"]

    # Create unassigned legacy approval (property_id=None)
    legacy = workflow.create_request(
        ApprovalRequestCreate(
            action_id="act-legacy",
            action_type="propose_payment",
            requested_by="analyst-system",
            approver_ids=[manager_a.user_id, super_user.user_id],
            rationale="Legacy approval",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
            property_id=None,
        )
    )

    # Regular property manager cannot view legacy record
    res_mgr = client.get(
        f"/api/v1/approvals/{legacy.approval_id}",
        headers=auth_header(manager_a.user_id, manager_a.email),
    )
    assert res_mgr.status_code == 403

    # Superuser can access legacy record
    res_super = client.get(
        f"/api/v1/approvals/{legacy.approval_id}",
        headers=auth_header(super_user.user_id, super_user.email),
    )
    assert res_super.status_code == 200
    assert res_super.json()["approval_id"] == legacy.approval_id


def test_audit_verify_requires_permission(auth_setup):
    client = auth_setup["client"]
    resident_a = auth_setup["resident_a"]
    manager_a = auth_setup["manager_a"]

    # Resident lacks audit:verify
    res_res = client.get(
        "/api/v1/audit/verify",
        headers={
            **auth_header(resident_a.user_id, resident_a.email),
            "X-Property-ID": "prop-alpha",
        },
    )
    assert res_res.status_code == 403

    # Manager has audit:verify
    res_mgr = client.get(
        "/api/v1/audit/verify",
        headers={
            **auth_header(manager_a.user_id, manager_a.email),
            "X-Property-ID": "prop-alpha",
        },
    )
    assert res_mgr.status_code == 200
    assert res_mgr.json()["valid"] is True


def test_auth_database_path_consistency():
    from tools.auth_config import PROJECT_ROOT, get_auth_config
    config = get_auth_config()
    expected_path = PROJECT_ROOT / "data" / "auth.sqlite3"
    assert config.auth_db_path.resolve() == expected_path.resolve()


def test_bootstrap_admin_and_reset_password(tmp_path):
    from tools.auth_bootstrap import bootstrap_admin
    from tools.auth_crypto import verify_password
    test_db = tmp_path / "bootstrap_test.sqlite3"
    repo = AuthRepository(test_db)

    # 1. Initial bootstrap
    res = bootstrap_admin(
        email="testadmin@example.com",
        password="InitialPassword123!",
        property_id="prop-boot",
        property_name="Bootstrap Property",
        auth_repo=repo,
    )
    assert res["user_created"] == "yes"
    assert res["password_updated"] == "no"

    user, pwd_hash = repo.get_user_by_email("testadmin@example.com")
    assert verify_password("InitialPassword123!", pwd_hash) is True
    assert verify_password("NewPassword456!", pwd_hash) is False

    # 2. Re-bootstrap without reset does not change password
    res_same = bootstrap_admin(
        email="testadmin@example.com",
        password="IgnoredPassword123!",
        auth_repo=repo,
    )
    assert res_same["user_created"] == "already existed"
    assert res_same["password_updated"] == "no"
    _, current_hash = repo.get_user_by_email("testadmin@example.com")
    assert verify_password("InitialPassword123!", current_hash) is True

    # 3. Bootstrap with reset_password=True safely updates password
    res_reset = bootstrap_admin(
        email="testadmin@example.com",
        password="NewPassword456!",
        reset_password=True,
        auth_repo=repo,
    )
    assert res_reset["user_created"] == "already existed"
    assert res_reset["password_updated"] == "yes"
    _, updated_hash = repo.get_user_by_email("testadmin@example.com")
    assert verify_password("NewPassword456!", updated_hash) is True
