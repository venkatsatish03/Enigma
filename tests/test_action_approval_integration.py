from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from schemas.approvals import ApprovalDecision
from tools.action_approval_service import submit_action_for_approval
from tools.action_policy_validator import PolicyStatus
from tools.approval_workflow import (
    ApprovalAuthorizationError,
    ApprovalWorkflow,
)
from tools.audit_trail import AuditTrail


@pytest.fixture
def workflow_setup(tmp_path):
    audit_db = tmp_path / "audit.sqlite3"
    approval_db = tmp_path / "approvals.sqlite3"
    audit = AuditTrail(audit_db)
    workflow = ApprovalWorkflow(approval_db, audit)
    return workflow, audit


def make_action(**overrides):
    values = {
        "action_id": "act-int-101",
        "action_type": "propose_payment",
        "status": "proposed",
        "amount": "350.00",
        "currency": "USD",
        "evidence": ["ev-invoice-101", "ev-bank-101"],
        "unresolved_issues": [],
        "record_ids": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_submit_eligible_action_persists_request_and_audit(workflow_setup):
    workflow, audit = workflow_setup
    action = make_action()
    correlation_id = "corr-run-101"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)

    submission = submit_action_for_approval(
        action,
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
        expires_at=expires_at,
        correlation_id=correlation_id,
    )

    assert submission.policy_result.status == PolicyStatus.ELIGIBLE
    assert submission.submitted is True
    assert submission.approval_request is not None

    approval_id = submission.approval_request.approval_id
    assert submission.approval_request.status == "pending"
    assert submission.approval_request.action_id == "act-int-101"
    assert submission.approval_request.correlation_id == correlation_id

    # Verify persistent storage
    loaded = workflow.get_request(approval_id)
    assert loaded.status == "pending"
    assert loaded.action_id == "act-int-101"
    assert loaded.requested_by == "analyst-1"
    assert loaded.approver_ids == ["manager-1"]
    assert loaded.correlation_id == correlation_id

    # Verify audit event
    events = audit.list_events(entity_type="approval", entity_id=approval_id)
    assert len(events) == 1
    assert events[0].event_type == "approval.requested"
    assert events[0].correlation_id == correlation_id

    # Verify tamper-evident audit hash chain
    verification = audit.verify_chain()
    assert verification.valid is True
    assert verification.events_checked == 1


def test_approve_request_updates_status_and_verifies_audit_chain(workflow_setup):
    workflow, audit = workflow_setup
    action = make_action()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)

    submission = submit_action_for_approval(
        action,
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
        expires_at=expires_at,
        correlation_id="corr-audit-202",
    )
    assert submission.submitted is True
    approval_id = submission.approval_request.approval_id

    decision_result = workflow.decide(
        ApprovalDecision(
            approval_id=approval_id,
            approver_id="manager-1",
            decision="approve",
            reason="Verified invoice match and debit transaction evidence.",
        )
    )

    assert decision_result.approval.status == "approved"
    assert decision_result.approval.decided_by == "manager-1"
    assert (
        decision_result.approval.decision_reason
        == "Verified invoice match and debit transaction evidence."
    )

    # Verify persisted state
    loaded = workflow.get_request(approval_id)
    assert loaded.status == "approved"
    assert loaded.decided_by == "manager-1"

    # Verify audit trail contains requested and approved events
    events = audit.list_events(entity_type="approval", entity_id=approval_id)
    assert [e.event_type for e in events] == [
        "approval.requested",
        "approval.approved",
    ]

    # Verify hash chain integrity across multiple chained events
    chain_result = audit.verify_chain()
    assert chain_result.valid is True
    assert chain_result.events_checked == 2
    assert chain_result.first_invalid_sequence is None


