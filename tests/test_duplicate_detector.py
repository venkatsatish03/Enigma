
from datetime import date
from decimal import Decimal

from schemas.financial_operations import (
    FinancialRecord,
    FinancialRecordType,
    FindingType,
)
from tools.duplicate_detector import detect_duplicates


def make_record(
    record_id,
    record_type,
    amount,
    *,
    reference=None,
    party_name=None,
    currency="USD",
    transaction_date=date(2026, 10, 1),
    document_id=None,
):
    return FinancialRecord(
        record_id=record_id,
        record_type=record_type,
        document_id=document_id or f"doc-{record_id}",
        amount=Decimal(amount) if amount is not None else None,
        reference=reference,
        party_name=party_name,
        currency=currency,
        transaction_date=transaction_date,
    )


def test_duplicate_invoices_with_same_reference_are_flagged():
    first = make_record(
        "inv-1",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-1001",
        party_name="ABC Maintenance",
    )
    second = make_record(
        "inv-2",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-1001",
        party_name="ABC Maintenance",
    )

    findings = detect_duplicates([first, second])

    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.POSSIBLE_DUPLICATE
    assert set(findings[0].record_ids) == {"inv-1", "inv-2"}


def test_same_invoice_reference_with_different_amount_is_flagged():
    first = make_record(
        "inv-3",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-2001",
        party_name="ABC Maintenance",
    )
    second = make_record(
        "inv-4",
        FinancialRecordType.INVOICE,
        "550.00",
        reference="INV-2001",
        party_name="ABC Maintenance",
    )

    findings = detect_duplicates([first, second])

    assert len(findings) == 1
    assert "differ" in findings[0].explanation


def test_duplicate_receipts_are_flagged():
    first = make_record(
        "rec-1",
        FinancialRecordType.RENT_RECEIPT,
        "1500.00",
        reference="RENT-101",
        party_name="Jane Doe",
    )
    second = make_record(
        "rec-2",
        FinancialRecordType.RENT_RECEIPT,
        "1500.00",
        reference="RENT-101",
        party_name="Jane Doe",
    )

    assert len(detect_duplicates([first, second])) == 1


def test_recurring_rent_on_different_dates_is_not_flagged():
    first = make_record(
        "rec-3",
        FinancialRecordType.RENT_RECEIPT,
        "1500.00",
        reference="RENT-OCT",
        party_name="Jane Doe",
        transaction_date=date(2026, 10, 1),
    )
    second = make_record(
        "rec-4",
        FinancialRecordType.RENT_RECEIPT,
        "1500.00",
        reference="RENT-NOV",
        party_name="Jane Doe",
        transaction_date=date(2026, 11, 1),
    )

    assert detect_duplicates([first, second]) == []


def test_same_amount_and_party_without_reference_needs_same_date():
    first = make_record(
        "inv-5",
        FinancialRecordType.INVOICE,
        "300.00",
        party_name="XYZ Plumbing",
        transaction_date=date(2026, 10, 1),
    )
    second = make_record(
        "inv-6",
        FinancialRecordType.INVOICE,
        "300.00",
        party_name="XYZ Plumbing",
        transaction_date=date(2026, 10, 1),
    )

    assert len(detect_duplicates([first, second])) == 1


def test_same_invoice_reference_for_different_vendors_is_not_flagged():
    first = make_record(
        "inv-7",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-3001",
        party_name="ABC Maintenance",
    )
    second = make_record(
        "inv-8",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-3001",
        party_name="XYZ Plumbing",
    )

    assert detect_duplicates([first, second]) == []


def test_duplicate_bank_transactions_on_same_date_are_flagged():
    first = make_record(
        "txn-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-250.00",
        reference="PAY-1001",
        party_name="ABC Maintenance payment",
    )
    second = make_record(
        "txn-2",
        FinancialRecordType.BANK_TRANSACTION,
        "-250.00",
        reference="PAY-1001",
        party_name="ABC Maintenance payment",
    )

    assert len(detect_duplicates([first, second])) == 1


def test_same_bank_transaction_on_different_dates_is_not_flagged():
    first = make_record(
        "txn-3",
        FinancialRecordType.BANK_TRANSACTION,
        "-250.00",
        reference="PAY-1001",
        party_name="ABC Maintenance payment",
        transaction_date=date(2026, 10, 1),
    )
    second = make_record(
        "txn-4",
        FinancialRecordType.BANK_TRANSACTION,
        "-250.00",
        reference="PAY-1001",
        party_name="ABC Maintenance payment",
        transaction_date=date(2026, 10, 2),
    )

    assert detect_duplicates([first, second]) == []


def test_invoice_and_bank_transaction_are_not_duplicates():
    invoice = make_record(
        "inv-9",
        FinancialRecordType.INVOICE,
        "250.00",
        reference="PAY-1001",
        party_name="ABC Maintenance",
    )
    transaction = make_record(
        "txn-5",
        FinancialRecordType.BANK_TRANSACTION,
        "-250.00",
        reference="PAY-1001",
        party_name="ABC Maintenance",
    )

    assert detect_duplicates([invoice, transaction]) == []


def test_duplicate_detector_does_not_mutate_records():
    first = make_record(
        "inv-10",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-4001",
        party_name="ABC Maintenance",
    )
    second = make_record(
        "inv-11",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-4001",
        party_name="ABC Maintenance",
    )

    original_first = first.model_dump()
    original_second = second.model_dump()

    detect_duplicates([first, second])

    assert first.model_dump() == original_first
    assert second.model_dump() == original_second


def test_duplicate_finding_ids_are_deterministic():
    first = make_record(
        "inv-12",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-5001",
        party_name="ABC Maintenance",
    )
    second = make_record(
        "inv-13",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-5001",
        party_name="ABC Maintenance",
    )

    first_run = detect_duplicates([first, second])
    second_run = detect_duplicates([first, second])

    assert first_run[0].finding_id == second_run[0].finding_id


def test_missing_amount_does_not_crash_detector():
    first = make_record(
        "inv-14",
        FinancialRecordType.INVOICE,
        None,
        reference="INV-6001",
        party_name="ABC Maintenance",
    )
    second = make_record(
        "inv-15",
        FinancialRecordType.INVOICE,
        None,
        reference="INV-6001",
        party_name="ABC Maintenance",
    )

    findings = detect_duplicates([first, second])

    assert len(findings) == 1
