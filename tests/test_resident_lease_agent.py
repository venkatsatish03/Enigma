
from datetime import date, datetime
from unittest.mock import Mock

from agents.resident_lease_agent import ResidentLeaseAgent
from schemas.domain import LeaseStatus, ResidentStatus
from schemas.resident_lease_agent import (
    FindingType,
    ResidentLeaseAnalysisRequest,
)


def make_resident(
    resident_id: str,
    email: str,
    first_name: str = "Alex",
    last_name: str = "Smith",
):
    return Mock(
        resident_id=resident_id,
        property_id="property-1",
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=None,
        status=ResidentStatus.ACTIVE,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


def make_lease(
    lease_id: str,
    resident_id: str,
    unit_id: str,
    start_date: date,
    end_date: date,
    status: LeaseStatus = LeaseStatus.ACTIVE,
):
    return Mock(
        lease_id=lease_id,
        property_id="property-1",
        resident_id=resident_id,
        unit_id=unit_id,
        start_date=start_date,
        end_date=end_date,
        status=status,
    )


def make_agent(residents=None, leases=None):
    repo = Mock()
    repo.list_residents.return_value = residents or []
    repo.list_leases.return_value = leases or []
    return ResidentLeaseAgent(repo)


def analyze(agent, as_of=date(2026, 10, 19), window=60):
    return agent.analyze(
        "property-1",
        ResidentLeaseAnalysisRequest(
            as_of_date=as_of,
            expiration_window_days=window,
        ),
    )


def test_detects_duplicate_resident_emails_case_insensitively():
    agent = make_agent(
        residents=[
            make_resident("r1", "Alex@example.com"),
            make_resident("r2", "alex@EXAMPLE.com"),
        ]
    )

    result = analyze(agent)

    findings = [
        f for f in result.findings
        if f.finding_type == FindingType.DUPLICATE_RESIDENT
    ]
    assert len(findings) == 1
    assert set(findings[0].entity_ids) == {"r1", "r2"}


def test_detects_incomplete_resident_fields():
    agent = make_agent(
        residents=[
            make_resident("r1", " ", first_name=""),
        ]
    )

    result = analyze(agent)

    findings = [
        f for f in result.findings
        if f.finding_type == FindingType.INCOMPLETE_RESIDENT
    ]
    assert len(findings) == 1
    assert "first_name" in findings[0].evidence[0]
    assert "email" in findings[0].evidence[0]


def test_detects_active_lease_past_end_date():
    agent = make_agent(
        leases=[
            make_lease(
                "l1", "r1", "u1",
                date(2025, 1, 1), date(2026, 10, 18),
            )
        ]
    )

    result = analyze(agent)

    assert any(
        f.finding_type == FindingType.EXPIRED_ACTIVE_LEASE
        for f in result.findings
    )


def test_lease_ending_today_is_not_expired():
    agent = make_agent(
        leases=[
            make_lease(
                "l1", "r1", "u1",
                date(2026, 1, 1), date(2026, 10, 19),
            )
        ]
    )

    result = analyze(agent)

    assert not any(
        f.finding_type == FindingType.EXPIRED_ACTIVE_LEASE
        for f in result.findings
    )


def test_detects_lease_expiring_inside_window():
    agent = make_agent(
        leases=[
            make_lease(
                "l1", "r1", "u1",
                date(2026, 1, 1), date(2026, 11, 18),
            )
        ]
    )

    result = analyze(agent, window=30)

    assert any(
        f.finding_type == FindingType.UPCOMING_LEASE_EXPIRATION
        for f in result.findings
    )


def test_does_not_flag_expiration_beyond_window():
    agent = make_agent(
        leases=[
            make_lease(
                "l1", "r1", "u1",
                date(2026, 1, 1), date(2027, 1, 1),
            )
        ]
    )

    result = analyze(agent, window=30)

    assert not any(
        f.finding_type == FindingType.UPCOMING_LEASE_EXPIRATION
        for f in result.findings
    )


def test_detects_overlapping_leases_for_same_unit():
    agent = make_agent(
        leases=[
            make_lease(
                "l1", "r1", "u1",
                date(2026, 1, 1), date(2026, 12, 31),
            ),
            make_lease(
                "l2", "r2", "u1",
                date(2026, 10, 1), date(2027, 9, 30),
            ),
        ]
    )

    result = analyze(agent)

    assert any(
        f.finding_type == FindingType.OVERLAPPING_UNIT_LEASES
        for f in result.findings
    )


def test_does_not_flag_non_overlapping_unit_leases():
    agent = make_agent(
        leases=[
            make_lease(
                "l1", "r1", "u1",
                date(2026, 1, 1), date(2026, 6, 30),
            ),
            make_lease(
                "l2", "r2", "u1",
                date(2026, 7, 1), date(2026, 12, 31),
            ),
        ]
    )

    result = analyze(agent)

    assert not any(
        f.finding_type == FindingType.OVERLAPPING_UNIT_LEASES
        for f in result.findings
    )


def test_detects_overlapping_leases_for_same_resident():
    agent = make_agent(
        leases=[
            make_lease(
                "l1", "r1", "u1",
                date(2026, 1, 1), date(2026, 12, 31),
            ),
            make_lease(
                "l2", "r1", "u2",
                date(2026, 10, 1), date(2027, 9, 30),
            ),
        ]
    )

    result = analyze(agent)

    assert any(
        f.finding_type == FindingType.OVERLAPPING_RESIDENT_LEASES
        for f in result.findings
    )


def test_analysis_uses_property_scoped_repository_queries():
    agent = make_agent()

    result = analyze(agent)

    assert result.property_id == "property-1"
    agent.domain_repo.list_residents.assert_called_once_with(
        property_id="property-1", limit=1000, offset=0
    )
    agent.domain_repo.list_leases.assert_called_once_with(
        property_id="property-1", limit=1000, offset=0
    )


def test_rejects_empty_property_id():
    agent = make_agent()

    try:
        agent.analyze(" ")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "property_id" in str(exc)


def test_paginated_repository_counts_all_records_beyond_one_thousand():
    # 1,250 residents and 1,100 leases across multiple pages
    all_residents = [
        make_resident(f"res-{i}", f"resident_{i}@example.com")
        for i in range(1250)
    ]
    all_leases = [
        make_lease(
            lease_id=f"lease-{i}",
            resident_id=f"res-{i}",
            unit_id=f"unit-{i}",
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
        )
        for i in range(1100)
    ]

    repo = Mock()

    def mock_list_residents(property_id: str, limit: int = 1000, offset: int = 0):
        assert property_id == "property-1"
        return all_residents[offset : offset + limit]

    def mock_list_leases(property_id: str, limit: int = 1000, offset: int = 0):
        assert property_id == "property-1"
        return all_leases[offset : offset + limit]

    repo.list_residents.side_effect = mock_list_residents
    repo.list_leases.side_effect = mock_list_leases

    agent = ResidentLeaseAgent(repo)
    result = analyze(agent)

    assert result.residents_analyzed == 1250
    assert result.leases_analyzed == 1100
    assert repo.list_residents.call_count == 2
    assert repo.list_leases.call_count == 2

