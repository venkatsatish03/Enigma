
from datetime import date
from decimal import Decimal

import pytest

from schemas.financial_operations import (
    EvidenceReference,
    FinancialRecord,
    FinancialRecordType,
    MatchStatus,
)
from tools.transaction_matcher import reconcile_records


def make_record(
    record_id,
    record_type,
    amount,
    *,
    reference=None,
    party_name=None,
    currency="USD",
    transaction_date=None,
    sign_status=None,
    evidence_id=None,
):
    metadata = {}
    if sign_status is not None:
        metadata["sign_status"] = sign_status

    evidence = []
    if evidence_id:
        evidence.append(
            EvidenceReference(
                evidence_id=evidence_id,
                document_id=f"doc-{record_id}",
                field_name="amount",
                extracted_value=str(amount),
                page_number=1,
            )
        )

    return FinancialRecord(
        record_id=record_id,
        record_type=record_type,
        document_id=f"doc-{record_id}",
        amount=Decimal(amount) if amount is not None else None,
        reference=reference,
        party_name=party_name,
        currency=currency,
        transaction_date=transaction_date,
        metadata=metadata,
        evidence=evidence,
    )


def test_invoice_matches_bank_debit():
    invoice = make_record(
        "inv-1",
        FinancialRecordType.INVOICE,
        "1250.50",
        reference="INV-1001",
        party_name="ABC Maintenance",
        transaction_date=date(2026, 10, 1),
        evidence_id="ev-invoice",
    )
    transaction = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-1250.50",
        reference="INV-1001",
        party_name="ABC Maintenance payment",
        transaction_date=date(2026, 10, 5),
        sign_status="validated_debit",
        evidence_id="ev-bank",
    )

    matches = reconcile_records([invoice, transaction])

    assert len(matches) == 1
    assert matches[0].status == MatchStatus.MATCHED
    assert set(matches[0].record_ids) == {"inv-1", "txn-1"}
    assert set(matches[0].evidence_ids) == {"ev-invoice", "ev-bank"}


def test_rent_receipt_matches_bank_credit():
    receipt = make_record(
        "receipt-1",
        FinancialRecordType.RENT_RECEIPT,
        "1500.00",
        reference="RENT-OCT-101",
        party_name="Jane Doe",
        transaction_date=date(2026, 10, 5),
    )
    transaction = make_record(
        "txn-2",
        FinancialRecordType.BANK_TRANSACTION,
        "1500.00",
        reference="RENT-OCT-101",
        party_name="Jane Doe rent deposit",
        transaction_date=date(2026, 10, 6),
        sign_status="validated_credit",
    )

    matches = reconcile_records([receipt, transaction])

    assert len(matches) == 1
    assert matches[0].status == MatchStatus.MATCHED


def test_invoice_date_conflict_is_not_confirmed():
    invoice = make_record(
        "inv-2",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-2001",
        transaction_date=date(2026, 10, 10),
    )
    transaction = make_record(
        "txn-3",
        FinancialRecordType.BANK_TRANSACTION,
        "-500.00",
        reference="INV-2001",
        transaction_date=date(2026, 10, 8),
        sign_status="validated_debit",
    )

    matches = reconcile_records([invoice, transaction])

    assert matches[0].status == MatchStatus.MATCHED_WITH_DATE_CONFLICT
    assert "date" in matches[0].conflicting_fields


def test_amount_mismatch_is_flagged():
    invoice = make_record(
        "inv-3",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-3001",
    )
    transaction = make_record(
        "txn-4",
        FinancialRecordType.BANK_TRANSACTION,
        "-450.00",
        reference="INV-3001",
        sign_status="validated_debit",
    )

    matches = reconcile_records([invoice, transaction])

    assert matches[0].status == MatchStatus.AMOUNT_CONFLICT
    assert "amount" in matches[0].conflicting_fields


def test_currency_mismatch_is_not_converted():
    invoice = make_record(
        "inv-4",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-4001",
        currency="USD",
    )
    transaction = make_record(
        "txn-5",
        FinancialRecordType.BANK_TRANSACTION,
        "-500.00",
        reference="INV-4001",
        currency="EUR",
        sign_status="validated_debit",
    )

    matches = reconcile_records([invoice, transaction])

    assert matches[0].status == MatchStatus.AMOUNT_CONFLICT
    assert "currency" in matches[0].conflicting_fields


def test_missing_currency_requires_review():
    invoice = make_record(
        "inv-5",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-5001",
        currency=None,
    )
    transaction = make_record(
        "txn-6",
        FinancialRecordType.BANK_TRANSACTION,
        "-500.00",
        reference="INV-5001",
        currency="USD",
        sign_status="validated_debit",
    )

    matches = reconcile_records([invoice, transaction])

    assert matches[0].status == MatchStatus.REQUIRES_REVIEW
    assert "currency_missing" in matches[0].conflicting_fields


def test_unknown_bank_sign_is_not_matched():
    invoice = make_record(
        "inv-6",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-6001",
    )
    transaction = make_record(
        "txn-7",
        FinancialRecordType.BANK_TRANSACTION,
        "-500.00",
        reference="INV-6001",
        sign_status="unresolved",
    )

    assert reconcile_records([invoice, transaction]) == []


def test_invoice_is_not_matched_to_bank_credit():
    invoice = make_record(
        "inv-7",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-7001",
    )
    transaction = make_record(
        "txn-8",
        FinancialRecordType.BANK_TRANSACTION,
        "500.00",
        reference="INV-7001",
        sign_status="validated_credit",
    )

    assert reconcile_records([invoice, transaction]) == []


def test_amount_tolerance_is_two_cents():
    invoice = make_record(
        "inv-8",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-8001",
    )
    transaction = make_record(
        "txn-9",
        FinancialRecordType.BANK_TRANSACTION,
        "-500.02",
        reference="INV-8001",
        sign_status="validated_debit",
    )

    matches = reconcile_records([invoice, transaction])

    assert matches[0].status == MatchStatus.MATCHED


def test_transaction_is_not_reused():
    invoice_1 = make_record(
        "inv-9",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-9001",
    )
    invoice_2 = make_record(
        "inv-10",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-9001",
    )
    transaction = make_record(
        "txn-10",
        FinancialRecordType.BANK_TRANSACTION,
        "-500.00",
        reference="INV-9001",
        sign_status="validated_debit",
    )

    matches = reconcile_records([invoice_1, invoice_2, transaction])

    assert len(matches) == 1
    assert "txn-10" in matches[0].record_ids


def test_missing_amount_is_skipped():
    invoice = make_record(
        "inv-11",
        FinancialRecordType.INVOICE,
        None,
        reference="INV-11001",
    )
    transaction = make_record(
        "txn-11",
        FinancialRecordType.BANK_TRANSACTION,
        "-500.00",
        reference="INV-11001",
        sign_status="validated_debit",
    )

    assert reconcile_records([invoice, transaction]) == []


def test_negative_tolerance_is_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        reconcile_records([], amount_tolerance=Decimal("-0.01"))
