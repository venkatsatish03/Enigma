from datetime import datetime, timezone

import pytest

from repository.domain_repository import DomainRepository
from repository.exceptions import (
    EntityNotFoundError,
    InvalidOperationError,
)
from schemas.domain import (
    MaintenanceRequestCreate,
    MaintenanceRequestStatus,
    MaintenanceRequestUpdate,
    UnitCreate,
    WorkOrderCreate,
    WorkOrderPriority,
)


@pytest.fixture
def maintenance_repo(tmp_path):
    repo = DomainRepository(tmp_path / "maintenance_requests.sqlite3")
    repo.create_property(
        property_id="prop-test",
        name="Maintenance Test Property",
    )
    unit = repo.create_unit(
        "prop-test",
        UnitCreate(unit_number="101"),
    )
    return repo, unit


def test_create_and_get_maintenance_request(maintenance_repo):
    repo, unit = maintenance_repo

    created = repo.create_maintenance_request(
        "prop-test",
        MaintenanceRequestCreate(
            unit_id=unit.unit_id,
            description="Water leaking from the kitchen sink.",
        ),
    )

    assert created.status == MaintenanceRequestStatus.SUBMITTED
    assert created.property_id == "prop-test"
    assert created.unit_id == unit.unit_id

    fetched = repo.get_maintenance_request(
        "prop-test", created.request_id
    )

    assert fetched is not None
    assert fetched.request_id == created.request_id
    assert fetched.description == created.description


def test_list_maintenance_requests_is_property_scoped(
    maintenance_repo,
):
    repo, unit = maintenance_repo

    created = repo.create_maintenance_request(
        "prop-test",
        MaintenanceRequestCreate(
            unit_id=unit.unit_id,
            description="Broken bathroom tap.",
        ),
    )

    results = repo.list_maintenance_requests(
        property_id="prop-test",
        limit=10,
        offset=0,
    )

    assert [item.request_id for item in results] == [
        created.request_id
    ]
    with pytest.raises(EntityNotFoundError):
        repo.list_maintenance_requests(
            property_id="missing-property"
        )


def test_request_status_transition_and_invalid_transition(
    maintenance_repo,
):
    repo, unit = maintenance_repo

    request = repo.create_maintenance_request(
        "prop-test",
        MaintenanceRequestCreate(
            unit_id=unit.unit_id,
            description="Heating is not working.",
        ),
    )

    triaged = repo.update_maintenance_request(
        "prop-test",
        request.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
            priority=WorkOrderPriority.HIGH,
            category="hvac",
            estimated_sla_hours=24,
        ),
    )

    assert triaged.status == MaintenanceRequestStatus.TRIAGED
    assert triaged.priority == WorkOrderPriority.HIGH
    assert triaged.estimated_sla_hours == 24

    with pytest.raises(InvalidOperationError):
        repo.update_maintenance_request(
            "prop-test",
            request.request_id,
            MaintenanceRequestUpdate(
                status=MaintenanceRequestStatus.CLOSED,
            ),
        )

    unchanged = repo.get_maintenance_request(
        "prop-test", request.request_id
    )
    assert unchanged.status == MaintenanceRequestStatus.TRIAGED


def test_request_can_link_to_existing_work_order(
    maintenance_repo,
):
    repo, unit = maintenance_repo

    request = repo.create_maintenance_request(
        "prop-test",
        MaintenanceRequestCreate(
            unit_id=unit.unit_id,
            description="Kitchen sink pipe leaking.",
        ),
    )

    work_order = repo.create_work_order(
        "prop-test",
        WorkOrderCreate(
            unit_id=unit.unit_id,
            title="Repair kitchen sink",
            description="Repair leaking kitchen sink pipe.",
            priority=WorkOrderPriority.MEDIUM,
        ),
    )

    converted = repo.update_maintenance_request(
        "prop-test",
        request.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.TRIAGED,
        ),
    )

    converted = repo.update_maintenance_request(
        "prop-test",
        request.request_id,
        MaintenanceRequestUpdate(
            status=MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER,
            work_order_id=work_order.work_order_id,
        ),
    )

    assert (
        converted.status
        == MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER
    )
    assert converted.work_order_id == work_order.work_order_id


def test_unknown_request_returns_not_found(maintenance_repo):
    repo, _ = maintenance_repo

    with pytest.raises(EntityNotFoundError):
        repo.update_maintenance_request(
            "prop-test",
            "does-not-exist",
            MaintenanceRequestUpdate(
                status=MaintenanceRequestStatus.CANCELLED,
            ),
        )