def test_reject_request_updates_status_and_preserves_audit_chain(workflow_setup):
    workflow, audit = workflow_setup
    action = make_action()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)

    submission = submit_action_for_approval(
        action,
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
        expires_at=expires_at,
    )
    approval_id = submission.approval_request.approval_id

    decision_result = workflow.decide(
        ApprovalDecision(
            approval_id=approval_id,
            approver_id="manager-1",
            decision="reject",
            reason="Supporting evidence is insufficient for payment.",
        )
    )

    assert decision_result.approval.status == "rejected"
    assert decision_result.approval.decided_by == "manager-1"
    assert workflow.get_request(approval_id).status == "rejected"

    events = audit.list_events(entity_type="approval", entity_id=approval_id)
    assert [e.event_type for e in events] == [
        "approval.requested",
        "approval.rejected",
    ]

    chain_result = audit.verify_chain()
    assert chain_result.valid is True
    assert chain_result.events_checked == 2


def test_blocked_actions_do_not_create_approval_requests(workflow_setup):
    workflow, audit = workflow_setup
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)

    # 1. Invalid amount (negative)
    action_invalid_amount = make_action(amount="-100.00")
    submission_amount = submit_action_for_approval(
        action_invalid_amount,
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
        expires_at=expires_at,
    )
    assert submission_amount.policy_result.status == PolicyStatus.BLOCKED
    assert submission_amount.submitted is False
    assert submission_amount.approval_request is None

    # 2. Duplicate payment finding
    action_with_dup = make_action(action_id="act-dup-1")
    dup_finding = {
        "finding_type": "duplicate_payment",
        "severity": "high",
        "status": "open",
    }
    submission_dup = submit_action_for_approval(
        action_with_dup,
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
        expires_at=expires_at,
        findings=[dup_finding],
    )
    assert submission_dup.policy_result.status == PolicyStatus.BLOCKED
    assert submission_dup.submitted is False
    assert submission_dup.approval_request is None

    # 3. Missing currency
    action_no_currency = make_action(currency="")
    submission_no_curr = submit_action_for_approval(
        action_no_currency,
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
        expires_at=expires_at,
    )
    assert submission_no_curr.policy_result.status == PolicyStatus.BLOCKED
    assert submission_no_curr.submitted is False
    assert submission_no_curr.approval_request is None

    # Verify no requests or audit events were stored for blocked actions
    assert workflow.list_requests() == []
    assert audit.list_events() == []
    assert audit.verify_chain().valid is True


def test_unauthorized_approver_cannot_decide_request(workflow_setup):
    workflow, audit = workflow_setup
    action = make_action()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)

    submission = submit_action_for_approval(
        action,
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
        expires_at=expires_at,
    )
    approval_id = submission.approval_request.approval_id

    # Unauthorized approver attempt
    with pytest.raises(ApprovalAuthorizationError):
        workflow.decide(
            ApprovalDecision(
                approval_id=approval_id,
                approver_id="unauthorized-user",
                decision="approve",
                reason="Unauthorized attempt to approve payment.",
            )
        )

    # Verify status remains pending
    assert workflow.get_request(approval_id).status == "pending"

    # Verify audit log only has the initial request event
    events = audit.list_events(entity_type="approval", entity_id=approval_id)
    assert len(events) == 1
    assert events[0].event_type == "approval.requested"
    assert audit.verify_chain().valid is True


def test_approval_does_not_execute_payment(workflow_setup):
    workflow, _ = workflow_setup
    action = make_action()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)

    submission = submit_action_for_approval(
        action,
        workflow,
        requested_by="analyst-1",
        approver_ids=["manager-1"],
        expires_at=expires_at,
    )

    decision_result = workflow.decide(
        ApprovalDecision(
            approval_id=submission.approval_request.approval_id,
            approver_id="manager-1",
            decision="approve",
            reason="Reviewed and approved action.",
        )
    )

    # Verify the submission and decision objects only represent authorization state
    assert not hasattr(submission, "execute_payment")
    assert not hasattr(submission, "payment_reference")
    assert not hasattr(decision_result, "execute")
    assert not hasattr(decision_result, "execute_payment")
    assert not hasattr(decision_result.approval, "payment_reference")
    assert decision_result.approval.status == "approved"
