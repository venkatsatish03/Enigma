"""Unit and integration tests for Maintenance & Property Operations Agent."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agents.maintenance_agent import MaintenanceAgent
from repository.domain_repository import DomainRepository
from schemas.domain import (
    PropertyUpdate,
    UnitCreate,
    VendorCategory,
    VendorCreate,
    WorkOrderCreate,
    WorkOrderPriority,
    WorkOrderRecord,
    WorkOrderStatus,
)
from schemas.maintenance_agent import (
    AssetCategory,
    AssetCreate,
    AutoDispatchWorkOrderRequest,
    InspectionChecklistItem,
    InspectionItemStatus,
    InspectionReportCreate,
    InspectionType,
    MaintenanceCategory,
    MaintenanceFindingType,
    MaintenanceOperationsAnalysisRequest,
    MaintenanceTriageRequest,
    PreventiveFrequency,
    PreventiveTaskCreate,
)


@pytest.fixture
def repo(tmp_path: Path) -> DomainRepository:
    db_path = tmp_path / "maintenance_domain.sqlite3"
    repository = DomainRepository(db_path)
    repository.ensure_property(property_id="prop-test", name="Highland Towers")
    repository.create_unit("prop-test", UnitCreate(unit_number="101"))
    repository.create_unit("prop-test", UnitCreate(unit_number="102"))
    return repository


@pytest.fixture
def agent(repo: DomainRepository) -> MaintenanceAgent:
    return MaintenanceAgent(repo)


# =============================================================================
# 1. Triage & Emergency Handling Tests
# =============================================================================

def test_triage_detects_gas_leak_emergency(agent: MaintenanceAgent) -> None:
    req = MaintenanceTriageRequest(description="I smell gas near the kitchen stove, very strong gas odor!")
    res = agent.triage_request(req)

    assert res.is_emergency is True
    assert res.priority == WorkOrderPriority.EMERGENCY
    assert res.estimated_sla_hours == 2
    assert "Evacuate" in res.immediate_safety_instructions[0]
    assert "gas_leak" in res.extracted_keywords


def test_triage_detects_active_flood_emergency(agent: MaintenanceAgent) -> None:
    req = MaintenanceTriageRequest(description="Burst pipe in the bathroom, water pouring everywhere and active flood!")
    res = agent.triage_request(req)

    assert res.is_emergency is True
    assert res.priority == WorkOrderPriority.EMERGENCY
    assert res.category == MaintenanceCategory.PLUMBING
    assert any("shut off the main water valve" in instr.lower() for instr in res.immediate_safety_instructions)


def test_triage_detects_electrical_fire_hazard(agent: MaintenanceAgent) -> None:
    req = MaintenanceTriageRequest(description="The main breaker outlet is sparking with smoke coming from outlet")
    res = agent.triage_request(req)

    assert res.is_emergency is True
    assert res.priority == WorkOrderPriority.EMERGENCY
    assert res.category == MaintenanceCategory.ELECTRICAL
    assert res.suggested_vendor_category == VendorCategory.ELECTRICAL


def test_triage_classifies_standard_categories_and_priorities(agent: MaintenanceAgent) -> None:
    # Plumbing standard
    res_plumbing = agent.triage_request(MaintenanceTriageRequest(description="Kitchen sink faucet has a minor leak dripping slowly"))
    assert res_plumbing.category == MaintenanceCategory.PLUMBING
    assert res_plumbing.priority == WorkOrderPriority.LOW
    assert res_plumbing.is_emergency is False

    # HVAC
    res_hvac = agent.triage_request(MaintenanceTriageRequest(description="The AC is blowing warm air and the thermostat seems dead"))
    assert res_hvac.category == MaintenanceCategory.HVAC
    assert res_hvac.suggested_vendor_category == VendorCategory.HVAC
    assert res_hvac.priority == WorkOrderPriority.MEDIUM

    # Appliance
    res_appliance = agent.triage_request(MaintenanceTriageRequest(description="Refrigerator is not cooling and the freezer is defrosting"))
    assert res_appliance.category == MaintenanceCategory.APPLIANCE

    # Structural
    res_struct = agent.triage_request(MaintenanceTriageRequest(description="Front door lock latch is sticking and window won't close"))
    assert res_struct.category == MaintenanceCategory.STRUCTURAL

    # Pest
    res_pest = agent.triage_request(MaintenanceTriageRequest(description="Saw roach and mouse droppings under the pantry"))
    assert res_pest.category == MaintenanceCategory.PEST_CONTROL


# =============================================================================
# 2. Vendor Coordination & Auto-Dispatch Tests
# =============================================================================

def test_vendor_recommendation_and_specialty_scoring(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    # Create vendors
    v_plumber = repo.create_vendor("prop-test", VendorCreate(name="Apex Plumbing", category=VendorCategory.PLUMBING))
    v_general = repo.create_vendor("prop-test", VendorCreate(name="Handy Joe", category=VendorCategory.GENERAL))
    v_electric = repo.create_vendor("prop-test", VendorCreate(name="Sparky Electric", category=VendorCategory.ELECTRICAL))

    recs = agent.recommend_vendors("prop-test", VendorCategory.PLUMBING)

    assert len(recs) == 3
    assert recs[0].vendor_id == v_plumber.vendor_id
    assert recs[0].match_score >= 0.9
    assert recs[1].vendor_id == v_general.vendor_id
    assert recs[1].match_score == 0.6
    assert recs[2].vendor_id == v_electric.vendor_id
    assert recs[2].match_score == 0.2


def test_auto_dispatch_creates_and_assigns_work_order(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    unit = repo.list_units("prop-test")[0]
    v_plumber = repo.create_vendor("prop-test", VendorCreate(name="Rapid Plumbing", category=VendorCategory.PLUMBING))

    req = AutoDispatchWorkOrderRequest(
        unit_id=unit.unit_id,
        title="Bathroom toilet overflowing",
        description="Toilet overflowing with water leaking across the floor urgently",
        auto_assign=True,
    )
    result = agent.auto_dispatch("prop-test", req)

    assert result.work_order_id is not None
    assert result.status == WorkOrderStatus.ASSIGNED
    assert result.assigned_vendor is not None
    assert result.assigned_vendor.vendor_id == v_plumber.vendor_id
    assert result.category == MaintenanceCategory.PLUMBING
    assert result.priority in (WorkOrderPriority.HIGH, WorkOrderPriority.EMERGENCY)


# =============================================================================
# 3. Inspections & Deficiency Work Order Generation Tests
# =============================================================================

def test_record_inspection_creates_work_orders_for_deficiencies(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    unit = repo.list_units("prop-test")[0]

    report = InspectionReportCreate(
        unit_id=unit.unit_id,
        inspection_type=InspectionType.MOVE_IN,
        inspector_name="Inspector Dave",
        items=[
            InspectionChecklistItem(
                item_name="Smoke Detectors",
                category=MaintenanceCategory.ELECTRICAL,
                status=InspectionItemStatus.PASS,
            ),
            InspectionChecklistItem(
                item_name="Bathroom Faucet",
                category=MaintenanceCategory.PLUMBING,
                status=InspectionItemStatus.FAIL,
                notes="Faucet leaking at base with low water pressure",
                severity_if_failed=WorkOrderPriority.MEDIUM,
            ),
            InspectionChecklistItem(
                item_name="Front Door Deadbolt",
                category=MaintenanceCategory.STRUCTURAL,
                status=InspectionItemStatus.ATTENTION_NEEDED,
                notes="Latch misaligned, difficult to lock",
                severity_if_failed=WorkOrderPriority.HIGH,
            ),
        ],
    )

    result = agent.record_inspection("prop-test", report)

    assert result.items_inspected == 3
    assert result.items_passed == 1
    assert result.items_failed == 2
    assert len(result.generated_work_orders) == 2
    assert len(result.findings) == 2

    # Verify work orders exist in repository
    wos = repo.list_work_orders("prop-test")
    assert len(wos) == 2
    titles = [wo.title for wo in wos]
    assert any("Bathroom Faucet" in t for t in titles)
    assert any("Front Door Deadbolt" in t for t in titles)


# =============================================================================
# 4. Preventive Maintenance & Asset Health Tests
# =============================================================================

def test_operations_analysis_detects_overdue_and_upcoming_schedules(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    today = date(2026, 10, 19)

    # Overdue schedule (due 10 days ago)
    repo.create_preventive_schedule(
        "prop-test",
        PreventiveTaskCreate(
            title="Q3 HVAC Filter Change",
            category=MaintenanceCategory.HVAC,
            frequency=PreventiveFrequency.QUARTERLY,
            interval_days=90,
            next_due_date=date(2026, 10, 9),
        ),
    )

    # Upcoming schedule (due in 5 days)
    repo.create_preventive_schedule(
        "prop-test",
        PreventiveTaskCreate(
            title="Fire Alarm Semiannual Inspection",
            category=MaintenanceCategory.ELECTRICAL,
            frequency=PreventiveFrequency.SEMI_ANNUAL,
            interval_days=180,
            next_due_date=date(2026, 10, 24),
        ),
    )

    # Equipment Asset with warranty expiring soon
    repo.create_asset(
        "prop-test",
        AssetCreate(
            name="Rheem 50-Gal Water Heater",
            asset_category=AssetCategory.WATER_HEATER,
            make_model="Rheem ProG50",
            serial_number="RH-982341",
            install_date=date(2020, 11, 1),
            warranty_expiry_date=date(2026, 11, 5),  # ~17 days from as_of
        ),
    )

    analysis = agent.analyze_operations(
        "prop-test",
        MaintenanceOperationsAnalysisRequest(as_of_date=today),
    )

    assert analysis.preventive_tasks_due_count == 2
    assert analysis.assets_tracked_count == 1

    finding_types = [f.finding_type for f in analysis.findings]
    assert MaintenanceFindingType.PREVENTIVE_MAINTENANCE_OVERDUE in finding_types
    assert MaintenanceFindingType.PREVENTIVE_MAINTENANCE_DUE in finding_types
    assert MaintenanceFindingType.WARRANTY_EXPIRING_SOON in finding_types


# =============================================================================
# 5. Cost Tracking & Reporting Tests
# =============================================================================

def test_cost_analysis_calculates_variance_and_overruns(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    unit = repo.list_units("prop-test")[0]
    vendor = repo.create_vendor("prop-test", VendorCreate(name="Precision HVAC", category=VendorCategory.HVAC))

    # Normal WO: actual equal to estimate
    repo.create_work_order(
        "prop-test",
        WorkOrderCreate(
            unit_id=unit.unit_id,
            vendor_id=vendor.vendor_id,
            title="HVAC Capacitor replacement",
            description="Replace burned out start capacitor",
            priority=WorkOrderPriority.MEDIUM,
            status=WorkOrderStatus.COMPLETED,
            estimated_cost=Decimal("150.00"),
            actual_cost=Decimal("150.00"),
        ),
    )

    # Overrun WO: actual exceeds estimate
    wo_overrun = repo.create_work_order(
        "prop-test",
        WorkOrderCreate(
            unit_id=unit.unit_id,
            vendor_id=vendor.vendor_id,
            title="Emergency pipe soldering in wall",
            description="Pipe solder leak repair",
            priority=WorkOrderPriority.HIGH,
            status=WorkOrderStatus.COMPLETED,
            estimated_cost=Decimal("200.00"),
            actual_cost=Decimal("350.00"),
        ),
    )

    cost_report = agent.analyze_costs("prop-test", as_of_date=date(2026, 10, 19))

    assert cost_report.total_work_orders == 2
    assert cost_report.completed_work_orders == 2
    assert cost_report.total_estimated_spend == Decimal("350.00")
    assert cost_report.total_actual_spend == Decimal("500.00")
    assert cost_report.budget_variance == Decimal("150.00")  # $500 - $350
    assert wo_overrun.work_order_id in cost_report.cost_overrun_work_orders
    assert len(cost_report.category_breakdown) >= 1


# =============================================================================
# 6. REST API Endpoints Integration Tests
# =============================================================================

from fastapi.testclient import TestClient
import main
from schemas.auth import UserRole
from tools.approval_workflow import ApprovalWorkflow
from tools.audit_trail import AuditTrail
from tools.auth_config import AuthConfig
from tools.auth_crypto import create_access_token
from tools.auth_dependencies import (
    get_auth_config,
    get_auth_repository,
    get_domain_repository,
)
from tools.auth_repository import AuthRepository


@pytest.fixture
def client_env(tmp_path: Path):
    auth_db = tmp_path / "api_auth.sqlite3"
    audit_db = tmp_path / "api_audit.sqlite3"
    approvals_db = tmp_path / "api_approvals.sqlite3"
    domain_db = tmp_path / "api_domain.sqlite3"

    auth_repo = AuthRepository(auth_db)
    audit = AuditTrail(audit_db)
    workflow = ApprovalWorkflow(approvals_db, audit)
    domain_repo = DomainRepository(domain_db)

    secret = "maintenance-secret-must-be-at-least-32-chars-long"
    config = AuthConfig(
        secret_key=secret,
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

    # Bootstrap Property and Manager
    prop = auth_repo.create_property(name="Parkview Lofts", property_id="prop-parkview")
    domain_repo.ensure_property(property_id="prop-parkview", name="Parkview Lofts")
    unit = domain_repo.create_unit("prop-parkview", UnitCreate(unit_number="301"))
    vendor = domain_repo.create_vendor("prop-parkview", VendorCreate(name="Parkview HVAC", category=VendorCategory.HVAC))

    user = auth_repo.create_user(email="manager@parkview.example", password_hash="hashedpassword123")
    auth_repo.create_membership(
        user_id=user.user_id,
        property_id="prop-parkview",
        role=UserRole.PROPERTY_MANAGER,
    )

    token, _ = create_access_token(
        user_id=user.user_id,
        email="manager@parkview.example",
        secret_key=secret,
        expires_delta=timedelta(minutes=60),
    )
    headers = {"Authorization": f"Bearer {token}"}

    client = TestClient(main.app)
    return {
        "client": client,
        "headers": headers,
        "property_id": "prop-parkview",
        "unit_id": unit.unit_id,
        "vendor_id": vendor.vendor_id,
        "domain_repo": domain_repo,
        "audit": audit,
        "auth_repo": auth_repo,
    }


def test_api_triage_endpoint(client_env):
    client = client_env["client"]
    headers = client_env["headers"]
    prop_id = client_env["property_id"]

    res = client.post(
        f"/api/v1/properties/{prop_id}/maintenance/triage",
        headers=headers,
        json={"description": "Gas smell coming from heater closet!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["is_emergency"] is True
    assert data["priority"] == "emergency"
    assert len(data["immediate_safety_instructions"]) > 0


def test_api_auto_dispatch_endpoint(client_env):
    client = client_env["client"]
    headers = client_env["headers"]
    prop_id = client_env["property_id"]
    unit_id = client_env["unit_id"]

    res = client.post(
        f"/api/v1/properties/{prop_id}/maintenance/auto-dispatch",
        headers=headers,
        json={
            "unit_id": unit_id,
            "title": "Air conditioning unit blowing hot air",
            "description": "AC unit is not cooling and making rattling noise",
            "auto_assign": True,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["category"] == "hvac"
    assert data["status"] == "assigned"
    assert data["assigned_vendor"] is not None
    assert data["assigned_vendor"]["vendor_name"] == "Parkview HVAC"


def test_api_inspections_endpoint(client_env):
    client = client_env["client"]
    headers = client_env["headers"]
    prop_id = client_env["property_id"]
    unit_id = client_env["unit_id"]

    res = client.post(
        f"/api/v1/properties/{prop_id}/maintenance/inspections",
        headers=headers,
        json={
            "unit_id": unit_id,
            "inspection_type": "move_out",
            "inspector_name": "Supervisor Sarah",
            "items": [
                {
                    "item_name": "Living room window latch",
                    "category": "structural",
                    "status": "fail",
                    "notes": "Latch broken completely off",
                    "severity_if_failed": "high",
                }
            ],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["items_inspected"] == 1
    assert data["items_failed"] == 1
    assert len(data["generated_work_orders"]) == 1


def test_api_assets_and_preventive_endpoints(client_env):
    client = client_env["client"]
    headers = client_env["headers"]
    prop_id = client_env["property_id"]
    unit_id = client_env["unit_id"]

    # 1. Create asset
    res_asset = client.post(
        f"/api/v1/properties/{prop_id}/maintenance/assets",
        headers=headers,
        json={
            "name": "Carrier Rooftop Heat Pump",
            "asset_category": "hvac",
            "unit_id": unit_id,
            "serial_number": "CARRIER-HP-99",
            "purchase_cost": "3200.00",
        },
    )
    assert res_asset.status_code == 201
    asset_id = res_asset.json()["asset_id"]

    # List assets
    list_res = client.get(f"/api/v1/properties/{prop_id}/maintenance/assets", headers=headers)
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1

    # 2. Create preventive schedule
    res_prev = client.post(
        f"/api/v1/properties/{prop_id}/maintenance/preventive-tasks",
        headers=headers,
        json={
            "title": "Clean Condenser Coils",
            "category": "hvac",
            "frequency": "quarterly",
            "interval_days": 90,
            "asset_id": asset_id,
            "next_due_date": "2026-12-01",
        },
    )
    assert res_prev.status_code == 201
    assert res_prev.json()["task_id"] is not None


def test_api_cost_and_operations_analysis_endpoints(client_env):
    client = client_env["client"]
    headers = client_env["headers"]
    prop_id = client_env["property_id"]

    # Cost analysis
    cost_res = client.get(f"/api/v1/properties/{prop_id}/maintenance/cost-analysis", headers=headers)
    assert cost_res.status_code == 200
    assert "total_work_orders" in cost_res.json()

    # Operations analysis
    ops_res = client.post(
        f"/api/v1/properties/{prop_id}/maintenance/operations-analysis",
        headers=headers,
        json={"lookback_days": 60},
    )
    assert ops_res.status_code == 200
    assert "open_work_orders_count" in ops_res.json()


# =============================================================================
# 6. Scenarios Audit Tests (Edge cases, security, boundaries, integrity)
# =============================================================================

def test_operations_analysis_empty_property(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    repo.ensure_property(property_id="prop-empty", name="Empty Manor")
    ops = agent.analyze_operations(property_id="prop-empty")
    assert ops.open_work_orders_count == 0
    assert ops.emergency_count == 0
    assert ops.overdue_work_orders_count == 0
    assert ops.preventive_tasks_due_count == 0
    assert ops.assets_tracked_count == 0
    assert len(ops.findings) == 0

    cost = agent.analyze_costs(property_id="prop-empty")
    assert cost.total_work_orders == 0
    assert cost.completed_work_orders == 0
    assert cost.total_estimated_spend == Decimal("0.00")
    assert cost.total_actual_spend == Decimal("0.00")
    assert cost.budget_variance == Decimal("0.00")


def test_operations_analysis_today_overdue_emergency(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    unit_id = repo.list_units("prop-test")[0].unit_id
    # Work order created 6 hours ago today: Emergency SLA is 4 hours
    six_hours_ago = datetime.now(timezone.utc) - timedelta(hours=6)
    wo = repo.create_work_order(
        property_id="prop-test",
        data=WorkOrderCreate(
            unit_id=unit_id,
            title="Gas Odor in Hallway",
            description="Strong gas odor near apartment 101",
            priority=WorkOrderPriority.EMERGENCY,
            status=WorkOrderStatus.OPEN,
        ),
    )
    # Manually backdate created_at in database to simulate 6 hours ago
    with repo._connect() as conn:
        conn.execute("UPDATE work_orders SET created_at = ? WHERE work_order_id = ?", (six_hours_ago.isoformat(), wo.work_order_id))

    # Run analysis without specifying as_of_date (defaults to current time)
    ops = agent.analyze_operations(property_id="prop-test")
    assert ops.emergency_count >= 1
    assert ops.overdue_work_orders_count >= 1
    overdue_finding = next((f for f in ops.findings if f.finding_type == MaintenanceFindingType.OVERDUE_WORK_ORDER), None)
    assert overdue_finding is not None
    assert overdue_finding.severity == "critical"


def test_operations_analysis_completed_not_flagged_overdue(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    unit_id = repo.list_units("prop-test")[0].unit_id
    ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
    wo = repo.create_work_order(
        property_id="prop-test",
        data=WorkOrderCreate(
            unit_id=unit_id,
            title="Old Completed Plumbing",
            description="Leaky faucet fixed last week",
            priority=WorkOrderPriority.HIGH,
            status=WorkOrderStatus.COMPLETED,
        ),
    )
    with repo._connect() as conn:
        conn.execute("UPDATE work_orders SET created_at = ? WHERE work_order_id = ?", (ten_days_ago.isoformat(), wo.work_order_id))

    ops = agent.analyze_operations(property_id="prop-test")
    assert not any(wo.work_order_id in f.entity_ids for f in ops.findings if f.finding_type == MaintenanceFindingType.OVERDUE_WORK_ORDER)


def test_operations_analysis_sla_boundary_conditions(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    unit_id = repo.list_units("prop-test")[0].unit_id
    now = datetime.now(timezone.utc)
    # High priority SLA is 24 hours:
    # 23 hours ago should NOT be overdue
    wo_not_overdue = repo.create_work_order(
        property_id="prop-test",
        data=WorkOrderCreate(
            unit_id=unit_id,
            title="Urgent heater check",
            description="Heater running cold",
            priority=WorkOrderPriority.HIGH,
            status=WorkOrderStatus.OPEN,
        ),
    )
    with repo._connect() as conn:
        conn.execute("UPDATE work_orders SET created_at = ? WHERE work_order_id = ?", ((now - timedelta(hours=23)).isoformat(), wo_not_overdue.work_order_id))

    ops = agent.analyze_operations(property_id="prop-test")
    assert not any(wo_not_overdue.work_order_id in f.entity_ids for f in ops.findings if f.finding_type == MaintenanceFindingType.OVERDUE_WORK_ORDER)

    # 25 hours ago SHOULD be overdue
    wo_overdue = repo.create_work_order(
        property_id="prop-test",
        data=WorkOrderCreate(
            unit_id=unit_id,
            title="Urgent window replacement",
            description="Broken glass",
            priority=WorkOrderPriority.HIGH,
            status=WorkOrderStatus.OPEN,
        ),
    )
    with repo._connect() as conn:
        conn.execute("UPDATE work_orders SET created_at = ? WHERE work_order_id = ?", ((now - timedelta(hours=25)).isoformat(), wo_overdue.work_order_id))

    ops2 = agent.analyze_operations(property_id="prop-test")
    assert any(wo_overdue.work_order_id in f.entity_ids for f in ops2.findings if f.finding_type == MaintenanceFindingType.OVERDUE_WORK_ORDER)


def test_operations_analysis_warranty_expired_finding(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    asset = repo.create_asset(
        property_id="prop-test",
        data=AssetCreate(
            name="Ancient Boiler",
            asset_category=AssetCategory.HVAC,
            serial_number="BLR-OLD-1999",
            warranty_expiry_date=date.today() - timedelta(days=45),
        ),
    )
    ops = agent.analyze_operations(property_id="prop-test")
    expired_finding = next((f for f in ops.findings if f.finding_type == MaintenanceFindingType.WARRANTY_EXPIRED), None)
    assert expired_finding is not None
    assert asset.asset_id in expired_finding.entity_ids
    assert "expired 45 days ago" in expired_finding.description


def test_operations_analysis_read_only_does_not_mutate(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    wos_before = repo.list_work_orders(property_id="prop-test")
    agent.analyze_operations(property_id="prop-test")
    agent.analyze_costs(property_id="prop-test")
    wos_after = repo.list_work_orders(property_id="prop-test")
    assert [w.model_dump() for w in wos_before] == [w.model_dump() for w in wos_after]


def test_api_cross_property_unit_rejected(client_env) -> None:
    client = client_env["client"]
    headers = client_env["headers"]
    prop_id = client_env["property_id"]
    domain_repo = client_env["domain_repo"]

    # Create another property and foreign unit
    domain_repo.ensure_property(property_id="prop-other", name="Foreign Property")
    foreign_unit = domain_repo.create_unit("prop-other", UnitCreate(unit_number="999"))

    res = client.post(
        f"/api/v1/properties/{prop_id}/maintenance/auto-dispatch",
        headers=headers,
        json={
            "unit_id": foreign_unit.unit_id,
            "title": "Cross property attempt",
            "description": "Leaky roof",
        },
    )
    assert res.status_code == 400
    assert "belongs to property 'prop-other'" in res.json()["detail"]


def test_api_triage_property_mismatch_blocked(client_env) -> None:
    client = client_env["client"]
    headers = client_env["headers"]

    # Attempt to triage on a property different from the authenticated manager's token
    res = client.post(
        "/api/v1/properties/prop-other-unauthorized/maintenance/triage",
        headers=headers,
        json={"description": "Gas smell in hallway"},
    )
    assert res.status_code in (403, 404)


def test_api_nonexistent_work_order_fails(client_env) -> None:
    client = client_env["client"]
    headers = client_env["headers"]
    prop_id = client_env["property_id"]

    res = client.post(
        f"/api/v1/properties/{prop_id}/maintenance/auto-dispatch",
        headers=headers,
        json={"work_order_id": "wo-does-not-exist"},
    )
    assert res.status_code == 400
    assert "not found" in res.json()["detail"].lower()


def test_maintenance_audit_trail_hash_chain_verified(client_env) -> None:
    audit = client_env["audit"]
    # Verify that the audit trail hash chain for maintenance operations is unbroken
    result = audit.verify_chain()
    assert result.valid is True
    assert result.events_checked >= 0


def test_deterministic_behavior_without_llm(agent: MaintenanceAgent) -> None:
    req = MaintenanceTriageRequest(description="Water leaking from sink drain pipe in bathroom")
    res1 = agent.triage_request(req)
    res2 = agent.triage_request(req)
    assert res1.model_dump() == res2.model_dump()
    assert res1.category == MaintenanceCategory.PLUMBING
    assert res1.suggested_vendor_category == VendorCategory.PLUMBING


# =============================================================================
# 7. Duplicate & Recurring Work Order Intelligence Tests
# =============================================================================

def test_duplicate_work_orders_detected_for_same_unit(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    """1. Two active work orders with the same unit and normalized issue produce a DUPLICATE_WORK_ORDER finding."""
    unit_id = repo.list_units("prop-test")[0].unit_id
    wo1 = repo.create_work_order(
        property_id="prop-test",
        data=WorkOrderCreate(
            unit_id=unit_id,
            title="Leaking sink faucet",
            description="Kitchen sink faucet dripping water",
            priority=WorkOrderPriority.MEDIUM,
            status=WorkOrderStatus.OPEN,
        ),
    )
    wo2 = repo.create_work_order(
        property_id="prop-test",
        data=WorkOrderCreate(
            unit_id=unit_id,
            title="Repair leaking sink faucet",
            description="Please fix kitchen sink faucet dripping water",
            priority=WorkOrderPriority.MEDIUM,
            status=WorkOrderStatus.ASSIGNED,
        ),
    )

    ops = agent.analyze_operations(property_id="prop-test")
    dup_findings = [f for f in ops.findings if f.finding_type == MaintenanceFindingType.DUPLICATE_WORK_ORDER]
    assert len(dup_findings) >= 1
    finding = dup_findings[0]
    assert wo1.work_order_id in finding.entity_ids
    assert wo2.work_order_id in finding.entity_ids


def test_unrelated_work_orders_do_not_produce_duplicate_finding(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    """2. Two unrelated work orders do not produce a duplicate finding."""
    units = repo.list_units("prop-test")
    unit1_id = units[0].unit_id
    unit2_id = units[1].unit_id

    repo.create_work_order(
        property_id="prop-test",
        data=WorkOrderCreate(
            unit_id=unit1_id,
            title="Leaking kitchen sink faucet",
            description="Faucet dripping in kitchen",
            priority=WorkOrderPriority.LOW,
            status=WorkOrderStatus.OPEN,
        ),
    )
    repo.create_work_order(
        property_id="prop-test",
        data=WorkOrderCreate(
            unit_id=unit2_id,
            title="Bedroom broken window latch",
            description="Window latch is stuck and won't lock",
            priority=WorkOrderPriority.LOW,
            status=WorkOrderStatus.OPEN,
        ),
    )

    ops = agent.analyze_operations(property_id="prop-test")
    dup_findings = [f for f in ops.findings if f.finding_type == MaintenanceFindingType.DUPLICATE_WORK_ORDER]
    assert len(dup_findings) == 0


def test_recurring_unit_issues_detected_within_lookback(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    """3. Three matching work orders for the same unit within the lookback window produce a RECURRING_UNIT_ISSUE finding."""
    unit_id = repo.list_units("prop-test")[0].unit_id
    now = datetime.now(timezone.utc)

    wo_ids = []
    for days_ago in [5, 15, 30]:
        created_time = now - timedelta(days=days_ago)
        wo = repo.create_work_order(
            property_id="prop-test",
            data=WorkOrderCreate(
                unit_id=unit_id,
                title="Running toilet in master bathroom",
                description="Toilet tank valve running continuously",
                priority=WorkOrderPriority.MEDIUM,
                status=WorkOrderStatus.COMPLETED,
            ),
        )
        with repo._connect() as conn:
            conn.execute("UPDATE work_orders SET created_at = ? WHERE work_order_id = ?", (created_time.isoformat(), wo.work_order_id))
        wo_ids.append(wo.work_order_id)

    ops = agent.analyze_operations(property_id="prop-test", request=MaintenanceOperationsAnalysisRequest(lookback_days=90))
    recurring_findings = [f for f in ops.findings if f.finding_type == MaintenanceFindingType.RECURRING_UNIT_ISSUE]
    assert len(recurring_findings) >= 1
    finding = recurring_findings[0]
    for wid in wo_ids:
        assert wid in finding.entity_ids


def test_work_orders_outside_lookback_window_do_not_count_toward_recurring(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    """4. Work orders outside the lookback window do not count toward recurring issues."""
    unit_id = repo.list_units("prop-test")[0].unit_id
    now = datetime.now(timezone.utc)

    # 2 within lookback window (10, 20 days ago), 1 outside lookback window (120 days ago)
    for days_ago in [10, 20, 120]:
        created_time = now - timedelta(days=days_ago)
        wo = repo.create_work_order(
            property_id="prop-test",
            data=WorkOrderCreate(
                unit_id=unit_id,
                title="Garbage disposal humming stuck",
                description="Kitchen disposal motor hums but blade jammed",
                priority=WorkOrderPriority.LOW,
                status=WorkOrderStatus.COMPLETED,
            ),
        )
        with repo._connect() as conn:
            conn.execute("UPDATE work_orders SET created_at = ? WHERE work_order_id = ?", (created_time.isoformat(), wo.work_order_id))

    ops = agent.analyze_operations(property_id="prop-test", request=MaintenanceOperationsAnalysisRequest(lookback_days=90))
    recurring_findings = [f for f in ops.findings if f.finding_type == MaintenanceFindingType.RECURRING_UNIT_ISSUE]
    assert len(recurring_findings) == 0


def test_operations_analysis_includes_records_beyond_first_one_thousand(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    """5. Analysis includes records beyond the first 1,000."""
    unit_id = repo.list_units("prop-test")[0].unit_id
    now_iso = datetime.now(timezone.utc).isoformat()

    rows = [
        (
            f"wo-bulk-{i}",
            "prop-test",
            unit_id,
            None,
            None,
            f"Bulk work order item {i}",
            f"Description for work order {i}",
            WorkOrderPriority.LOW.value,
            WorkOrderStatus.OPEN.value,
            None,
            None,
            now_iso,
            now_iso,
        )
        for i in range(1050)
    ]
    with repo._connect() as conn:
        conn.executemany(
            """INSERT INTO work_orders (
                work_order_id, property_id, unit_id, resident_id, vendor_id,
                title, description, priority, status, estimated_cost, actual_cost,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    ops = agent.analyze_operations(property_id="prop-test")
    assert ops.open_work_orders_count == 1050


def test_duplicate_and_recurring_findings_do_not_modify_records(repo: DomainRepository, agent: MaintenanceAgent) -> None:
    """6. Duplicate and recurring findings do not modify any work-order records."""
    unit_id = repo.list_units("prop-test")[0].unit_id
    now = datetime.now(timezone.utc)

    for i in range(3):
        wo = repo.create_work_order(
            property_id="prop-test",
            data=WorkOrderCreate(
                unit_id=unit_id,
                title="Clogged bathroom shower drain",
                description="Shower drain backed up with standing water",
                priority=WorkOrderPriority.MEDIUM,
                status=WorkOrderStatus.OPEN,
            ),
        )
        created_time = now - timedelta(days=i * 2 + 1)
        with repo._connect() as conn:
            conn.execute("UPDATE work_orders SET created_at = ? WHERE work_order_id = ?", (created_time.isoformat(), wo.work_order_id))

    wos_before = repo.list_work_orders(property_id="prop-test")
    assert len(wos_before) == 3

    ops = agent.analyze_operations(property_id="prop-test", request=MaintenanceOperationsAnalysisRequest(lookback_days=30))
    dup_findings = [f for f in ops.findings if f.finding_type == MaintenanceFindingType.DUPLICATE_WORK_ORDER]
    rec_findings = [f for f in ops.findings if f.finding_type == MaintenanceFindingType.RECURRING_UNIT_ISSUE]
    assert len(dup_findings) >= 1
    assert len(rec_findings) >= 1

    wos_after = repo.list_work_orders(property_id="prop-test")
    assert [w.model_dump() for w in wos_before] == [w.model_dump() for w in wos_after]



