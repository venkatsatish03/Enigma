
import sqlite3

import pytest

from schemas.audit import AuditEventCreate
from tools.audit_trail import AuditTrail, GENESIS_HASH


@pytest.fixture
def audit(tmp_path):
    return AuditTrail(tmp_path / "audit.sqlite3")


def make_event(
    *,
    event_type="document.processed",
    entity_type="document",
    entity_id="doc-1",
    actor_id="agent-document",
    payload=None,
    evidence_ids=None,
    correlation_id="run-1",
):
    return AuditEventCreate(
        actor_id=actor_id,
        actor_type="agent",
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
        evidence_ids=evidence_ids or [],
        correlation_id=correlation_id,
    )


def test_append_persists_event(audit):
    event = audit.append(
        make_event(payload={"status": "completed", "pages": 3})
    )

    assert event.sequence_number == 1
    assert event.event_type == "document.processed"
    assert event.payload == {"status": "completed", "pages": 3}
    assert event.previous_hash == GENESIS_HASH
    assert len(event.event_hash) == 64


def test_events_survive_repository_recreation(tmp_path):
    database_path = tmp_path / "audit.sqlite3"
    first_repository = AuditTrail(database_path)
    first_repository.append(make_event(entity_id="invoice-1"))

    second_repository = AuditTrail(database_path)
    events = second_repository.list_events()

    assert len(events) == 1
    assert events[0].entity_id == "invoice-1"


def test_hash_chain_is_valid_after_multiple_events(audit):
    audit.append(make_event(entity_id="doc-1"))
    audit.append(
        make_event(
            event_type="financial.reconciled",
            entity_type="operation",
            entity_id="operation-1",
        )
    )

    result = audit.verify_chain()

    assert result.valid is True
    assert result.events_checked == 2
    assert result.first_invalid_sequence is None


def test_event_links_to_previous_event_hash(audit):
    first = audit.append(make_event(entity_id="doc-1"))
    second = audit.append(make_event(entity_id="doc-2"))

    assert second.previous_hash == first.event_hash


def test_entity_filter_returns_only_matching_events(audit):
    audit.append(make_event(entity_id="doc-1"))
    audit.append(make_event(entity_id="doc-2"))
    audit.append(make_event(entity_id="doc-1"))

    events = audit.list_events(entity_type="document", entity_id="doc-1")

    assert len(events) == 2
    assert all(event.entity_id == "doc-1" for event in events)


def test_correlation_filter_returns_related_events(audit):
    audit.append(make_event(correlation_id="run-A"))
    audit.append(make_event(correlation_id="run-B"))
    audit.append(make_event(correlation_id="run-A"))

    events = audit.list_events(correlation_id="run-A")

    assert len(events) == 2
    assert all(event.correlation_id == "run-A" for event in events)


def test_events_are_returned_in_sequence_order(audit):
    first = audit.append(make_event(entity_id="doc-1"))
    second = audit.append(make_event(entity_id="doc-2"))
    third = audit.append(make_event(entity_id="doc-3"))

    events = audit.list_events()

    assert [event.event_id for event in events] == [
        first.event_id,
        second.event_id,
        third.event_id,
    ]


def test_update_is_blocked_by_sqlite_trigger(audit):
    event = audit.append(make_event())

    with sqlite3.connect(audit.database_path) as connection:
        with pytest.raises(
            sqlite3.IntegrityError, match="Audit events are append-only"
        ):
            connection.execute(
                "UPDATE audit_events SET entity_id = ? WHERE event_id = ?",
                ("changed", event.event_id),
            )


def test_delete_is_blocked_by_sqlite_trigger(audit):
    event = audit.append(make_event())

    with sqlite3.connect(audit.database_path) as connection:
        with pytest.raises(
            sqlite3.IntegrityError, match="Audit events are append-only"
        ):
            connection.execute(
                "DELETE FROM audit_events WHERE event_id = ?",
                (event.event_id,),
            )


def test_modified_payload_is_detected_by_hash_verification(audit):
    audit.append(make_event(payload={"status": "completed"}))

    # Simulate tampering that bypasses the normal append-only API.
    with sqlite3.connect(audit.database_path) as connection:
        connection.execute("DROP TRIGGER audit_events_no_update")
        connection.execute(
            "UPDATE audit_events SET payload_json = ? WHERE sequence_number = 1",
            ('{"status":"changed"}',),
        )

    result = audit.verify_chain()

    assert result.valid is False
    assert result.first_invalid_sequence == 1
    assert "hash" in result.message.lower()


def test_modified_chain_link_is_detected(audit):
    audit.append(make_event(entity_id="doc-1"))
    audit.append(make_event(entity_id="doc-2"))

    with sqlite3.connect(audit.database_path) as connection:
        connection.execute("DROP TRIGGER audit_events_no_update")
        connection.execute(
            "UPDATE audit_events SET previous_hash = ? WHERE sequence_number = 2",
            ("f" * 64,),
        )

    result = audit.verify_chain()

    assert result.valid is False
    assert result.first_invalid_sequence == 2


def test_invalid_limit_is_rejected(audit):
    with pytest.raises(ValueError, match="limit"):
        audit.list_events(limit=0)


def test_evidence_ids_and_correlation_id_are_preserved(audit):
    event = audit.append(
        make_event(
            evidence_ids=["evidence-1", "evidence-2"],
            correlation_id="workflow-123",
        )
    )

    loaded = audit.list_events()[0]

    assert loaded.evidence_ids == ["evidence-1", "evidence-2"]
    assert loaded.correlation_id == "workflow-123"
