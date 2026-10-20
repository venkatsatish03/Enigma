
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.action_approval_service import submit_action_for_approval
from tools.action_policy_validator import PolicyStatus


def make_action(**overrides):
    values = {
        "action_id": "action-101",
        "action_type": "propose_payment",
        "status": "proposed",
        "amount": "250.00",
        "currency": "USD",
        "evidence": ["invoice-101", "bank-transaction-101"],
        "unresolved_issues": [],
        "record_ids": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def submit(action, workflow=None, **overrides):
    workflow = workflow or Mock()
    workflow.create_request.return_value = SimpleNamespace(
        request_id="approval-101",
        action_id="action-101",
        status="pending",
    )

    args = {
        "requested_by": "operator-1",
        "approver_ids": ["manager-1"],
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
    }
    args.update(overrides)

    result = submit_action_for_approval(action, workflow, **args)
    return result, workflow


def test_eligible_action_creates_approval_request():
    result, workflow = submit(make_action())

    assert result.policy_result.status == PolicyStatus.ELIGIBLE
    assert result.submitted
    workflow.create_request.assert_called_once()


def test_approval_request_contains_action_identity():
    result, workflow = submit(
        make_action(),
        correlation_id="audit-run-101",
    )

    request = workflow.create_request.call_args.args[0]

    assert request.action_id == "action-101"
    assert request.action_type == "propose_payment"
    assert request.requested_by == "operator-1"
    assert request.approver_ids == ["manager-1"]
    assert request.correlation_id == "audit-run-101"


@pytest.mark.parametrize(
    "action",
    [
        make_action(action_id=None),
        make_action(amount="-1"),
        make_action(currency=""),
        make_action(evidence=[]),
        make_action(status="rejected"),
    ],
)
def test_ineligible_action_does_not_create_request(action):
    result, workflow = submit(action)

    assert not result.submitted
    assert result.approval_request is None
    workflow.create_request.assert_not_called()


def test_duplicate_payment_finding_does_not_create_request():
    finding = {
        "finding_type": "duplicate_payment",
        "severity": "high",
        "status": "open",
    }

    result, workflow = submit(
        make_action(),
        findings=[finding],
    )

    assert result.policy_result.status == PolicyStatus.BLOCKED
    assert not result.submitted
    workflow.create_request.assert_not_called()


def test_unresolved_issues_do_not_create_request():
    result, workflow = submit(
        make_action(unresolved_issues=["Amount requires verification"])
    )

    assert result.policy_result.status == PolicyStatus.MANUAL_REVIEW
    assert not result.submitted
    workflow.create_request.assert_not_called()


def test_high_severity_finding_does_not_create_request():
    finding = {
        "finding_type": "date_needs_review",
        "severity": "high",
        "status": "open",
    }

    result, workflow = submit(
        make_action(),
        findings=[finding],
    )

    assert result.policy_result.status == PolicyStatus.MANUAL_REVIEW
    workflow.create_request.assert_not_called()


def test_workflow_error_is_propagated():
    workflow = Mock()
    workflow.create_request.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        submit(make_action(), workflow=workflow)

    workflow.create_request.assert_called_once()




def test_submission_does_not_execute_payment():
    result, workflow = submit(make_action())

    assert result.submitted
    assert not hasattr(result, "execute_payment")
    assert not hasattr(result, "payment_reference")

    workflow.create_request.assert_called_once()

    # Ensure the service only interacts with the approval-request API.
    assert workflow.method_calls
    assert all(
        call[0] == "create_request"
        for call in workflow.method_calls
    )

