
from decimal import Decimal

from schemas.financial_operations import (
    EvidenceReference,
    FinancialRecord,
    FinancialRecordType,
    MatchStatus,
    ReconciliationMatch,
)
from schemas.payment_allocation import PaymentAllocationInstruction
from tools.payment_allocator import allocate_payments


def make_record(
    record_id: str,
    record_type: FinancialRecordType,
    amount: str,
    *,
    reference: str | None = None,
    party_name: str | None = "Example Vendor",
    currency: str | None = "USD",
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


def test_partial_payment_allocates_only_available_payment_amount():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "500.00",
        reference="INV-1",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-200.00",
        reference="INV-1",
        metadata={"sign_status": "validated_debit"},
    )

    result = allocate_payments([invoice, payment])

    assert len(result.allocations) == 1
    assert result.allocations[0].amount == Decimal("200.00")
    assert result.allocations[0].invoice_record_id == "inv-1"
    assert result.unallocated_amounts == {}


def test_payment_larger_than_invoice_is_capped_at_invoice_balance():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="INV-1",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-150.00",
        reference="INV-1",
        metadata={"sign_status": "validated_debit"},
    )

    result = allocate_payments([invoice, payment])

    assert len(result.allocations) == 1
    assert result.allocations[0].amount == Decimal("100.00")
    assert result.unallocated_amounts["pay-1"] == Decimal("50.00")


def test_multiple_payments_can_partially_settle_one_invoice():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "300.00",
        reference="INV-1",
    )
    payment_1 = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-100.00",
        reference="INV-1",
        metadata={"sign_status": "validated_debit"},
    )
    payment_2 = make_record(
        "pay-2", FinancialRecordType.BANK_TRANSACTION, "-200.00",
        reference="INV-1",
        metadata={"sign_status": "validated_debit"},
    )

    result = allocate_payments([invoice, payment_1, payment_2])

    assert sum((a.amount for a in result.allocations), Decimal("0")) == Decimal("300.00")
    assert len(result.allocations) == 2
    assert result.unallocated_amounts == {}


def test_explicit_instruction_splits_one_payment_across_invoices():
    invoice_1 = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="INV-1",
    )
    invoice_2 = make_record(
        "inv-2", FinancialRecordType.INVOICE, "200.00",
        reference="INV-2",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-150.00",
        reference="REMIT-1",
        metadata={"sign_status": "validated_debit"},
    )
    instructions = [
        PaymentAllocationInstruction(
            payment_record_id="pay-1",
            invoice_record_id="inv-1",
            amount=Decimal("50.00"),
            currency="USD",
            instruction_reference="remittance-123",
        ),
        PaymentAllocationInstruction(
            payment_record_id="pay-1",
            invoice_record_id="inv-2",
            amount=Decimal("100.00"),
            currency="USD",
            instruction_reference="remittance-123",
        ),
    ]

    result = allocate_payments(
        [invoice_1, invoice_2, payment],
        instructions=instructions,
    )

    assert len(result.allocations) == 2
    assert sum((a.amount for a in result.allocations), Decimal("0")) == Decimal("150.00")
    assert {a.invoice_record_id for a in result.allocations} == {"inv-1", "inv-2"}
    assert result.unallocated_amounts == {}


def test_instruction_over_allocation_is_blocked():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="INV-1",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-60.00",
        reference="REMIT-1",
        metadata={"sign_status": "validated_debit"},
    )
    instruction = PaymentAllocationInstruction(
        payment_record_id="pay-1",
        invoice_record_id="inv-1",
        amount=Decimal("80.00"),
        currency="USD",
    )

    result = allocate_payments([invoice, payment], instructions=[instruction])

    assert result.allocations == []
    assert result.unallocated_amounts["pay-1"] == Decimal("60.00")
    assert any("exceeds the remaining" in issue for issue in result.unresolved_issues)


def test_duplicate_invoice_reference_blocks_automatic_allocation():
    invoice_1 = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="INV-DUP",
    )
    invoice_2 = make_record(
        "inv-2", FinancialRecordType.INVOICE, "100.00",
        reference="INV-DUP",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-50.00",
        reference="INV-DUP",
        metadata={"sign_status": "validated_debit"},
    )

    result = allocate_payments([invoice_1, invoice_2, payment])

    assert result.allocations == []
    assert result.unallocated_amounts["pay-1"] == Decimal("50.00")
    assert any("multiple invoices" in issue for issue in result.unresolved_issues)


def test_currency_mismatch_blocks_allocation():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="INV-1", currency="USD",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-50.00",
        reference="INV-1", currency="EUR",
        metadata={"sign_status": "validated_debit"},
    )

    result = allocate_payments([invoice, payment])

    assert result.allocations == []
    assert result.unallocated_amounts["pay-1"] == Decimal("50.00")
    assert any("Currency mismatch" in issue for issue in result.unresolved_issues)


def test_unverified_bank_transaction_is_not_allocated():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="INV-1",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-50.00",
        reference="INV-1",
        metadata={"sign_status": "unresolved"},
    )

    result = allocate_payments([invoice, payment])

    assert result.allocations == []
    assert result.unallocated_amounts == {}
    assert any("not verified" in issue for issue in result.unresolved_issues)


def test_rent_receipt_can_be_allocated_using_unique_reference():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="RENT-OCT",
    )
    receipt = make_record(
        "receipt-1", FinancialRecordType.RENT_RECEIPT, "40.00",
        reference="RENT-OCT",
    )

    result = allocate_payments([invoice, receipt])

    assert len(result.allocations) == 1
    assert result.allocations[0].amount == Decimal("40.00")


def test_party_mismatch_blocks_automatic_allocation():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="INV-1", party_name="Vendor A",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-50.00",
        reference="INV-1", party_name="Vendor B",
        metadata={"sign_status": "validated_debit"},
    )

    result = allocate_payments([invoice, payment])

    assert result.allocations == []
    assert any("matching, explicit party names" in issue
               for issue in result.unresolved_issues)


def test_confirmed_match_can_allocate_without_reference():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference=None,
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-30.00",
        reference=None,
        metadata={"sign_status": "validated_debit"},
    )
    match = ReconciliationMatch(
        match_id="match-1",
        record_ids=["inv-1", "pay-1"],
        status=MatchStatus.MATCHED,
        match_method="verified_test_match",
        evidence_ids=[],
        explanation="Explicitly confirmed test match",
    )

    result = allocate_payments([invoice, payment], matches=[match])

    assert len(result.allocations) == 1
    assert result.allocations[0].amount == Decimal("30.00")
    assert result.allocations[0].method == "confirmed_match"


def test_allocation_ids_are_deterministic():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="INV-1",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-50.00",
        reference="INV-1",
        metadata={"sign_status": "validated_debit"},
    )

    first = allocate_payments([invoice, payment])
    second = allocate_payments([invoice, payment])

    assert [a.allocation_id for a in first.allocations] == [
        a.allocation_id for a in second.allocations
    ]


def test_inputs_are_not_mutated():
    invoice = make_record(
        "inv-1", FinancialRecordType.INVOICE, "100.00",
        reference="INV-1",
    )
    payment = make_record(
        "pay-1", FinancialRecordType.BANK_TRANSACTION, "-50.00",
        reference="INV-1",
        metadata={"sign_status": "validated_debit"},
    )
    original_invoice_metadata = dict(invoice.metadata)
    original_payment_metadata = dict(payment.metadata)

    allocate_payments([invoice, payment])

    assert invoice.metadata == original_invoice_metadata
    assert payment.metadata == original_payment_metadata
