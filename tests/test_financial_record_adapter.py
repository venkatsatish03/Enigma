
from datetime import date
from decimal import Decimal

import pytest

from agents.document_agent import DocumentAgentResult
from schemas.document import (
    BankTransaction,
    FieldEvidence,
    FinancialDocument,
)
from schemas.financial_operations import FinancialRecordType
from tools.financial_record_adapter import adapt_document_agent_output


def test_invoice_is_converted_without_reextracting():
    invoice = FinancialDocument(
        document_id="doc-invoice-1",
        document_type="invoice",
        source_file="invoice.pdf",
        vendor_name="ABC Maintenance",
        invoice_number="INV-100",
        document_date="2026-10-01",
        due_date="2026-10-31",
        total_amount=Decimal("1250.50"),
        currency="USD",
        is_total_validated=True,
        field_evidence=[
            FieldEvidence(
                field_name="invoice_number",
                value="INV-100",
                page_number=1,
                text_snippet="Invoice No: INV-100",
                confidence=0.99,
            )
        ],
    )

    result = DocumentAgentResult(
        status="completed",
        source_file="invoice.pdf",
        documents=[invoice],
    )

    records = adapt_document_agent_output(result)

    assert len(records) == 1
    record = records[0]

    assert record.record_type == FinancialRecordType.INVOICE
    assert record.document_id == "doc-invoice-1"
    assert record.reference == "INV-100"
    assert record.party_name == "ABC Maintenance"
    assert record.amount == Decimal("1250.50")
    assert record.transaction_date == date(2026, 10, 1)
    assert record.due_date == date(2026, 10, 31)
    assert record.evidence[0].field_name == "invoice_number"
    assert record.evidence[0].page_number == 1


def test_maintenance_invoice_maps_to_invoice_record():
    document = FinancialDocument(
        document_id="doc-maint-1",
        document_type="maintenance_invoice",
        source_file="maintenance.pdf",
        vendor_name="Repair Co",
        invoice_number="M-10",
        total_amount=Decimal("200.00"),
        currency="USD",
    )

    result = DocumentAgentResult(
        status="completed",
        source_file="maintenance.pdf",
        documents=[document],
    )

    records = adapt_document_agent_output(result)

    assert len(records) == 1
    assert records[0].record_type == FinancialRecordType.INVOICE


def test_rent_receipt_uses_payment_amount_and_reference():
    receipt = FinancialDocument(
        document_id="doc-receipt-1",
        document_type="rent_receipt",
        source_file="receipt.pdf",
        tenant_name="Jane Doe",
        unit="A-101",
        receipt_number="R-200",
        payment_reference="PAY-900",
        payment_date="2026-10-05",
        amount_paid=Decimal("1500.00"),
        currency="USD",
    )

    result = DocumentAgentResult(
        status="completed",
        source_file="receipt.pdf",
        documents=[receipt],
    )

    records = adapt_document_agent_output(result)

    assert len(records) == 1
    record = records[0]

    assert record.record_type == FinancialRecordType.RENT_RECEIPT
    assert record.reference == "PAY-900"
    assert record.party_name == "Jane Doe"
    assert record.party_id == "A-101"
    assert record.amount == Decimal("1500.00")
    assert record.transaction_date == date(2026, 10, 5)


def test_bank_debit_is_normalized_as_negative():
    statement = FinancialDocument(
        document_id="statement-1",
        document_type="bank_statement",
        source_file="statement.pdf",
        currency="USD",
        bank_transactions=[
            BankTransaction(
                date="2026-10-06",
                description="ABC Maintenance INV-100",
                reference="INV-100",
                amount=Decimal("1250.50"),
                transaction_type="debit",
                is_sign_validated=True,
                page_number=1,
            )
        ],
    )

    result = DocumentAgentResult(
        status="completed",
        source_file="statement.pdf",
        documents=[statement],
    )

    records = adapt_document_agent_output(result)

    assert len(records) == 1
    record = records[0]

    assert record.record_type == FinancialRecordType.BANK_TRANSACTION
    assert record.amount == Decimal("-1250.50")
    assert record.currency == "USD"
    assert record.reference == "INV-100"
    assert record.metadata["sign_status"] == "validated_debit"


