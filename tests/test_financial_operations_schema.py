
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas.financial_operations import (
    ActionStatus,
    ActionType,
    BalanceAssessment,
    FinancialFinding,
    FinancialRecord,
    FinancialRecordType,
    FindingSeverity,
    FindingType,
    MatchStatus,
    ProposedFinancialAction,
    ReconciliationMatch,
)


def test_invoice_record_accepts_decimal_amount():
    record = FinancialRecord(
        record_id="inv-001",
        record_type=FinancialRecordType.INVOICE,
        document_id="doc-001",
        reference="INV-1001",
        amount=Decimal("1250.50"),
        currency="USD",
        transaction_date=date(2026, 10, 1),
        due_date=date(2026, 10, 31),
    )

    assert record.amount == Decimal("1250.50")
    assert record.currency == "USD"


def test_bank_transaction_can_have_negative_amount():
    record = FinancialRecord(
        record_id="txn-001",
        record_type=FinancialRecordType.BANK_TRANSACTION,
        document_id="statement-001",
        amount=Decimal("-1250.50"),
        currency="USD",
    )

    assert record.amount == Decimal("-1250.50")


def test_missing_amount_is_allowed_for_incomplete_evidence():
    record = FinancialRecord(
        record_id="inv-002",
        record_type=FinancialRecordType.INVOICE,
        document_id="doc-002",
    )

    assert record.amount is None


def test_currency_must_be_three_characters():
    with pytest.raises(ValidationError):
        FinancialRecord(
            record_id="inv-003",
            record_type=FinancialRecordType.INVOICE,
            document_id="doc-003",
            amount=Decimal("100"),
            currency="US",
        )


def test_reconciliation_requires_at_least_two_records():
    with pytest.raises(ValidationError):
        ReconciliationMatch(
            match_id="match-001",
            record_ids=["inv-001"],
            status=MatchStatus.CANDIDATE,
            match_method="exact_reference",
        )


def test_match_score_must_be_between_zero_and_one():
    with pytest.raises(ValidationError):
        ReconciliationMatch(
            match_id="match-002",
            record_ids=["inv-001", "txn-001"],
            status=MatchStatus.CANDIDATE,
            match_method="reference_and_amount",
            score=1.5,
        )


def test_unsupported_balance_can_remain_unset():
    assessment = BalanceAssessment(
        assessment_id="bal-001",
        obligation_record_id="inv-001",
        currency="USD",
        original_amount=Decimal("1000.00"),
        outstanding_amount=None,
        calculation_supported=False,
        unresolved_issues=["Payment evidence is missing"],
    )

    assert assessment.outstanding_amount is None
    assert assessment.calculation_supported is False


def test_proposed_action_requires_approval_by_default():
    action = ProposedFinancialAction(
        action_id="action-001",
        action_type=ActionType.PROPOSE_PAYMENT,
        amount=Decimal("500.00"),
        currency="USD",
        rationale="Invoice and supporting evidence require review",
        idempotency_key="payment:inv-001:v1",
    )

    assert action.requires_approval is True
    assert action.status == ActionStatus.PROPOSED
    assert action.approval_id is None


def test_finding_keeps_evidence_references():
    finding = FinancialFinding(
        finding_id="finding-001",
        finding_type=FindingType.AMOUNT_DISCREPANCY,
        severity=FindingSeverity.HIGH,
        summary="Invoice and payment amounts differ",
        record_ids=["inv-001", "txn-001"],
        evidence_ids=["evidence-001", "evidence-002"],
    )

    assert len(finding.evidence_ids) == 2
    assert finding.status == "open"
