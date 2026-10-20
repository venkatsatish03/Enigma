
from decimal import Decimal

from schemas.financial_operations import (
    EvidenceReference,
    FinancialRecord,
    FinancialRecordType,
    MatchStatus,
    ReconciliationMatch,
)
from tools.discrepancy_detector import detect_discrepancies


def make_record(
    record_id: str,
    record_type: FinancialRecordType,
    *,
    amount: str | None = "100.00",
    currency: str | None = "USD",
    transaction_date: str | None = "2026-10-01",
    metadata: dict[str, str] | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        record_id=record_id,
        record_type=record_type,
        document_id=f"doc-{record_id}",
        amount=Decimal(amount) if amount is not None else None,
        currency=currency,
        transaction_date=transaction_date,
        evidence=[
            EvidenceReference(
                evidence_id=f"evidence-{record_id}",
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
    status: MatchStatus,
    *,
    conflicting_fields: list[str] | None = None,
    explanation: str = "",
) -> ReconciliationMatch:
    return ReconciliationMatch(
        match_id=match_id,
        record_ids=record_ids,
        status=status,
        match_method="test",
        conflicting_fields=conflicting_fields or [],
        evidence_ids=[],
        explanation=explanation,
    )


def test_amount_conflict_creates_high_severity_finding():
    invoice = make_record("invoice-1", FinancialRecordType.INVOICE)
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        amount="-90.00",
        metadata={"sign_status": "validated_debit"},
    )
    match = make_match(
        "match-1",
        ["invoice-1", "payment-1"],
        MatchStatus.AMOUNT_CONFLICT,
        conflicting_fields=["amount"],
    )

    findings = detect_discrepancies([invoice, payment], [match])

    amount_findings = [
        f for f in findings if f.finding_type.value == "amount_discrepancy"
    ]
    assert len(amount_findings) == 1
    assert amount_findings[0].severity.value == "high"
    assert set(amount_findings[0].record_ids) == {"invoice-1", "payment-1"}


def test_date_conflict_creates_date_finding():
    invoice = make_record("invoice-1", FinancialRecordType.INVOICE)
    payment = make_record("payment-1", FinancialRecordType.BANK_TRANSACTION)
    match = make_match(
        "match-date",
        ["invoice-1", "payment-1"],
        MatchStatus.MATCHED_WITH_DATE_CONFLICT,
        conflicting_fields=["date"],
    )

    findings = detect_discrepancies([invoice, payment], [match])

    assert any(f.finding_type.value == "date_discrepancy" for f in findings)


def test_different_explicit_currencies_create_finding():
    invoice = make_record(
        "invoice-1", FinancialRecordType.INVOICE, currency="USD"
    )
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        currency="EUR",
    )
    match = make_match(
        "match-currency",
        ["invoice-1", "payment-1"],
        MatchStatus.REQUIRES_REVIEW,
    )

    findings = detect_discrepancies([invoice, payment], [match])

    assert any(f.finding_type.value == "currency_mismatch" for f in findings)


def test_missing_currency_alone_is_not_currency_mismatch():
    invoice = make_record(
        "invoice-1", FinancialRecordType.INVOICE, currency="USD"
    )
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        currency=None,
    )
    match = make_match(
        "match-unknown-currency",
        ["invoice-1", "payment-1"],
        MatchStatus.MATCHED,
    )

    findings = detect_discrepancies([invoice, payment], [match])

    assert not any(f.finding_type.value == "currency_mismatch" for f in findings)


def test_requires_review_creates_insufficient_evidence_finding():
    invoice = make_record("invoice-1", FinancialRecordType.INVOICE)
    payment = make_record("payment-1", FinancialRecordType.BANK_TRANSACTION)
    match = make_match(
        "match-review",
        ["invoice-1", "payment-1"],
        MatchStatus.REQUIRES_REVIEW,
        explanation="Reference number could not be verified.",
    )

    findings = detect_discrepancies([invoice, payment], [match])

    insufficient = [
        f for f in findings if f.finding_type.value == "insufficient_evidence"
    ]
    assert len(insufficient) == 1
    assert "Reference number" in insufficient[0].explanation


def test_missing_fields_create_insufficient_evidence_finding():
    invoice = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        metadata={"missing_fields": "invoice_number,due_date"},
    )

    findings = detect_discrepancies([invoice], [])

    assert len(findings) == 1
    assert findings[0].finding_type.value == "insufficient_evidence"
    assert "invoice_number" in findings[0].explanation
    assert "due_date" in findings[0].explanation


def test_unresolved_bank_transaction_sign_is_flagged():
    transaction = make_record(
        "transaction-1",
        FinancialRecordType.BANK_TRANSACTION,
        metadata={"sign_status": "unresolved"},
    )

    findings = detect_discrepancies([transaction], [])

    assert len(findings) == 1
    assert findings[0].finding_type.value == "insufficient_evidence"
    assert findings[0].severity.value == "high"


def test_unmatched_transaction_alone_does_not_create_finding():
    transaction = make_record(
        "transaction-1",
        FinancialRecordType.BANK_TRANSACTION,
        metadata={"sign_status": "validated_debit"},
    )

    findings = detect_discrepancies([transaction], [])

    assert findings == []


def test_clean_match_creates_no_findings():
    invoice = make_record("invoice-1", FinancialRecordType.INVOICE)
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        metadata={"sign_status": "validated_debit"},
    )
    match = make_match(
        "match-clean",
        ["invoice-1", "payment-1"],
        MatchStatus.MATCHED,
    )

    findings = detect_discrepancies([invoice, payment], [match])

    assert findings == []


def test_findings_are_deterministic():
    invoice = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        metadata={"missing_fields": "due_date"},
    )

    first = detect_discrepancies([invoice], [])
    second = detect_discrepancies([invoice], [])

    assert [f.finding_id for f in first] == [f.finding_id for f in second]


def test_duplicate_match_findings_are_deduplicated():
    invoice = make_record("invoice-1", FinancialRecordType.INVOICE)
    payment = make_record("payment-1", FinancialRecordType.BANK_TRANSACTION)
    match = make_match(
        "match-duplicate",
        ["invoice-1", "payment-1"],
        MatchStatus.AMOUNT_CONFLICT,
        conflicting_fields=["amount"],
    )

    findings = detect_discrepancies(
        [invoice, payment],
        [match, match],
    )

    amount_findings = [
        f for f in findings if f.finding_type.value == "amount_discrepancy"
    ]
    assert len(amount_findings) == 1


def test_records_are_not_mutated():
    invoice = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        metadata={"missing_fields": "due_date"},
    )
    original_metadata = dict(invoice.metadata)

    detect_discrepancies([invoice], [])

    assert invoice.metadata == original_metadata
