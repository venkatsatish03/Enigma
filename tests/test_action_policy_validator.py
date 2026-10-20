
from types import SimpleNamespace

import pytest

from tools.action_policy_validator import (
    PolicyStatus,
    validate_proposed_action,
)


def make_action(**overrides):
    values = {
        "action_id": "action-001",
        "action_type": "propose_payment",
        "status": "proposed",
        "amount": "125.50",
        "currency": "USD",
        "evidence": ["invoice-001", "bank-transaction-001"],
        "unresolved_issues": [],
        "record_ids": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_valid_payment_is_eligible_for_approval():
    result = validate_proposed_action(make_action())

    assert result.status == PolicyStatus.ELIGIBLE
    assert result.eligible_for_approval
    assert result.reasons == []


@pytest.mark.parametrize("amount", [None, 0, -1, "NaN", "Infinity", "invalid"])
def test_invalid_payment_amount_is_blocked(amount):
    result = validate_proposed_action(make_action(amount=amount))

    assert result.status == PolicyStatus.BLOCKED
    assert any("amount" in reason.lower() for reason in result.reasons)


def test_missing_currency_is_blocked():
    result = validate_proposed_action(make_action(currency=" "))

    assert result.status == PolicyStatus.BLOCKED
    assert any("currency" in reason.lower() for reason in result.reasons)


def test_missing_evidence_requires_manual_review():
    result = validate_proposed_action(make_action(evidence=[]))

    assert result.status == PolicyStatus.MANUAL_REVIEW
    assert any("evidence" in reason.lower() for reason in result.reasons)


def test_missing_action_id_is_blocked():
    result = validate_proposed_action(make_action(action_id=None))

    assert result.status == PolicyStatus.BLOCKED
    assert any("identifier" in reason.lower() for reason in result.reasons)


def test_rejected_action_is_blocked():
    result = validate_proposed_action(make_action(status="rejected"))

    assert result.status == PolicyStatus.BLOCKED


def test_unresolved_issues_require_manual_review():
    result = validate_proposed_action(
        make_action(unresolved_issues=["Invoice date needs verification"])
    )

    assert result.status == PolicyStatus.MANUAL_REVIEW


def test_duplicate_payment_finding_blocks_action():
    finding = {
        "finding_type": "duplicate_payment",
        "severity": "high",
        "status": "open",
    }

    result = validate_proposed_action(make_action(), findings=[finding])

    assert result.status == PolicyStatus.BLOCKED
    assert any("duplicate" in reason.lower() for reason in result.reasons)


def test_amount_conflict_finding_blocks_action():
    finding = {
        "finding_type": "amount_conflict",
        "severity": "high",
        "status": "open",
    }

    result = validate_proposed_action(make_action(), findings=[finding])

    assert result.status == PolicyStatus.BLOCKED


def test_resolved_duplicate_finding_does_not_block():
    finding = {
        "finding_type": "duplicate_payment",
        "severity": "high",
        "status": "resolved",
    }

    result = validate_proposed_action(make_action(), findings=[finding])

    assert result.status == PolicyStatus.ELIGIBLE


def test_conflicting_reconciliation_blocks_action():
    result = validate_proposed_action(
        make_action(),
        matches=[{"status": "amount_conflict"}],
    )

    assert result.status == PolicyStatus.BLOCKED


def test_invalid_allocation_blocks_action():
    result = validate_proposed_action(
        make_action(),
        allocations=[{"status": "invalid"}],
    )

    assert result.status == PolicyStatus.BLOCKED


def test_missing_referenced_record_blocks_action():
    result = validate_proposed_action(
        make_action(record_ids=["invoice-missing"]),
        records=[{"record_id": "invoice-001"}],
    )

    assert result.status == PolicyStatus.BLOCKED


def test_high_severity_finding_requires_manual_review():
    finding = {
        "finding_type": "date_needs_review",
        "severity": "high",
        "status": "open",
    }

    result = validate_proposed_action(make_action(), findings=[finding])

    assert result.status == PolicyStatus.MANUAL_REVIEW


def test_non_payment_action_does_not_require_payment_fields():
    action = make_action(
        action_type="request_document_review",
        amount=None,
        currency=None,
        evidence=[],
    )

    result = validate_proposed_action(action)

    assert result.status == PolicyStatus.ELIGIBLE


def test_approval_eligibility_does_not_execute_payment():
    action = make_action()
    result = validate_proposed_action(action)

    assert result.eligible_for_approval
    assert not hasattr(result, "execute")
    assert not hasattr(result, "payment_reference")
