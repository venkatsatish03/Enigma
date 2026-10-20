
"""Conservative payment-to-invoice allocation engine."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from schemas.financial_operations import (
    FinancialRecord,
    FinancialRecordType,
    MatchStatus,
    ReconciliationMatch,
)
from schemas.payment_allocation import (
    PaymentAllocation,
    PaymentAllocationInstruction,
    PaymentAllocationResult,
)


ZERO = Decimal("0")


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"allocation_{digest}"


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip().casefold()


def _amount(record: FinancialRecord) -> Decimal | None:
    if record.amount is None:
        return None
    try:
        return abs(Decimal(record.amount))
    except (ValueError, TypeError, ArithmeticError):
        return None


def _currency(record: FinancialRecord) -> str | None:
    return record.currency.strip().upper() if record.currency else None


def _evidence_ids(record: FinancialRecord) -> list[str]:
    return list(
        dict.fromkeys(
            item.evidence_id for item in record.evidence if item.evidence_id
        )
    )


def _is_payment_record(record: FinancialRecord) -> bool:
    return record.record_type in {
        FinancialRecordType.BANK_TRANSACTION,
        FinancialRecordType.RENT_RECEIPT,
    }


def _payment_is_supported(record: FinancialRecord) -> tuple[bool, str]:
    if record.record_type == FinancialRecordType.RENT_RECEIPT:
        return True, "Source record is a rent receipt."

    sign_status = _normalize(record.metadata.get("sign_status"))
    if sign_status in {"validated_debit", "explicit_negative_sign"}:
        return True, "Bank transaction is verified as an outgoing debit."

    return False, "Bank transaction is not verified as an outgoing debit."


def _party_compatible(
    payment: FinancialRecord,
    invoice: FinancialRecord,
) -> bool:
    payment_party = _normalize(payment.party_name)
    invoice_party = _normalize(invoice.party_name)

    # If either party is unavailable, do not assume a match based on party.
    # A unique exact reference can still provide a candidate, but not enough
    # evidence for automatic allocation under this conservative rule.
    return bool(payment_party and invoice_party and payment_party == invoice_party)


def _confirmed_pair(
    payment_id: str,
    invoice_id: str,
    matches: list[ReconciliationMatch],
) -> bool:
    return any(
        match.status == MatchStatus.MATCHED
        and {payment_id, invoice_id}.issubset(set(match.record_ids))
        for match in matches
    )


def allocate_payments(
    records: list[FinancialRecord],
    matches: list[ReconciliationMatch] | None = None,
    instructions: list[PaymentAllocationInstruction] | None = None,
) -> PaymentAllocationResult:
    """Allocate supported payments to invoices without exceeding balances.

    Automatic allocation requires a unique exact reference, compatible
    parties, matching currencies, and a supported payment direction.
    Multi-invoice splits require explicit instructions.
    """
    matches = matches or []
    instructions = instructions or []

    records_by_id = {record.record_id: record for record in records}
    invoices = {
        record.record_id: record
        for record in records
        if record.record_type == FinancialRecordType.INVOICE
    }
    payments = {
        record.record_id: record
        for record in records
        if _is_payment_record(record)
    }

    result = PaymentAllocationResult()
    remaining_by_invoice: dict[str, Decimal] = {}
    remaining_by_payment: dict[str, Decimal] = {}

    for invoice_id, invoice in invoices.items():
        amount = _amount(invoice)
        if amount is None:
            result.unresolved_issues.append(
                f"Invoice {invoice_id} has no valid amount; "
                "allocations to it are blocked."
            )
            continue
        remaining_by_invoice[invoice_id] = amount

    for payment_id, payment in payments.items():
        amount = _amount(payment)
        if amount is None:
            result.unresolved_issues.append(
                f"Payment {payment_id} has no valid amount."
            )
            continue

        supported, reason = _payment_is_supported(payment)
        if not supported:
            result.unresolved_issues.append(f"Payment {payment_id}: {reason}")
            continue

        remaining_by_payment[payment_id] = amount

    def add_allocation(
        payment_id: str,
        invoice_id: str,
        amount: Decimal,
        method: str,
        rationale: str,
    ) -> bool:
        payment = payments.get(payment_id)
        invoice = invoices.get(invoice_id)

        if payment is None:
            result.unresolved_issues.append(
                f"Allocation references unknown or unsupported payment "
                f"{payment_id}."
            )
            return False
        if invoice is None:
            result.unresolved_issues.append(
                f"Allocation references unknown invoice {invoice_id}."
            )
            return False

        if payment_id not in remaining_by_payment:
            result.unresolved_issues.append(
                f"Payment {payment_id} is not eligible for allocation."
            )
            return False
        if invoice_id not in remaining_by_invoice:
            result.unresolved_issues.append(
                f"Invoice {invoice_id} is not eligible for allocation."
            )
            return False

        payment_currency = _currency(payment)
        invoice_currency = _currency(invoice)
        if not payment_currency or not invoice_currency:
            result.unresolved_issues.append(
                f"Currency missing for payment {payment_id} or invoice "
                f"{invoice_id}; allocation was blocked."
            )
            return False
        if payment_currency != invoice_currency:
            result.unresolved_issues.append(
                f"Currency mismatch between payment {payment_id} and "
                f"invoice {invoice_id}; allocation was blocked."
            )
            return False

        if amount <= ZERO:
            result.unresolved_issues.append(
                f"Allocation amount must be positive for payment {payment_id}."
            )
            return False

        available_payment = remaining_by_payment[payment_id]
        available_invoice = remaining_by_invoice[invoice_id]
        if amount > available_payment or amount > available_invoice:
            result.unresolved_issues.append(
                f"Requested allocation {amount} {payment_currency} from "
                f"{payment_id} to {invoice_id} exceeds the remaining payment "
                "or invoice balance; allocation was blocked."
            )
            return False

        allocation_id = _stable_id(
            payment_id, invoice_id, str(amount), method
        )
        result.allocations.append(
            PaymentAllocation(
                allocation_id=allocation_id,
                payment_record_id=payment_id,
                invoice_record_id=invoice_id,
                amount=amount,
                currency=payment_currency,
                method=method,  # type: ignore[arg-type]
                evidence_ids=list(
                    dict.fromkeys(
                        _evidence_ids(payment) + _evidence_ids(invoice)
                    )
                ),
                rationale=rationale,
            )
        )
        remaining_by_payment[payment_id] -= amount
        remaining_by_invoice[invoice_id] -= amount
        return True

    explicitly_instructed_payment_ids = {
        instruction.payment_record_id
        for instruction in instructions
    }

    # Explicit instructions are processed first, in a stable order.
    # Invalid instructions become unresolved issues; they are never guessed.
    for instruction in sorted(
        instructions,
        key=lambda item: (
            item.payment_record_id,
            item.invoice_record_id,
            str(item.amount),
        ),
    ):
        payment = payments.get(instruction.payment_record_id)
        invoice = invoices.get(instruction.invoice_record_id)

        if payment is None or invoice is None:
            result.unresolved_issues.append(
                "Explicit allocation instruction references an unknown "
                f"payment or invoice: {instruction.payment_record_id} -> "
                f"{instruction.invoice_record_id}."
            )
            continue

        instruction_currency = instruction.currency.upper()
        if (
            _currency(payment) != instruction_currency
            or _currency(invoice) != instruction_currency
        ):
            result.unresolved_issues.append(
                f"Instruction currency mismatch for payment "
                f"{instruction.payment_record_id} and invoice "
                f"{instruction.invoice_record_id}."
            )
            continue

        supported, reason = _payment_is_supported(payment)
        if not supported:
            result.unresolved_issues.append(
                f"Explicit instruction blocked for "
                f"{instruction.payment_record_id}: {reason}"
            )
            continue

        add_allocation(
            instruction.payment_record_id,
            instruction.invoice_record_id,
            instruction.amount,
            "explicit_instruction",
            (
                "Allocated according to an explicit instruction"
                + (
                    f" ({instruction.instruction_reference})"
                    if instruction.instruction_reference
                    else ""
                )
                + "."
            ),
        )

    # Automatically allocate remaining amounts only for a unique exact
    # reference plus compatible party and currency.
    for payment_id in sorted(remaining_by_payment):
        payment = payments[payment_id]
        if payment_id in explicitly_instructed_payment_ids:
            continue    
        if remaining_by_payment[payment_id] <= ZERO:
            continue

        reference = _normalize(payment.reference)
        if not reference:
            # A confirmed match can be used only if it identifies exactly one
            # invoice. No partial amount is inferred from a conflicting match.
            confirmed_invoice_ids = [
                invoice_id
                for invoice_id in invoices
                if _confirmed_pair(payment_id, invoice_id, matches)
            ]
            if len(confirmed_invoice_ids) != 1:
                continue
            candidate_invoice_ids = confirmed_invoice_ids
            method = "confirmed_match"
        else:
            candidate_invoice_ids = [
                invoice_id
                for invoice_id, invoice in invoices.items()
                if _normalize(invoice.reference) == reference
            ]
            if len(candidate_invoice_ids) != 1:
                if len(candidate_invoice_ids) > 1:
                    result.unresolved_issues.append(
                        f"Payment {payment_id} reference matches multiple "
                        "invoices; automatic allocation was blocked."
                    )
                continue
            method = "unique_reference"

        invoice_id = candidate_invoice_ids[0]
        invoice = invoices[invoice_id]

        if not _party_compatible(payment, invoice):
            result.unresolved_issues.append(
                f"Payment {payment_id} and invoice {invoice_id} do not have "
                "matching, explicit party names; automatic allocation "
                "was blocked."
            )
            continue

        if _currency(payment) != _currency(invoice):
            result.unresolved_issues.append(
                f"Currency mismatch between payment {payment_id} and invoice "
                f"{invoice_id}; automatic allocation was blocked."
            )
            continue

        if invoice_id not in remaining_by_invoice:
            continue

        allocation_amount = min(
            remaining_by_payment[payment_id],
            remaining_by_invoice[invoice_id],
        )
        if allocation_amount <= ZERO:
            continue

        add_allocation(
            payment_id,
            invoice_id,
            allocation_amount,
            method,
            (
                "Allocated using a unique invoice reference and matching "
                "party/currency evidence."
                if method == "unique_reference"
                else "Allocated using a confirmed reconciliation match."
            ),
        )

    # Return only meaningful unallocated amounts for eligible payment records.
    for payment_id, remaining in sorted(remaining_by_payment.items()):
        if remaining > ZERO:
            result.unallocated_amounts[payment_id] = remaining

    result.allocations.sort(
        key=lambda item: (
            item.payment_record_id,
            item.invoice_record_id,
            item.allocation_id,
        )
    )
    result.unresolved_issues = list(dict.fromkeys(result.unresolved_issues))
    return result
