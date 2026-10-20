"""Tests for Approval and Audit API endpoints with authentication and property authorization."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from schemas.approvals import ApprovalRequestCreate
from schemas.audit import AuditEventCreate
from schemas.auth import UserRole
from tools.approval_workflow import ApprovalWorkflow
from tools.audit_trail import AuditTrail
from tools.auth_config import AuthConfig
from tools.auth_crypto import create_access_token, hash_password
from tools.auth_dependencies import get_auth_config, get_auth_repository
from tools.auth_repository import AuthRepository

TEST_SECRET = "test-secret-at-least-32-chars-long-1234567890"


@pytest.fixture
def test_env(tmp_path):
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

    # Provision property and users
    prop = auth_repo.create_property(name="API Property", property_id="prop-api")
    pwd = hash_password("Secret123!")

    m1 = auth_repo.create_user(
        email="manager1@example.com",
        password_hash=pwd,
        user_id="manager-1",
    )
    m2 = auth_repo.create_user(
        email="manager2@example.com",
        password_hash=pwd,
        user_id="manager-2",
    )
    analyst = auth_repo.create_user(
        email="analyst1@example.com",
        password_hash=pwd,
        user_id="analyst-1",
    )
    unauth = auth_repo.create_user(
        email="unauth@example.com",
        password_hash=pwd,
        user_id="unauthorized-user",
    )

    for u in [m1, m2, analyst, unauth]:
        auth_repo.create_membership(
            property_id="prop-api",
            user_id=u.user_id,
            role=UserRole.PROPERTY_MANAGER,
        )

    main.app.dependency_overrides[get_auth_config] = lambda: config
    main.app.dependency_overrides[get_auth_repository] = lambda: auth_repo
    main.app.dependency_overrides[main.get_audit_trail] = lambda: audit
    main.app.dependency_overrides[main.get_approval_workflow] = lambda: workflow

    token_m1, _ = create_access_token(
        user_id="manager-1",
        email="manager1@example.com",
        secret_key=TEST_SECRET,
    )
    client = TestClient(main.app)
    client.headers["Authorization"] = f"Bearer {token_m1}"
    client.headers["X-Property-ID"] = "prop-api"

    try:
        yield {
            "client": client,
            "workflow": workflow,
            "audit": audit,
            "auth_repo": auth_repo,
            "config": config,
        }
    finally:
        main.app.dependency_overrides.clear()


def make_token(user_id: str, email: str) -> str:
    token, _ = create_access_token(
        user_id=user_id,
        email=email,
        secret_key=TEST_SECRET,
    )
    return token


def create_sample_request(
    workflow: ApprovalWorkflow,
    *,
    action_id: str = "act-api-001",
    action_type: str = "propose_payment",
    requested_by: str = "analyst-1",
    approver_ids: list[str] | None = None,
    amount: str = "500.00",
    currency: str = "USD",
    expires_in_hours: int = 2,
    correlation_id: str = "corr-api-001",
    property_id: str = "prop-api",
):
    request = ApprovalRequestCreate(
        action_id=action_id,
        action_type=action_type,
        requested_by=requested_by,
        approver_ids=approver_ids or ["manager-1", "manager-2"],
        rationale="Payment review required by policy.",
        amount=amount,
        currency=currency,
        evidence_ids=["inv-001", "bank-001"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_in_hours),
        correlation_id=correlation_id,
        property_id=property_id,
    )
    return workflow.create_request(request)


def test_list_approvals_empty(test_env):
    client = test_env["client"]
    response = client.get("/api/v1/approvals")
    assert response.status_code == 200
    assert response.json() == []


def test_get_approval_success(test_env):
    client = test_env["client"]
    workflow = test_env["workflow"]
    sample = create_sample_request(workflow)

    response = client.get(f"/api/v1/approvals/{sample.approval_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["approval_id"] == sample.approval_id
    assert data["action_id"] == "act-api-001"
    assert data["status"] == "pending"
    assert data["requested_by"] == "analyst-1"
    assert data["approver_ids"] == ["manager-1", "manager-2"]
    assert data["amount"] == "500.00"
    assert data["currency"] == "USD"
    assert data["correlation_id"] == "corr-api-001"
    assert data["property_id"] == "prop-api"


def test_get_approval_not_found_returns_404(test_env):
    client = test_env["client"]
    response = client.get("/api/v1/approvals/non-existent-id")
    assert response.status_code == 404
    assert "was not found" in response.json()["detail"]


def test_list_approvals_filtering_and_limit(test_env):
    client = test_env["client"]
    workflow = test_env["workflow"]
    req1 = create_sample_request(workflow, action_id="act-1", correlation_id="c-1")
    req2 = create_sample_request(workflow, action_id="act-2", correlation_id="c-2")
    req3 = create_sample_request(workflow, action_id="act-1", correlation_id="c-3")

    # List all
    res_all = client.get("/api/v1/approvals")
    assert res_all.status_code == 200
    assert len(res_all.json()) == 3

    # Filter by action_id
    res_action = client.get("/api/v1/approvals?action_id=act-1")
    assert res_action.status_code == 200
    items = res_action.json()
    assert len(items) == 2
    assert all(item["action_id"] == "act-1" for item in items)

    # Filter by status
    res_status = client.get("/api/v1/approvals?status=pending")
    assert res_status.status_code == 200
    assert len(res_status.json()) == 3

    # Limit
    res_limit = client.get("/api/v1/approvals?limit=1")
    assert res_limit.status_code == 200
    assert len(res_limit.json()) == 1

    # Invalid limit (less than 1)
    res_invalid_low = client.get("/api/v1/approvals?limit=0")
    assert res_invalid_low.status_code == 422

    # Invalid limit (greater than 10000)
    res_invalid_high = client.get("/api/v1/approvals?limit=20000")
    assert res_invalid_high.status_code == 422


def test_approve_request_success(test_env):
    client = test_env["client"]
    workflow = test_env["workflow"]
    audit = test_env["audit"]
    sample = create_sample_request(workflow)

    decision_payload = {
        "approver_id": "manager-1",
        "decision": "approve",
        "reason": "Verified invoice and bank reconciliation evidence.",
    }

    response = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        json=decision_payload,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Approval request approved."
    assert data["approval"]["status"] == "approved"
    assert data["approval"]["decided_by"] == "manager-1"
    assert data["approval"]["decision_reason"] == decision_payload["reason"]

    # Verify persistent state via GET
    get_res = client.get(f"/api/v1/approvals/{sample.approval_id}")
    assert get_res.status_code == 200
    assert get_res.json()["status"] == "approved"

    # Verify audit event emitted
    events = audit.list_events(entity_type="approval", entity_id=sample.approval_id)
    event_types = [e.event_type for e in events]
    assert "approval.requested" in event_types
    assert "approval.approved" in event_types


def test_reject_request_success(test_env):
    client = test_env["client"]
    workflow = test_env["workflow"]
    audit = test_env["audit"]
    sample = create_sample_request(workflow)

    token_m2 = make_token("manager-2", "manager2@example.com")
    decision_payload = {
        "decision": "reject",
        "reason": "Invoice amount does not match statement balance.",
    }

    response = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        headers={"Authorization": f"Bearer {token_m2}"},
        json=decision_payload,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Approval request rejected."
    assert data["approval"]["status"] == "rejected"
    assert data["approval"]["decided_by"] == "manager-2"
    assert data["approval"]["decision_reason"] == decision_payload["reason"]

    # Verify persistent state via GET
    get_res = client.get(f"/api/v1/approvals/{sample.approval_id}")
    assert get_res.status_code == 200
    assert get_res.json()["status"] == "rejected"

    # Verify audit event emitted
    events = audit.list_events(entity_type="approval", entity_id=sample.approval_id)
    event_types = [e.event_type for e in events]
    assert "approval.requested" in event_types
    assert "approval.rejected" in event_types


def test_decision_unknown_approval_returns_404(test_env):
    client = test_env["client"]
    response = client.post(
        "/api/v1/approvals/non-existent-id/decision",
        json={
            "decision": "approve",
            "reason": "Approving non-existent request",
        },
    )
    assert response.status_code == 404
    assert "was not found" in response.json()["detail"]


def test_unauthorized_approver_returns_403_and_leaves_request_pending_without_audit_event(
    test_env,
):
    """Proves failed authorization leaves request pending and does not create an audit decision event."""
    client = test_env["client"]
    workflow = test_env["workflow"]
    audit = test_env["audit"]
    sample = create_sample_request(
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
    )

    token_unauth = make_token("unauthorized-user", "unauth@example.com")

    # 1. Attempt decision with unauthorized approver
    response = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        headers={"Authorization": f"Bearer {token_unauth}"},
        json={
            "decision": "approve",
            "reason": "Unauthorized attempt to approve payment.",
        },
    )
    assert response.status_code == 403
    assert "not an authorized approver" in response.json()["detail"]

    # 2. Verify request remains pending
    get_res = client.get(f"/api/v1/approvals/{sample.approval_id}")
    assert get_res.status_code == 200
    assert get_res.json()["status"] == "pending"
    assert get_res.json()["decided_by"] is None
    assert get_res.json()["decided_at"] is None

    # 3. Verify audit log only contains the initial request event, and NO decision event
    events = audit.list_events(entity_type="approval", entity_id=sample.approval_id)
    assert len(events) == 1
    assert events[0].event_type == "approval.requested"
    assert "approval.approved" not in [e.event_type for e in events]
    assert "approval.rejected" not in [e.event_type for e in events]

    # 4. Verify hash chain integrity remains intact
    verification = audit.verify_chain()
    assert verification.valid is True
    assert verification.events_checked == 1


def test_requester_cannot_approve_own_request_returns_403(test_env):
    client = test_env["client"]
    workflow = test_env["workflow"]
    sample = create_sample_request(
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
    )

    token_analyst = make_token("analyst-1", "analyst1@example.com")
    response = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        headers={"Authorization": f"Bearer {token_analyst}"},
        json={
            "decision": "approve",
            "reason": "Self-approval attempt",
        },
    )
    assert response.status_code == 403

    # Check status is still pending
    assert workflow.get_request(sample.approval_id).status == "pending"


def test_repeated_decision_returns_409(test_env):
    client = test_env["client"]
    workflow = test_env["workflow"]
    sample = create_sample_request(workflow)

    first_decision = {
        "decision": "approve",
        "reason": "Initial approval",
    }
    first_res = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        json=first_decision,
    )
    assert first_res.status_code == 200

    # Second decision attempt on already-approved request with manager-2
    token_m2 = make_token("manager-2", "manager2@example.com")
    second_decision = {
        "decision": "reject",
        "reason": "Conflicting subsequent decision",
    }
    second_res = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        headers={"Authorization": f"Bearer {token_m2}"},
        json=second_decision,
    )
    assert second_res.status_code == 409
    assert "Cannot decide a request with status 'approved'" in second_res.json()["detail"]


def test_decision_validation_errors_return_422(test_env):
    client = test_env["client"]
    workflow = test_env["workflow"]
    sample = create_sample_request(workflow)

    # 1. Invalid decision choice (not 'approve' or 'reject')
    res_bad_decision = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        json={
            "decision": "maybe",
            "reason": "Not sure",
        },
    )
    assert res_bad_decision.status_code == 422

    # 2. Blank reason (whitespace only)
    res_blank_reason = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        json={
            "decision": "approve",
            "reason": "    ",
        },
    )
    assert res_blank_reason.status_code == 422

    # 3. Mismatched approval_id in body vs path
    res_mismatch = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        json={
            "approval_id": "different-approval-id",
            "decision": "approve",
            "reason": "Valid reason",
        },
    )
    assert res_mismatch.status_code == 422
    assert "does not match" in res_mismatch.json()["detail"]


def test_audit_events_list_and_filtering(test_env):
    client = test_env["client"]
    audit = test_env["audit"]

    # Append distinct audit events with property_id="prop-api"
    audit.append(
        AuditEventCreate(
            actor_id="agent-doc",
            actor_type="agent",
            event_type="document.analyzed",
            entity_type="document",
            entity_id="doc-101",
            payload={"pages": 2},
            correlation_id="corr-batch-1",
            property_id="prop-api",
        )
    )
    audit.append(
        AuditEventCreate(
            actor_id="agent-doc",
            actor_type="agent",
            event_type="document.analyzed",
            entity_type="document",
            entity_id="doc-102",
            payload={"pages": 5},
            correlation_id="corr-batch-1",
            property_id="prop-api",
        )
    )
    audit.append(
        AuditEventCreate(
            actor_id="analyst-1",
            actor_type="user",
            event_type="approval.requested",
            entity_type="approval",
            entity_id="app-201",
            payload={"amount": "100.00"},
            correlation_id="corr-batch-2",
            property_id="prop-api",
        )
    )

    # 1. Unfiltered list
    res_all = client.get("/api/v1/audit/events")
    assert res_all.status_code == 200
    all_events = res_all.json()
    assert len(all_events) == 3

    # 2. Filter by entity_type
    res_entity_type = client.get("/api/v1/audit/events?entity_type=document")
    assert res_entity_type.status_code == 200
    docs = res_entity_type.json()
    assert len(docs) == 2
    assert all(e["entity_type"] == "document" for e in docs)

    # 3. Filter by entity_id
    res_entity_id = client.get("/api/v1/audit/events?entity_id=app-201")
    assert res_entity_id.status_code == 200
    apps = res_entity_id.json()
    assert len(apps) == 1
    assert apps[0]["entity_id"] == "app-201"

    # 4. Filter by correlation_id
    res_corr = client.get("/api/v1/audit/events?correlation_id=corr-batch-1")
    assert res_corr.status_code == 200
    batch1 = res_corr.json()
    assert len(batch1) == 2
    assert all(e["correlation_id"] == "corr-batch-1" for e in batch1)

    # 5. Invalid limit
    res_limit_err = client.get("/api/v1/audit/events?limit=0")
    assert res_limit_err.status_code == 422


def test_audit_verify_endpoint(test_env):
    client = test_env["client"]
    audit = test_env["audit"]

    # Empty chain verification
    res_empty = client.get("/api/v1/audit/verify")
    assert res_empty.status_code == 200
    empty_data = res_empty.json()
    assert empty_data["valid"] is True
    assert empty_data["events_checked"] == 0

    # Append an event and verify
    audit.append(
        AuditEventCreate(
            actor_id="system",
            actor_type="system",
            event_type="system.startup",
            entity_type="system",
            entity_id="sys-1",
            property_id="prop-api",
        )
    )
    res_with_event = client.get("/api/v1/audit/verify")
    assert res_with_event.status_code == 200
    event_data = res_with_event.json()
    assert event_data["valid"] is True
    assert event_data["events_checked"] == 1
    assert event_data["first_invalid_sequence"] is None
    assert event_data["message"] == "Audit hash chain is valid."


def test_no_payment_execution_present(test_env):
    client = test_env["client"]
    workflow = test_env["workflow"]
    sample = create_sample_request(workflow)

    decision_res = client.post(
        f"/api/v1/approvals/{sample.approval_id}/decision",
        json={
            "decision": "approve",
            "reason": "Authorized approval only",
        },
    )
    assert decision_res.status_code == 200
    data = decision_res.json()
    # Confirm no payment execution was triggered or present in API response
    assert "payment_reference" not in data["approval"]
    assert "execute" not in data
    assert "payment_status" not in data