def test_bank_credit_is_normalized_as_positive():
    statement = FinancialDocument(
        document_id="statement-2",
        document_type="bank_statement",
        source_file="statement.pdf",
        bank_transactions=[
            BankTransaction(
                date="2026-10-07",
                description="Rent received",
                amount=Decimal("-1500.00"),
                transaction_type="credit",
            )
        ],
    )

    result = DocumentAgentResult(
        status="completed",
        source_file="statement.pdf",
        documents=[statement],
    )

    records = adapt_document_agent_output(result)

    assert records[0].amount == Decimal("1500.00")
    assert records[0].metadata["sign_status"] == "validated_credit"


def test_unknown_transaction_sign_is_not_guessed():
    statement = FinancialDocument(
        document_id="statement-3",
        document_type="bank_statement",
        source_file="statement.pdf",
        bank_transactions=[
            BankTransaction(
                description="Unclassified transaction",
                amount=Decimal("250.00"),
                transaction_type="unknown",
            )
        ],
    )

    result = DocumentAgentResult(
        status="completed",
        source_file="statement.pdf",
        documents=[statement],
    )

    records = adapt_document_agent_output(result)

    assert records[0].amount == Decimal("250.00")
    assert records[0].metadata["sign_status"] == "unresolved"
    assert records[0].source_status == "sign_unverified"


def test_ambiguous_date_is_not_guessed():
    statement = FinancialDocument(
        document_id="statement-4",
        document_type="bank_statement",
        source_file="statement.pdf",
        bank_transactions=[
            BankTransaction(
                date="03/04/2026",
                description="Transaction with ambiguous date",
                amount=Decimal("100.00"),
                transaction_type="credit",
            )
        ],
    )

    result = DocumentAgentResult(
        status="completed",
        source_file="statement.pdf",
        documents=[statement],
    )

    records = adapt_document_agent_output(result)

    assert records[0].transaction_date is None


def test_failed_document_agent_result_is_rejected():
    result = DocumentAgentResult(
        status="failed",
        source_file="broken.pdf",
        error="Extraction failed",
    )

    with pytest.raises(ValueError, match="Extraction failed"):
        adapt_document_agent_output(result)


def test_dictionary_output_is_supported():
    payload = {
        "status": "completed",
        "source_file": "invoice.pdf",
        "documents": [
            {
                "document_id": "doc-dict-1",
                "document_type": "invoice",
                "source_file": "invoice.pdf",
                "invoice_number": "INV-500",
                "total_amount": "99.95",
                "currency": "USD",
            }
        ],
    }

    records = adapt_document_agent_output(payload)

    assert len(records) == 1
    assert records[0].reference == "INV-500"
    assert records[0].amount == Decimal("99.95")


def test_unknown_document_types_are_not_reclassified():
    document = FinancialDocument(
        document_id="doc-lease-1",
        document_type="lease",
        source_file="lease.pdf",
    )

    result = DocumentAgentResult(
        status="completed",
        source_file="lease.pdf",
        documents=[document],
    )

    assert adapt_document_agent_output(result) == []


def test_missing_document_id_gets_deterministic_fallback():
    document = FinancialDocument(
        document_type="invoice",
        source_file="invoice.pdf",
        invoice_number="INV-700",
        total_amount=Decimal("75.00"),
        currency="USD",
    )

    result = DocumentAgentResult(
        status="completed",
        source_file="invoice.pdf",
        documents=[document],
    )

    first = adapt_document_agent_output(result)
    second = adapt_document_agent_output(result)

    assert first[0].document_id == second[0].document_id
    assert first[0].record_id == second[0].record_id
