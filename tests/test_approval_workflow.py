
from datetime import datetime, timedelta, timezone

import pytest

from schemas.approvals import ApprovalDecision, ApprovalRequestCreate
from schemas.audit import AuditEventCreate
from tools.approval_workflow import (
    ApprovalAuthorizationError,
    ApprovalNotFoundError,
    ApprovalStateError,
    ApprovalWorkflow,
)
from tools.audit_trail import AuditTrail


@pytest.fixture
def setup_workflow(tmp_path):
    audit = AuditTrail(tmp_path / "audit.sqlite3")
    workflow = ApprovalWorkflow(
        tmp_path / "approvals.sqlite3",
        audit,
    )
    return workflow, audit


def make_request(
    *,
    action_id="action-1",
    requested_by="agent-finance",
    approver_ids=None,
    expires_in_minutes=30,
):
    return ApprovalRequestCreate(
        action_id=action_id,
        action_type="propose_payment",
        requested_by=requested_by,
        approver_ids=approver_ids or ["manager-1"],
        rationale="Invoice payment requires review.",
        amount="250.00",
        currency="usd",
        evidence_ids=["invoice-evidence-1"],
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=expires_in_minutes),
        correlation_id="workflow-1",
    )


def test_create_request_persists_pending_approval(setup_workflow):
    workflow, _ = setup_workflow

    created = workflow.create_request(make_request())
    loaded = workflow.get_request(created.approval_id)

    assert created.status == "pending"
    assert loaded.approval_id == created.approval_id
    assert loaded.action_id == "action-1"
    assert loaded.currency == "USD"
    assert loaded.approver_ids == ["manager-1"]


def test_request_creation_writes_audit_event(setup_workflow):
    workflow, audit = setup_workflow

    created = workflow.create_request(make_request())

    events = audit.list_events(
        entity_type="approval",
        entity_id=created.approval_id,
    )
    assert len(events) == 1
    assert events[0].event_type == "approval.requested"


def test_unauthorized_user_cannot_decide_request(setup_workflow):
    workflow, _ = setup_workflow
    created = workflow.create_request(make_request())

    with pytest.raises(ApprovalAuthorizationError):
        workflow.decide(
            ApprovalDecision(
                approval_id=created.approval_id,
                approver_id="intruder",
                decision="approve",
                reason="Attempted approval",
            )
        )

    assert workflow.get_request(created.approval_id).status == "pending"


def test_requester_cannot_approve_own_request(setup_workflow):
    workflow, _ = setup_workflow

    with pytest.raises(ValueError, match="requester cannot be an approver"):
        workflow.create_request(
            make_request(
                requested_by="manager-1",
                approver_ids=["manager-1", "manager-2"],
            )
        )


def test_requester_cannot_be_listed_as_approver(setup_workflow):
    workflow, _ = setup_workflow

    with pytest.raises(ValueError, match="cannot be an approver"):
        workflow.create_request(
            make_request(
                requested_by="manager-1",
                approver_ids=["manager-1"],
            )
        )


def test_authorized_approver_can_approve(setup_workflow):
    workflow, audit = setup_workflow
    created = workflow.create_request(make_request())

    result = workflow.decide(
        ApprovalDecision(
            approval_id=created.approval_id,
            approver_id="manager-1",
            decision="approve",
            reason="Reviewed invoice and evidence.",
        )
    )

    assert result.approval.status == "approved"
    assert result.approval.decided_by == "manager-1"
    assert result.approval.decision_reason == "Reviewed invoice and evidence."

    events = audit.list_events(
        entity_type="approval",
        entity_id=created.approval_id,
    )
    assert [event.event_type for event in events] == [
        "approval.requested",
        "approval.approved",
    ]


def test_authorized_approver_can_reject(setup_workflow):
    workflow, audit = setup_workflow
    created = workflow.create_request(make_request())

    result = workflow.decide(
        ApprovalDecision(
            approval_id=created.approval_id,
            approver_id="manager-1",
            decision="reject",
            reason="Invoice amount does not match supporting evidence.",
        )
    )

    assert result.approval.status == "rejected"
    assert result.approval.decided_by == "manager-1"

    events = audit.list_events(entity_id=created.approval_id)
    assert events[-1].event_type == "approval.rejected"


def test_decision_requires_a_reason():
    with pytest.raises(ValueError):
        ApprovalDecision(
            approval_id="approval-1",
            approver_id="manager-1",
            decision="approve",
            reason="   ",
        )


def test_approved_request_cannot_be_decided_again(setup_workflow):
    workflow, _ = setup_workflow
    created = workflow.create_request(make_request())

    workflow.decide(
        ApprovalDecision(
            approval_id=created.approval_id,
            approver_id="manager-1",
            decision="approve",
            reason="Approved after review.",
        )
    )

    with pytest.raises(ApprovalStateError):
        workflow.decide(
            ApprovalDecision(
                approval_id=created.approval_id,
                approver_id="manager-1",
                decision="reject",
                reason="Trying to reverse decision.",
            )
        )


def test_expired_request_cannot_be_approved(setup_workflow):
    workflow, audit = setup_workflow
    created = workflow.create_request(make_request())

    # Simulate time passing without relying on sleeps.
    with workflow._connect() as connection:
        connection.execute(
            "UPDATE approval_requests SET expires_at = ? WHERE approval_id = ?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                created.approval_id,
            ),
        )

    result = workflow.decide(
        ApprovalDecision(
            approval_id=created.approval_id,
            approver_id="manager-1",
            decision="approve",
            reason="Late approval attempt.",
        )
    )

    assert result.approval.status == "expired"
    assert result.approval.decided_by is None

    events = audit.list_events(entity_id=created.approval_id)
    assert events[-1].event_type == "approval.expired"


def test_request_with_past_expiration_is_rejected(setup_workflow):
    workflow, _ = setup_workflow

    request = make_request(expires_in_minutes=-1)
    with pytest.raises(ValueError, match="future"):
        workflow.create_request(request)


def test_naive_expiration_datetime_is_rejected(setup_workflow):
    workflow, _ = setup_workflow

    request = make_request()
    request.expires_at = datetime.now() + timedelta(minutes=30)

    with pytest.raises(ValueError, match="timezone"):
        workflow.create_request(request)


def test_unknown_request_raises_not_found(setup_workflow):
    workflow, _ = setup_workflow

    with pytest.raises(ApprovalNotFoundError):
        workflow.get_request("unknown-approval")


def test_list_requests_can_filter_by_status_and_action(setup_workflow):
    workflow, _ = setup_workflow

    first = workflow.create_request(make_request(action_id="action-1"))
    workflow.create_request(make_request(action_id="action-2"))

    workflow.decide(
        ApprovalDecision(
            approval_id=first.approval_id,
            approver_id="manager-1",
            decision="approve",
            reason="Approved.",
        )
    )

    pending = workflow.list_requests(status="pending")
    approved_action = workflow.list_requests(
        status="approved",
        action_id="action-1",
    )

    assert len(pending) == 1
    assert pending[0].action_id == "action-2"
    assert len(approved_action) == 1
    assert approved_action[0].approval_id == first.approval_id


def test_approval_database_and_audit_database_are_separate(setup_workflow):
    workflow, audit = setup_workflow
    created = workflow.create_request(make_request())

    # Approval records persist independently; audit events remain available.
    assert workflow.get_request(created.approval_id).status == "pending"
    assert audit.verify_chain().valid is True
    assert audit.list_events(entity_id=created.approval_id)
