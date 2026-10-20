"""Tests for SLA Monitoring and Escalation mechanism in Enigma."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from agents.sla_monitoring_agent import SLAMonitoringAgent
from repository.domain_repository import DomainRepository
from repository.exceptions import (
    CrossPropertyReferenceError,
    EntityNotFoundError,
    InvalidOperationError,
)
from schemas.audit import AuditEventCreate
from schemas.auth import Permission, UserRole
from schemas.domain import (
    MaintenanceRequestCreate,
    MaintenanceRequestRecord,
    MaintenanceRequestStatus,
    MaintenanceRequestUpdate,
    SlaEscalationCreate,
    SlaEscalationStatus,
    SlaEscalationType,
    SlaEscalationUpdate,
    UnitCreate,
    VendorCategory,
    VendorCreate,
    WorkOrderCreate,
    WorkOrderPriority,
    WorkOrderStatus,
    WorkOrderUpdate,
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

TEST_SECRET = "test-secret-at-least-32-chars-long-1234567890"
BASE_TIME = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sla_env(tmp_path):
    domain_db = tmp_path / "domain.sqlite3"
    auth_db = tmp_path / "auth.sqlite3"
    audit_db = tmp_path / "audit.sqlite3"

    repo = DomainRepository(domain_db)
    auth_repo = AuthRepository(auth_db)
    audit = AuditTrail(audit_db)

    config = AuthConfig(
        secret_key=TEST_SECRET,
        algorithm="HS256",
        access_token_expire_minutes=60,
        auth_db_path=auth_db,
        domain_db_path=domain_db,
    )

    # Provision properties in domain and auth
    repo.create_property(property_id="prop-a", name="Property A")
    repo.create_property(property_id="prop-b", name="Property B")

    auth_repo.create_property(property_id="prop-a", name="Property A")
    auth_repo.create_property(property_id="prop-b", name="Property B")

    unit_a = repo.create_unit("prop-a", UnitCreate(unit_number="101"))
    unit_b = repo.create_unit("prop-b", UnitCreate(unit_number="201"))

    # Provision user and memberships
    pwd = hash_password("Manager123!")
    user_a = auth_repo.create_user(
        email="manager_a@example.com",
        password_hash=pwd,
        user_id="user-a",
    )
    auth_repo.create_membership(
        property_id="prop-a",
        user_id="user-a",
        role=UserRole.PROPERTY_MANAGER,
    )

    user_b = auth_repo.create_user(
        email="manager_b@example.com",
        password_hash=pwd,
        user_id="user-b",
    )
    auth_repo.create_membership(
        property_id="prop-b",
        user_id="user-b",
        role=UserRole.PROPERTY_MANAGER,
    )

    agent = SLAMonitoringAgent(repo)

    token_a, _ = create_access_token(
        user_id="user-a",
        email="manager_a@example.com",
        secret_key=TEST_SECRET,
    )
    token_b, _ = create_access_token(
        user_id="user-b",
        email="manager_b@example.com",
        secret_key=TEST_SECRET,
    )

    main.app.dependency_overrides[get_auth_config] = lambda: config
    main.app.dependency_overrides[get_auth_repository] = lambda: auth_repo
    main.app.dependency_overrides[get_domain_repository] = lambda: repo
    main.app.dependency_overrides[main.get_audit_trail] = lambda: audit

    client_a = TestClient(main.app)
    client_a.headers["Authorization"] = f"Bearer {token_a}"
    client_a.headers["X-Property-ID"] = "prop-a"

    client_b = TestClient(main.app)
    client_b.headers["Authorization"] = f"Bearer {token_b}"
    client_b.headers["X-Property-ID"] = "prop-b"

    yield {
        "repo": repo,
        "auth_repo": auth_repo,
        "audit": audit,
        "agent": agent,
        "unit_a": unit_a,
        "unit_b": unit_b,
        "client_a": client_a,
        "client_b": client_b,
    }

    main.app.dependency_overrides.clear()


# -----------------------------------------------------------------------------
# 1. A request whose deadline is several hours away is not overdue.
# -----------------------------------------------------------------------------
def test_request_deadline_several_hours_away_is_not_overdue(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Leaky faucet in bathroom.",
        ),
    )
    # Deadline is 8 hours in the future
    future_deadline = BASE_TIME + timedelta(hours=8)
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            priority=WorkOrderPriority.MEDIUM,
            estimated_sla_hours=8,
            sla_deadline_at=future_deadline,
        ),
    )

    eval_result = agent.evaluate_request(
        repo.get_maintenance_request("prop-a", req.request_id),
        warning_window_hours=2.0,
        now=BASE_TIME,
    )
    assert eval_result is None

    result = agent.monitor_property_sla(
        "prop-a",
        warning_window_hours=2.0,
        now=BASE_TIME,
    )
    assert result.evaluated_count == 1
    assert result.approaching_deadline_count == 0
    assert result.overdue_count == 0
    assert result.new_escalations_count == 0
    assert result.existing_escalations_count == 0


# -----------------------------------------------------------------------------
# 2. A request inside the warning window is flagged as approaching.
# -----------------------------------------------------------------------------
def test_request_inside_warning_window_is_approaching(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Refrigerator not cooling.",
        ),
    )
    # Deadline is 1 hour in future (inside 2-hour window)
    deadline = BASE_TIME + timedelta(hours=1)
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            priority=WorkOrderPriority.HIGH,
            estimated_sla_hours=12,
            sla_deadline_at=deadline,
        ),
    )

    result = agent.monitor_property_sla(
        "prop-a",
        warning_window_hours=2.0,
        now=BASE_TIME,
    )
    assert result.evaluated_count == 1
    assert result.approaching_deadline_count == 1
    assert result.overdue_count == 0
    assert result.new_escalations_count == 1
    assert result.existing_escalations_count == 0

    assert len(result.escalations) == 1
    esc = result.escalations[0]
    assert esc.escalation_type == SlaEscalationType.APPROACHING_DEADLINE
    assert esc.status == SlaEscalationStatus.OPEN
    assert esc.request_id == req.request_id


# -----------------------------------------------------------------------------
# 3. A request whose deadline has passed is flagged as overdue.
# -----------------------------------------------------------------------------
def test_request_deadline_passed_is_overdue(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Broken main door lock.",
        ),
    )
    # Deadline passed 30 minutes ago
    deadline = BASE_TIME - timedelta(minutes=30)
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            priority=WorkOrderPriority.HIGH,
            estimated_sla_hours=12,
            sla_deadline_at=deadline,
        ),
    )

    result = agent.monitor_property_sla(
        "prop-a",
        warning_window_hours=2.0,
        now=BASE_TIME,
    )
    assert result.evaluated_count == 1
    assert result.approaching_deadline_count == 0
    assert result.overdue_count == 1
    assert result.new_escalations_count == 1
    assert result.existing_escalations_count == 0

    assert len(result.escalations) == 1
    esc = result.escalations[0]
    assert esc.escalation_type == SlaEscalationType.OVERDUE
    assert esc.status == SlaEscalationStatus.OPEN


# -----------------------------------------------------------------------------
# 4. A request with no SLA deadline is ignored.
# -----------------------------------------------------------------------------
def test_request_with_no_sla_deadline_is_ignored(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    # Newly submitted request (no triage, no sla_deadline_at)
    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Untriaged request without SLA deadline.",
        ),
    )
    assert req.sla_deadline_at is None

    result = agent.monitor_property_sla(
        "prop-a",
        warning_window_hours=2.0,
        now=BASE_TIME,
    )
    assert result.evaluated_count == 0
    assert result.approaching_deadline_count == 0
    assert result.overdue_count == 0
    assert result.new_escalations_count == 0


# -----------------------------------------------------------------------------
# 5. Closed and cancelled requests are excluded.
# -----------------------------------------------------------------------------
def test_closed_and_cancelled_requests_are_excluded(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    # Request 1: triaged -> cancelled
    req1 = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Cancelled repair request.",
        ),
    )
    repo.update_maintenance_request(
        "prop-a",
        req1.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            sla_deadline_at=BASE_TIME - timedelta(hours=2),
        ),
    )
    repo.update_maintenance_request(
        "prop-a",
        req1.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.CANCELLED,
        ),
    )

    # Request 2: triaged -> converted -> closed
    req2 = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Closed repair request.",
        ),
    )
    repo.update_maintenance_request(
        "prop-a",
        req2.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            sla_deadline_at=BASE_TIME - timedelta(hours=5),
        ),
    )
    wo = repo.convert_maintenance_request_to_work_order(
        "prop-a", req2.request_id
    )
    repo.update_maintenance_request(
        "prop-a",
        req2.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.CLOSED,
        ),
    )

    result = agent.monitor_property_sla(
        "prop-a",
        warning_window_hours=2.0,
        now=BASE_TIME,
    )
    assert result.evaluated_count == 0
    assert result.overdue_count == 0
    assert result.new_escalations_count == 0


# -----------------------------------------------------------------------------
# 6. Converted-to-work-order request handled according to monitoring policy.
# -----------------------------------------------------------------------------
def test_converted_to_work_order_request_handling(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="HVAC failure needing work order.",
        ),
    )
    deadline = BASE_TIME - timedelta(hours=1)
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            sla_deadline_at=deadline,
        ),
    )
    wo = repo.convert_maintenance_request_to_work_order(
        "prop-a", req.request_id
    )

    # 1. When work order is OPEN: request is actively monitored and flagged overdue
    result1 = agent.monitor_property_sla(
        "prop-a", warning_window_hours=2.0, now=BASE_TIME
    )
    assert result1.evaluated_count == 1
    assert result1.overdue_count == 1
    assert result1.new_escalations_count == 1

    # 2. When work order is completed: escalation is auto-resolved and excluded
    repo.update_work_order(
        "prop-a",
        wo.work_order_id,
        WorkOrderUpdate(status=WorkOrderStatus.ASSIGNED),
    )
    repo.update_work_order(
        "prop-a",
        wo.work_order_id,
        WorkOrderUpdate(status=WorkOrderStatus.IN_PROGRESS),
    )
    repo.update_work_order(
        "prop-a",
        wo.work_order_id,
        WorkOrderUpdate(status=WorkOrderStatus.COMPLETED),
    )

    result2 = agent.monitor_property_sla(
        "prop-a", warning_window_hours=2.0, now=BASE_TIME
    )
    assert result2.overdue_count == 0
    assert result2.existing_escalations_count == 0

    active_escalations = [
        e
        for e in repo.list_sla_escalations("prop-a", request_id=req.request_id)
        if e.status in (SlaEscalationStatus.OPEN, SlaEscalationStatus.ACKNOWLEDGED)
    ]
    assert len(active_escalations) == 0


# -----------------------------------------------------------------------------
# 7. Repeated monitoring runs do not create duplicate active escalation records.
# -----------------------------------------------------------------------------
def test_repeated_monitoring_runs_no_duplicates(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Water leaking from sink.",
        ),
    )
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            sla_deadline_at=BASE_TIME - timedelta(hours=1),
        ),
    )

    # Run 1: Creates new escalation
    run1 = agent.monitor_property_sla("prop-a", now=BASE_TIME)
    assert run1.new_escalations_count == 1
    assert run1.existing_escalations_count == 0

    # Run 2: Re-evaluates without duplicate
    run2 = agent.monitor_property_sla("prop-a", now=BASE_TIME)
    assert run2.new_escalations_count == 0
    assert run2.existing_escalations_count == 1

    # Run 3: Still no duplicate
    run3 = agent.monitor_property_sla("prop-a", now=BASE_TIME)
    assert run3.new_escalations_count == 0
    assert run3.existing_escalations_count == 1

    all_escalations = repo.list_sla_escalations("prop-a", request_id=req.request_id)
    assert len(all_escalations) == 1


# -----------------------------------------------------------------------------
# 8. An approaching escalation transitions correctly when the request becomes overdue.
# -----------------------------------------------------------------------------
def test_approaching_escalation_transitions_when_overdue(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Window latch loose.",
        ),
    )
    # Deadline is 1 hour after BASE_TIME
    deadline = BASE_TIME + timedelta(hours=1)
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            sla_deadline_at=deadline,
        ),
    )

    # Time 1: At BASE_TIME, deadline is in 1 hour (approaching)
    run1 = agent.monitor_property_sla("prop-a", warning_window_hours=2.0, now=BASE_TIME)
    assert run1.approaching_deadline_count == 1
    assert run1.overdue_count == 0
    assert run1.new_escalations_count == 1

    approaching_esc = run1.escalations[0]
    assert approaching_esc.escalation_type == SlaEscalationType.APPROACHING_DEADLINE
    assert approaching_esc.status == SlaEscalationStatus.OPEN

    # Time 2: 2 hours later, deadline has now passed!
    later_time = BASE_TIME + timedelta(hours=2)
    run2 = agent.monitor_property_sla("prop-a", warning_window_hours=2.0, now=later_time)
    assert run2.approaching_deadline_count == 0
    assert run2.overdue_count == 1
    assert run2.new_escalations_count == 1

    # Old approaching escalation is now resolved
    old_esc = repo.get_sla_escalation("prop-a", approaching_esc.escalation_id)
    assert old_esc.status == SlaEscalationStatus.RESOLVED

    # New overdue escalation is active (open)
    new_esc = run2.escalations[0]
    assert new_esc.escalation_type == SlaEscalationType.OVERDUE
    assert new_esc.status == SlaEscalationStatus.OPEN

    # Exactly one active escalation exists for this request
    active = [
        e
        for e in repo.list_sla_escalations("prop-a", request_id=req.request_id)
        if e.status in (SlaEscalationStatus.OPEN, SlaEscalationStatus.ACKNOWLEDGED)
    ]
    assert len(active) == 1
    assert active[0].escalation_type == SlaEscalationType.OVERDUE


# -----------------------------------------------------------------------------
# 9. Acknowledged escalation status is preserved during repeated monitoring.
# -----------------------------------------------------------------------------
def test_acknowledged_status_preserved_during_monitoring(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Dishwasher leaking.",
        ),
    )
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            sla_deadline_at=BASE_TIME - timedelta(hours=1),
        ),
    )

    run1 = agent.monitor_property_sla("prop-a", now=BASE_TIME)
    esc_id = run1.escalations[0].escalation_id

    # Manager acknowledges the escalation
    ack_esc = repo.acknowledge_sla_escalation(
        "prop-a", esc_id, notes="Vendor has been notified via phone."
    )
    assert ack_esc.status == SlaEscalationStatus.ACKNOWLEDGED

    # Subsequent monitoring cycle
    run2 = agent.monitor_property_sla("prop-a", now=BASE_TIME)
    assert run2.existing_escalations_count == 1
    assert run2.new_escalations_count == 0

    esc_after = repo.get_sla_escalation("prop-a", esc_id)
    assert esc_after.status == SlaEscalationStatus.ACKNOWLEDGED
    assert esc_after.notes == "Vendor has been notified via phone."


# -----------------------------------------------------------------------------
# 10. A resolved or closed request does not retain an active escalation.
# -----------------------------------------------------------------------------
def test_resolved_or_closed_request_does_not_retain_active_escalation(sla_env):
    repo = sla_env["repo"]
    agent = sla_env["agent"]
    unit_a = sla_env["unit_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Electrical spark in wall switch.",
        ),
    )
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            sla_deadline_at=BASE_TIME - timedelta(hours=2),
        ),
    )

    run1 = agent.monitor_property_sla("prop-a", now=BASE_TIME)
    assert run1.new_escalations_count == 1
    esc_id = run1.escalations[0].escalation_id

    # Close the maintenance request via repo update
    repo.convert_maintenance_request_to_work_order("prop-a", req.request_id)
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(status=MaintenanceRequestStatus.CLOSED),
    )

    # Escalation was resolved upon request closing
    esc = repo.get_sla_escalation("prop-a", esc_id)
    assert esc.status == SlaEscalationStatus.RESOLVED

    # Running monitor also verifies no active escalations remain
    run2 = agent.monitor_property_sla("prop-a", now=BASE_TIME)
    assert run2.overdue_count == 0
    assert run2.existing_escalations_count == 0

    active_escalations = [
        e
        for e in repo.list_sla_escalations("prop-a", request_id=req.request_id)
        if e.status in (SlaEscalationStatus.OPEN, SlaEscalationStatus.ACKNOWLEDGED)
    ]
    assert len(active_escalations) == 0


# -----------------------------------------------------------------------------
# 11. Property A cannot read or modify Property B's escalation.
# -----------------------------------------------------------------------------
def test_property_isolation_on_escalations(sla_env):
    repo = sla_env["repo"]
    unit_a = sla_env["unit_a"]
    unit_b = sla_env["unit_b"]
    client_a = sla_env["client_a"]
    client_b = sla_env["client_b"]

    # Create request and escalation in Property A
    req_a = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Property A private issue.",
        ),
    )
    esc_a = repo.create_sla_escalation(
        "prop-a",
        SlaEscalationCreate(
            request_id=req_a.request_id,
            escalation_type=SlaEscalationType.OVERDUE,
            sla_deadline_at=BASE_TIME - timedelta(hours=1),
        ),
    )

    # Repository level: Property B cannot access Property A escalation
    with pytest.raises(CrossPropertyReferenceError):
        repo.update_sla_escalation_status(
            "prop-b",
            esc_a.escalation_id,
            SlaEscalationUpdate(status=SlaEscalationStatus.ACKNOWLEDGED),
        )

    # API level: Property B client trying to access Property A escalation
    res_get = client_b.get(
        f"/api/v1/properties/prop-b/sla/escalations/{esc_a.escalation_id}"
    )
    assert res_get.status_code in (403, 404)

    # API level: Property B client trying to update Property A escalation
    res_patch = client_b.patch(
        f"/api/v1/properties/prop-b/sla/escalations/{esc_a.escalation_id}",
        json={"status": "acknowledged"},
    )
    assert res_patch.status_code in (400, 403, 404)

    # API level: Property A client trying to specify property_id in path that doesn't match header
    res_mismatch = client_a.get(
        f"/api/v1/properties/prop-b/sla/escalations/{esc_a.escalation_id}"
    )
    assert res_mismatch.status_code == 403


# -----------------------------------------------------------------------------
# 12. Invalid escalation status transitions are rejected.
# -----------------------------------------------------------------------------
def test_invalid_escalation_status_transitions(sla_env):
    repo = sla_env["repo"]
    unit_a = sla_env["unit_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="Thermostat unresponsive.",
        ),
    )
    esc = repo.create_sla_escalation(
        "prop-a",
        SlaEscalationCreate(
            request_id=req.request_id,
            escalation_type=SlaEscalationType.OVERDUE,
            sla_deadline_at=BASE_TIME - timedelta(hours=1),
        ),
    )

    # 1. Acknowledge the escalation (open -> acknowledged is valid)
    acked = repo.acknowledge_sla_escalation("prop-a", esc.escalation_id)
    assert acked.status == SlaEscalationStatus.ACKNOWLEDGED

    # 2. Transitioning from acknowledged back to open is invalid
    with pytest.raises(InvalidOperationError):
        repo.update_sla_escalation_status(
            "prop-a",
            esc.escalation_id,
            SlaEscalationUpdate(status=SlaEscalationStatus.OPEN),
        )

    # 3. Resolve the escalation (acknowledged -> resolved is valid)
    resolved = repo.resolve_sla_escalation("prop-a", esc.escalation_id)
    assert resolved.status == SlaEscalationStatus.RESOLVED

    # 4. Transitioning from resolved back to open or acknowledged is invalid
    with pytest.raises(InvalidOperationError):
        repo.update_sla_escalation_status(
            "prop-a",
            esc.escalation_id,
            SlaEscalationUpdate(status=SlaEscalationStatus.OPEN),
        )
    with pytest.raises(InvalidOperationError):
        repo.update_sla_escalation_status(
            "prop-a",
            esc.escalation_id,
            SlaEscalationUpdate(status=SlaEscalationStatus.ACKNOWLEDGED),
        )


# -----------------------------------------------------------------------------
# 13. Pagination and filtering work correctly.
# -----------------------------------------------------------------------------
def test_pagination_and_filtering(sla_env):
    repo = sla_env["repo"]
    unit_a = sla_env["unit_a"]

    reqs = []
    for i in range(5):
        r = repo.create_maintenance_request(
            "prop-a",
            MaintenanceRequestCreate(
                unit_id=unit_a.unit_id,
                description=f"Batch test request {i}",
            ),
        )
        reqs.append(r)

    # Create 3 approaching and 2 overdue
    repo.create_sla_escalation(
        "prop-a",
        SlaEscalationCreate(
            request_id=reqs[0].request_id,
            escalation_type=SlaEscalationType.APPROACHING_DEADLINE,
            sla_deadline_at=BASE_TIME + timedelta(hours=1),
        ),
    )
    repo.create_sla_escalation(
        "prop-a",
        SlaEscalationCreate(
            request_id=reqs[1].request_id,
            escalation_type=SlaEscalationType.APPROACHING_DEADLINE,
            sla_deadline_at=BASE_TIME + timedelta(hours=1),
        ),
    )
    repo.create_sla_escalation(
        "prop-a",
        SlaEscalationCreate(
            request_id=reqs[2].request_id,
            escalation_type=SlaEscalationType.APPROACHING_DEADLINE,
            sla_deadline_at=BASE_TIME + timedelta(hours=1),
        ),
    )
    e3 = repo.create_sla_escalation(
        "prop-a",
        SlaEscalationCreate(
            request_id=reqs[3].request_id,
            escalation_type=SlaEscalationType.OVERDUE,
            sla_deadline_at=BASE_TIME - timedelta(hours=1),
        ),
    )
    e4 = repo.create_sla_escalation(
        "prop-a",
        SlaEscalationCreate(
            request_id=reqs[4].request_id,
            escalation_type=SlaEscalationType.OVERDUE,
            sla_deadline_at=BASE_TIME - timedelta(hours=2),
        ),
    )
    repo.resolve_sla_escalation("prop-a", e4.escalation_id)

    # Filter by escalation_type = overdue
    overdue_list = repo.list_sla_escalations(
        "prop-a", escalation_type=SlaEscalationType.OVERDUE
    )
    assert len(overdue_list) == 2

    # Filter by status = open
    open_list = repo.list_sla_escalations(
        "prop-a", status=SlaEscalationStatus.OPEN
    )
    assert len(open_list) == 4

    # Filter by status = resolved
    resolved_list = repo.list_sla_escalations(
        "prop-a", status=SlaEscalationStatus.RESOLVED
    )
    assert len(resolved_list) == 1
    assert resolved_list[0].escalation_id == e4.escalation_id

    # Pagination: limit 2, offset 0
    p1 = repo.list_sla_escalations("prop-a", limit=2, offset=0)
    assert len(p1) == 2

    # Pagination: limit 2, offset 2
    p2 = repo.list_sla_escalations("prop-a", limit=2, offset=2)
    assert len(p2) == 2
    assert p1[0].escalation_id != p2[0].escalation_id

    # Count
    cnt = repo.count_sla_escalations("prop-a")
    assert cnt == 5


# -----------------------------------------------------------------------------
# 14. API endpoints work and audit trail records events.
# -----------------------------------------------------------------------------
def test_sla_api_endpoints_and_audit_trail(sla_env):
    repo = sla_env["repo"]
    audit = sla_env["audit"]
    unit_a = sla_env["unit_a"]
    client_a = sla_env["client_a"]

    req = repo.create_maintenance_request(
        "prop-a",
        MaintenanceRequestCreate(
            unit_id=unit_a.unit_id,
            description="API integration test request.",
        ),
    )
    repo.update_maintenance_request(
        "prop-a",
        req.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            sla_deadline_at=BASE_TIME - timedelta(hours=1),
        ),
    )

    # 1. POST /api/v1/properties/{property_id}/sla/monitor
    res_monitor = client_a.post(
        "/api/v1/properties/prop-a/sla/monitor?warning_window_hours=2.0"
    )
    assert res_monitor.status_code == 200
    mon_data = res_monitor.json()
    assert mon_data["property_id"] == "prop-a"
    assert mon_data["evaluated_count"] == 1
    assert mon_data["overdue_count"] == 1
    assert mon_data["new_escalations_count"] == 1
    assert len(mon_data["escalations"]) == 1
    esc_id = mon_data["escalations"][0]["escalation_id"]

    # Check audit trail recorded monitoring run
    audit_events = audit.list_events(property_id="prop-a", limit=100)
    run_events = [e for e in audit_events if e.event_type == "sla.monitoring_run"]
    assert len(run_events) >= 1
    assert run_events[0].payload["overdue_count"] == 1

    # 2. GET /api/v1/properties/{property_id}/sla/escalations
    res_list = client_a.get("/api/v1/properties/prop-a/sla/escalations")
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert len(list_data) == 1
    assert list_data[0]["escalation_id"] == esc_id

    # 3. GET /api/v1/properties/{property_id}/sla/escalations/{escalation_id}
    res_get = client_a.get(
        f"/api/v1/properties/prop-a/sla/escalations/{esc_id}"
    )
    assert res_get.status_code == 200
    get_data = res_get.json()
    assert get_data["escalation_id"] == esc_id
    assert get_data["status"] == "open"

    # 4. PATCH /api/v1/properties/{property_id}/sla/escalations/{escalation_id} (Acknowledge)
    res_patch_ack = client_a.patch(
        f"/api/v1/properties/prop-a/sla/escalations/{esc_id}",
        json={"status": "acknowledged", "notes": "Acknowledged by manager."},
    )
    assert res_patch_ack.status_code == 200
    ack_data = res_patch_ack.json()
    assert ack_data["status"] == "acknowledged"
    assert ack_data["notes"] == "Acknowledged by manager."

    # 5. PATCH /api/v1/properties/{property_id}/sla/escalations/{escalation_id} (Resolve)
    res_patch_res = client_a.patch(
        f"/api/v1/properties/prop-a/sla/escalations/{esc_id}",
        json={"status": "resolved", "notes": "Vendor dispatched and resolved issue."},
    )
    assert res_patch_res.status_code == 200
    res_data = res_patch_res.json()
    assert res_data["status"] == "resolved"

    # Verify audit event for patch
    audit_events = audit.list_events(property_id="prop-a", limit=100)
    status_events = [
        e
        for e in audit_events
        if e.event_type == "sla.escalation_status_updated"
    ]
    assert len(status_events) == 2


# -----------------------------------------------------------------------------
# 15. Existing maintenance-request CRUD, triage, conversion, and dispatch functionality remains intact.
# -----------------------------------------------------------------------------
def test_maintenance_request_lifecycle_regression(sla_env):
    repo = sla_env["repo"]
    unit_a = sla_env["unit_a"]
    client_a = sla_env["client_a"]

    # 1. Create vendor
    repo.create_vendor(
        "prop-a",
        VendorCreate(
            name="Emergency Plumbing Pros",
            category=VendorCategory.PLUMBING,
            email="service@emergencyplumbing.test",
        ),
    )

    # 2. Create request via API
    res_create = client_a.post(
        "/api/v1/properties/prop-a/maintenance-requests",
        json={
            "unit_id": unit_a.unit_id,
            "description": "Burst pipe water pouring from bathroom ceiling.",
        },
    )
    assert res_create.status_code == 201
    created_data = res_create.json()
    request_id = created_data["request_id"]
    assert created_data["status"] == "submitted"

    # 3. Triage request via API
    res_triage = client_a.post(
        f"/api/v1/properties/prop-a/maintenance-requests/{request_id}/triage"
    )
    assert res_triage.status_code == 200
    triaged_data = res_triage.json()
    assert triaged_data["status"] == "triaged"
    assert triaged_data["is_emergency"] is True
    assert triaged_data["sla_deadline_at"] is not None

    # 4. Convert request via API
    res_convert = client_a.post(
        f"/api/v1/properties/prop-a/maintenance-requests/{request_id}/convert"
    )
    assert res_convert.status_code == 200
    work_order_data = res_convert.json()
    assert work_order_data["work_order_id"] is not None

    # 5. Dispatch request via API
    res_dispatch = client_a.post(
        f"/api/v1/properties/prop-a/maintenance-requests/{request_id}/dispatch"
    )
    assert res_dispatch.status_code == 200
    dispatch_data = res_dispatch.json()
    assert dispatch_data["assigned_vendor"] is not None
    assert dispatch_data["assigned_vendor"]["vendor_name"] == "Emergency Plumbing Pros"
