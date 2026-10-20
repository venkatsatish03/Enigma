
"""Conservative outstanding-balance assessment.

Only explicitly matched and sufficiently supported payment/credit evidence
is included in the calculation. A computed balance is not automatically
proof that the amount is legally or operationally outstanding.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Iterable

from schemas.financial_operations import (
    BalanceAssessment,
    FinancialRecord,
    FinancialRecordType,
    MatchStatus,
    ReconciliationMatch,
)
from schemas.payment_allocation import PaymentAllocation

ZERO = Decimal("0")


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _currency(record: FinancialRecord) -> str | None:
    if not record.currency or not record.currency.strip():
        return None
    return record.currency.strip().upper()


def _evidence_ids(records: Iterable[FinancialRecord]) -> list[str]:
    return list(
        dict.fromkeys(
            evidence.evidence_id
            for record in records
            for evidence in record.evidence
            if evidence.evidence_id
        )
    )


def _amount(record: FinancialRecord) -> Decimal | None:
    if record.amount is None:
        return None
    try:
        return abs(Decimal(record.amount))
    except (ValueError, TypeError, ArithmeticError):
        return None


def _is_confirmed_match(match: ReconciliationMatch) -> bool:
    return match.status == MatchStatus.MATCHED


def _transaction_sign_is_verified(record: FinancialRecord) -> bool:
    sign_status = (record.metadata.get("sign_status") or "").strip().lower()
    return sign_status in {
        "validated_debit",
        "validated_credit",
        "explicit_negative_sign",
        "explicit_positive_sign",
    }


def _is_outgoing_payment_for_obligation(
    record: FinancialRecord,
) -> tuple[bool, str | None]:
    """Check whether a bank transaction is supported as a payment.

    For invoice settlement, a bank debit is treated as an outgoing payment.
    A credit is not assumed to settle an invoice.
    """
    if record.record_type != FinancialRecordType.BANK_TRANSACTION:
        return False, None

    sign_status = (record.metadata.get("sign_status") or "").strip().lower()

    if not _transaction_sign_is_verified(record):
        return False, "Bank transaction direction is unverified."

    if sign_status in {"validated_debit", "explicit_negative_sign"}:
        return True, None

    return False, (
        "Matched bank transaction is not verified as an outgoing payment."
    )



def assess_balances(
    records: list[FinancialRecord],
    matches: list[ReconciliationMatch],
    allocations: list[PaymentAllocation] | None = None,
) -> list[BalanceAssessment]:
    """Assess invoice balances from either confirmed matches or allocations.

    If allocations is supplied, allocations are the sole payment source.
    This prevents counting a bank transaction both as a match and as an
    allocation. Credits remain outside the payment-allocation pathway.
    """
    records_by_id = {record.record_id: record for record in records}

    matches_by_record: dict[str, list[ReconciliationMatch]] = {}
    if allocations is None:
        for match in matches:
            for record_id in match.record_ids:
                matches_by_record.setdefault(record_id, []).append(match)

    allocations_by_invoice: dict[str, list[PaymentAllocation]] = {}
    if allocations is not None:
        for allocation in allocations:
            allocations_by_invoice.setdefault(
                allocation.invoice_record_id, []
            ).append(allocation)

    assessments: list[BalanceAssessment] = []
    obligations = [
        record
        for record in records
        if record.record_type == FinancialRecordType.INVOICE
    ]

    for obligation in sorted(obligations, key=lambda item: item.record_id):
        original_amount = _amount(obligation)
        obligation_currency = _currency(obligation)

        supported_payments = ZERO
        supported_credits = ZERO
        unresolved_issues: list[str] = []
        evidence_ids = _evidence_ids([obligation])
        has_confirmed_payment_evidence = False
        seen_supporting_records: set[str] = set()

        if original_amount is None:
            unresolved_issues.append(
                "The obligation amount is missing or invalid."
            )

        if obligation_currency is None:
            unresolved_issues.append(
                "The obligation currency is missing."
            )

        if allocations is not None:
            # Allocations are authoritative for payment amounts in this mode.
            for allocation in allocations_by_invoice.get(
                obligation.record_id, []
            ):
                payment = records_by_id.get(allocation.payment_record_id)

                if payment is None:
                    unresolved_issues.append(
                        f"Allocation {allocation.allocation_id} references "
                        f"unknown payment {allocation.payment_record_id}."
                    )
                    continue

                if payment.record_type not in {
                    FinancialRecordType.BANK_TRANSACTION,
                    FinancialRecordType.RENT_RECEIPT,
                }:
                    unresolved_issues.append(
                        f"Allocation {allocation.allocation_id} references "
                        "a record that is not an eligible payment."
                    )
                    continue

                if (
                    not obligation_currency
                    or not allocation.currency
                    or allocation.currency.upper() != obligation_currency
                ):
                    unresolved_issues.append(
                        f"Allocation {allocation.allocation_id} has a "
                        "missing or mismatched currency."
                    )
                    continue

                if _currency(payment) != obligation_currency:
                    unresolved_issues.append(
                        f"Payment {payment.record_id} currency does not match "
                        f"invoice {obligation.record_id}."
                    )
                    continue

                if allocation.amount <= ZERO:
                    unresolved_issues.append(
                        f"Allocation {allocation.allocation_id} has a "
                        "non-positive amount."
                    )
                    continue

                # The allocator already validates bank debit direction.
                # Recheck it here because this is a financial calculation.
                if payment.record_type == FinancialRecordType.BANK_TRANSACTION:
                    sign_status = (
                        payment.metadata.get("sign_status") or ""
                    ).strip().lower()
                    if sign_status not in {
                        "validated_debit",
                        "explicit_negative_sign",
                    }:
                        unresolved_issues.append(
                            f"Payment {payment.record_id} is not verified "
                            "as an outgoing bank debit."
                        )
                        continue

                supported_payments += allocation.amount
                evidence_ids.extend(_evidence_ids([payment]))
                evidence_ids.extend(allocation.evidence_ids)
                seen_supporting_records.add(payment.record_id)
                has_confirmed_payment_evidence = True

        else:
            # Legacy path: preserve the previous confirmed-match behavior.
            for match in matches_by_record.get(obligation.record_id, []):
                other_ids = [
                    record_id
                    for record_id in match.record_ids
                    if record_id != obligation.record_id
                ]
                related_records = [
                    records_by_id[record_id]
                    for record_id in other_ids
                    if record_id in records_by_id
                ]

                if not related_records:
                    continue

                if not _is_confirmed_match(match):
                    unresolved_issues.append(
                        f"Match {match.match_id} is {match.status.value}; "
                        "its amount was not included."
                    )
                    continue

                for related in related_records:
                    if related.record_id in seen_supporting_records:
                        continue

                    if related.record_type not in {
                        FinancialRecordType.RENT_RECEIPT,
                        FinancialRecordType.BANK_TRANSACTION,
                        FinancialRecordType.CREDIT_NOTE,
                    }:
                        continue

                    supporting_amount = _amount(related)
                    if supporting_amount is None:
                        unresolved_issues.append(
                            f"Matched record {related.record_id} has no "
                            "valid amount."
                        )
                        continue

                    related_currency = _currency(related)
                    if not obligation_currency or not related_currency:
                        unresolved_issues.append(
                            f"Currency is missing on obligation or matched "
                            f"record {related.record_id}; its amount was "
                            "not included."
                        )
                        continue

                    if obligation_currency != related_currency:
                        unresolved_issues.append(
                            f"Currency mismatch for matched record "
                            f"{related.record_id}: {obligation_currency} "
                            f"versus {related_currency}; its amount was "
                            "not included."
                        )
                        continue

                    if related.record_type == FinancialRecordType.BANK_TRANSACTION:
                        is_payment, reason = _is_outgoing_payment_for_obligation(
                            related
                        )
                        if not is_payment:
                            unresolved_issues.append(
                                f"Matched transaction {related.record_id} "
                                f"was not counted as a payment. "
                                f"{reason or ''}".strip()
                            )
                            continue
                        supported_payments += supporting_amount

                    elif related.record_type == FinancialRecordType.RENT_RECEIPT:
                        supported_payments += supporting_amount

                    elif related.record_type == FinancialRecordType.CREDIT_NOTE:
                        supported_credits += supporting_amount

                    seen_supporting_records.add(related.record_id)
                    evidence_ids.extend(_evidence_ids([related]))
                    evidence_ids.extend(match.evidence_ids)
                    has_confirmed_payment_evidence = True

        if not has_confirmed_payment_evidence:
            unresolved_issues.append(
                "No confirmed supporting payment or credit evidence was found. "
                "Absence of a matched record does not prove that no payment "
                "occurred."
            )

        outstanding_amount = None
        if original_amount is not None:
            outstanding_amount = (
                original_amount - supported_payments - supported_credits
            )

        calculation_supported = (
            original_amount is not None
            and obligation_currency is not None
            and not unresolved_issues
        )

        assessments.append(
            BalanceAssessment(
                assessment_id=_stable_id(
                    "balance",
                    obligation.record_id,
                    *sorted(seen_supporting_records),
                ),
                obligation_record_id=obligation.record_id,
                currency=obligation_currency,
                original_amount=original_amount,
                supported_payments=supported_payments,
                supported_credits=supported_credits,
                outstanding_amount=outstanding_amount,
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                unresolved_issues=list(dict.fromkeys(unresolved_issues)),
                calculation_supported=calculation_supported,
            )
        )

    return assessments
