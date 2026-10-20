
from decimal import Decimal

from schemas.financial_operations import (
    EvidenceReference,
    FinancialRecord,
    FinancialRecordType,
    MatchStatus,
    ReconciliationMatch,
)
from tools.balance_assessor import assess_balances


def make_record(
    record_id: str,
    record_type: FinancialRecordType,
    amount: str | None,
    *,
    currency: str | None = "USD",
    metadata: dict[str, str] | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        record_id=record_id,
        record_type=record_type,
        document_id=f"doc-{record_id}",
        amount=Decimal(amount) if amount is not None else None,
        currency=currency,
        evidence=[
            EvidenceReference(
                evidence_id=f"ev-{record_id}",
                document_id=f"doc-{record_id}",
                field_name="amount",
                extracted_value=amount,
                page_number=1,
            )
        ],
        metadata=metadata or {},
    )


def make_match(
    match_id: str,
    record_ids: list[str],
    status: MatchStatus = MatchStatus.MATCHED,
) -> ReconciliationMatch:
    return ReconciliationMatch(
        match_id=match_id,
        record_ids=record_ids,
        status=status,
        match_method="test",
        evidence_ids=[],
        explanation="Test match",
    )


def test_fully_supported_payment_reduces_outstanding_balance():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, "500.00")
    payment = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        metadata={"sign_status": "validated_debit"},
    )

    assessments = assess_balances(
        [invoice, payment],
        [make_match("m-1", ["inv-1", "txn-1"])],
    )

    result = assessments[0]
    assert result.original_amount == Decimal("500.00")
    assert result.supported_payments == Decimal("200.00")
    assert result.supported_credits == Decimal("0")
    assert result.outstanding_amount == Decimal("300.00")
    assert result.calculation_supported is True


def test_multiple_confirmed_payments_are_accumulated():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, "500.00")
    payment_1 = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-150.00",
        metadata={"sign_status": "validated_debit"},
    )
    payment_2 = make_record(
        "txn-2",
        FinancialRecordType.BANK_TRANSACTION,
        "-100.00",
        metadata={"sign_status": "validated_debit"},
    )

    assessments = assess_balances(
        [invoice, payment_1, payment_2],
        [
            make_match("m-1", ["inv-1", "txn-1"]),
            make_match("m-2", ["inv-1", "txn-2"]),
        ],
    )

    result = assessments[0]
    assert result.supported_payments == Decimal("250.00")
    assert result.outstanding_amount == Decimal("250.00")
    assert result.calculation_supported is True


def test_rent_receipt_can_support_payment():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, "1200.00")
    receipt = make_record(
        "receipt-1", FinancialRecordType.RENT_RECEIPT, "1200.00"
    )

    result = assess_balances(
        [invoice, receipt],
        [make_match("m-1", ["inv-1", "receipt-1"])],
    )[0]

    assert result.supported_payments == Decimal("1200.00")
    assert result.outstanding_amount == Decimal("0.00")
    assert result.calculation_supported is True


def test_confirmed_credit_note_reduces_balance():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, "500.00")
    credit = make_record(
        "credit-1", FinancialRecordType.CREDIT_NOTE, "50.00"
    )

    result = assess_balances(
        [invoice, credit],
        [make_match("m-1", ["inv-1", "credit-1"])],
    )[0]

    assert result.supported_credits == Decimal("50.00")
    assert result.outstanding_amount == Decimal("450.00")
    assert result.calculation_supported is True


def test_unconfirmed_match_does_not_reduce_balance():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, "500.00")
    payment = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        metadata={"sign_status": "validated_debit"},
    )

    result = assess_balances(
        [invoice, payment],
        [make_match("m-1", ["inv-1", "txn-1"], MatchStatus.AMOUNT_CONFLICT)],
    )[0]

    assert result.supported_payments == Decimal("0")
    assert result.outstanding_amount == Decimal("500.00")
    assert result.calculation_supported is False
    assert any(
        "amount_conflict" in issue
        for issue in result.unresolved_issues
    )


def test_unverified_transaction_sign_does_not_reduce_balance():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, "500.00")
    payment = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        metadata={"sign_status": "unresolved"},
    )

    result = assess_balances(
        [invoice, payment],
        [make_match("m-1", ["inv-1", "txn-1"])],
    )[0]

    assert result.supported_payments == Decimal("0")
    assert result.outstanding_amount == Decimal("500.00")
    assert result.calculation_supported is False
    assert any("direction is unverified" in issue for issue in result.unresolved_issues)


def test_credit_transaction_is_not_counted_as_outgoing_payment():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, "500.00")
    transaction = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "200.00",
        metadata={"sign_status": "validated_credit"},
    )

    result = assess_balances(
        [invoice, transaction],
        [make_match("m-1", ["inv-1", "txn-1"])],
    )[0]

    assert result.supported_payments == Decimal("0")
    assert result.outstanding_amount == Decimal("500.00")
    assert result.calculation_supported is False


def test_currency_mismatch_is_not_counted():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "500.00", currency="USD"
    )
    payment = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        currency="EUR",
        metadata={"sign_status": "validated_debit"},
    )

    result = assess_balances(
        [invoice, payment],
        [make_match("m-1", ["inv-1", "txn-1"])],
    )[0]

    assert result.supported_payments == Decimal("0")
    assert result.outstanding_amount == Decimal("500.00")
    assert result.calculation_supported is False
    assert any("Currency mismatch" in issue for issue in result.unresolved_issues)


def test_missing_obligation_amount_is_not_calculated():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, None)

    result = assess_balances([invoice], [])[0]

    assert result.original_amount is None
    assert result.outstanding_amount is None
    assert result.calculation_supported is False
    assert any("amount is missing" in issue for issue in result.unresolved_issues)


def test_no_payment_evidence_is_not_claimed_as_confirmed_outstanding():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, "500.00")

    result = assess_balances([invoice], [])[0]

    assert result.supported_payments == Decimal("0")
    assert result.outstanding_amount == Decimal("500.00")
    assert result.calculation_supported is False
    assert any("does not prove that no payment occurred" in issue
               for issue in result.unresolved_issues)


def test_different_obligations_get_separate_assessments():
    invoice_1 = make_record("inv-1", FinancialRecordType.INVOICE, "500.00")
    invoice_2 = make_record("inv-2", FinancialRecordType.INVOICE, "800.00")

    results = assess_balances([invoice_2, invoice_1], [])

    assert {item.obligation_record_id for item in results} == {"inv-1", "inv-2"}
    assert len(results) == 2
    assert results[0].obligation_record_id == "inv-1"


def test_assessment_ids_are_deterministic():
    invoice = make_record("inv-1", FinancialRecordType.INVOICE, "500.00")
    payment = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        metadata={"sign_status": "validated_debit"},
    )
    matches = [make_match("m-1", ["inv-1", "txn-1"])]

    first = assess_balances([invoice, payment], matches)
    second = assess_balances([invoice, payment], matches)

    assert first[0].assessment_id == second[0].assessment_id


def test_non_invoice_records_do_not_create_obligation_assessments():
    payment = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        metadata={"sign_status": "validated_debit"},
    )

    assert assess_balances([payment], []) == []
