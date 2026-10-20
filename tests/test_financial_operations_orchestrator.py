
from decimal import Decimal

import pytest

from schemas.financial_operations import (
    EvidenceReference,
    FinancialOperationsResult,
    FinancialRecord,
    FinancialRecordType,
)
from tools.financial_operations_orchestrator import (
    run_financial_operations,
    run_financial_operations_on_records,
)
from schemas.payment_allocation import PaymentAllocationInstruction


def make_record(
    record_id: str,
    record_type: FinancialRecordType,
    amount: str,
    *,
    reference: str | None = None,
    party_name: str | None = "Example Vendor",
    currency: str = "USD",
    metadata: dict[str, str] | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        record_id=record_id,
        record_type=record_type,
        document_id=f"doc-{record_id}",
        reference=reference,
        party_name=party_name,
        amount=Decimal(amount),
        currency=currency,
        transaction_date="2026-10-01",
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


def test_empty_records_produce_valid_empty_result():
    result = run_financial_operations_on_records([])

    assert isinstance(result, FinancialOperationsResult)
    assert result.status == "completed"
    assert result.records == []
    assert result.matches == []
    assert result.findings == []
    assert result.balance_assessments == []
    assert result.proposed_actions == []


def test_invoice_and_confirmed_payment_produce_balance_assessment():
    invoice = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        "200.00",
        reference="INV-100",
    )
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        reference="INV-100",
        metadata={"sign_status": "validated_debit"},
    )

    result = run_financial_operations_on_records([invoice, payment])

    assert result.status == "completed"
    assert len(result.records) == 2
    assert len(result.balance_assessments) == 1

    assessment = result.balance_assessments[0]
    assert assessment.obligation_record_id == "invoice-1"
    assert assessment.supported_payments == Decimal("200.00")
    assert assessment.outstanding_amount == Decimal("0.00")


def test_duplicate_records_surface_findings():
    invoice_1 = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        "100.00",
        reference="INV-100",
    )
    invoice_2 = make_record(
        "invoice-2",
        FinancialRecordType.INVOICE,
        "100.00",
        reference="INV-100",
    )

    result = run_financial_operations_on_records([invoice_1, invoice_2])

    assert any(
        finding.finding_type.value == "possible_duplicate"
        for finding in result.findings
    )


def test_unmatched_bank_transaction_is_not_automatically_an_error():
    transaction = make_record(
        "bank-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-75.00",
        reference="BANK-REF-1",
        metadata={"sign_status": "validated_debit"},
    )

    result = run_financial_operations_on_records([transaction])

    assert result.matches == []
    assert result.findings == []
    assert result.balance_assessments == []


def test_operation_id_is_deterministic_for_same_record_set():
    invoice = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-100",
    )
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        reference="INV-100",
        metadata={"sign_status": "validated_debit"},
    )

    first = run_financial_operations_on_records([invoice, payment])
    second = run_financial_operations_on_records([invoice, payment])

    assert first.operation_id == second.operation_id


def test_operation_id_does_not_depend_on_record_order():
    invoice = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-100",
    )
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        reference="INV-100",
        metadata={"sign_status": "validated_debit"},
    )

    first = run_financial_operations_on_records([invoice, payment])
    second = run_financial_operations_on_records([payment, invoice])

    assert first.operation_id == second.operation_id


def test_invalid_records_argument_is_rejected():
    with pytest.raises(TypeError, match="records must be a list"):
        run_financial_operations_on_records("not a list")  # type: ignore[arg-type]


def test_invalid_item_in_records_is_rejected():
    with pytest.raises(TypeError, match="Every item"):
        run_financial_operations_on_records([object()])  # type: ignore[list-item]


def test_document_agent_entry_point_uses_adapter(monkeypatch):
    invoice = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        "500.00",
        reference="INV-100",
    )
    calls = []

    def fake_adapter(output):
        calls.append(output)
        return [invoice]

    monkeypatch.setattr(
        "tools.financial_operations_orchestrator.adapt_document_agent_output",
        fake_adapter,
    )

    source_output = object()
    result = run_financial_operations(source_output)

    assert calls == [source_output]
    assert result.records == [invoice]
    assert result.status == "completed"


def test_orchestrator_exposes_allocations_and_uses_them_for_balance():
    invoice = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        "200.00",
        reference="INV-100",
    )
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        reference="INV-100",
        metadata={"sign_status": "validated_debit"},
    )

    result = run_financial_operations_on_records([invoice, payment])

    assert len(result.payment_allocations) == 1
    assert result.payment_allocations[0].amount == Decimal("200.00")
    assert result.unallocated_payment_amounts == {}
    assert result.balance_assessments[0].supported_payments == Decimal("200.00")
    assert result.balance_assessments[0].outstanding_amount == Decimal("0.00")


def test_orchestrator_does_not_count_payment_twice():
    invoice = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        "200.00",
        reference="INV-100",
    )
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-200.00",
        reference="INV-100",
        metadata={"sign_status": "validated_debit"},
    )

    result = run_financial_operations_on_records([invoice, payment])

    assessment = result.balance_assessments[0]
    assert len(result.payment_allocations) == 1
    assert assessment.supported_payments == Decimal("200.00")
    assert assessment.outstanding_amount == Decimal("0.00")


def test_orchestrator_accepts_explicit_split_instructions():
    invoice_1 = make_record(
        "invoice-1",
        FinancialRecordType.INVOICE,
        "100.00",
        reference="INV-1",
    )
    invoice_2 = make_record(
        "invoice-2",
        FinancialRecordType.INVOICE,
        "200.00",
        reference="INV-2",
    )
    payment = make_record(
        "payment-1",
        FinancialRecordType.BANK_TRANSACTION,
        "-150.00",
        reference="REMIT-1",
        metadata={"sign_status": "validated_debit"},
    )
    instructions = [
        PaymentAllocationInstruction(
            payment_record_id="payment-1",
            invoice_record_id="invoice-1",
            amount=Decimal("50.00"),
            currency="USD",
            instruction_reference="remittance-1",
        ),
        PaymentAllocationInstruction(
            payment_record_id="payment-1",
            invoice_record_id="invoice-2",
            amount=Decimal("100.00"),
            currency="USD",
            instruction_reference="remittance-1",
        ),
    ]

    result = run_financial_operations_on_records(
        [invoice_1, invoice_2, payment],
        allocation_instructions=instructions,
    )

    assert len(result.payment_allocations) == 2
    assert result.unallocated_payment_amounts == {}
    assessments = {
        item.obligation_record_id: item
        for item in result.balance_assessments
    }
    assert assessments["invoice-1"].supported_payments == Decimal("50.00")
    assert assessments["invoice-1"].outstanding_amount == Decimal("50.00")
    assert assessments["invoice-2"].supported_payments == Decimal("100.00")
    assert assessments["invoice-2"].outstanding_amount == Decimal("100.00")
