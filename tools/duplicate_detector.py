
from __future__ import annotations

import re
from hashlib import sha256

from schemas.financial_operations import (
    FinancialFinding,
    FinancialRecord,
    FinancialRecordType,
    FindingSeverity,
    FindingType,
)


SOURCE_TYPES = {
    FinancialRecordType.INVOICE,
    FinancialRecordType.RENT_RECEIPT,
    FinancialRecordType.BANK_TRANSACTION,
}


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _tokens(value: str | None) -> set[str]:
    if not value:
        return set()

    return {
        token.casefold()
        for token in re.findall(r"[a-zA-Z0-9]+", value)
        if len(token) > 3
    }


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def _same_currency(
    first: FinancialRecord,
    second: FinancialRecord,
) -> bool:
    if not first.currency or not second.currency:
        return False

    return first.currency.upper() == second.currency.upper()


def _same_amount(
    first: FinancialRecord,
    second: FinancialRecord,
) -> bool:
    if first.amount is None or second.amount is None:
        return False

    return abs(first.amount) == abs(second.amount)


def _same_party(
    first: FinancialRecord,
    second: FinancialRecord,
) -> bool:
    first_party = _normalize(first.party_name)
    second_party = _normalize(second.party_name)

    if first_party and second_party and first_party == second_party:
        return True

    first_tokens = _tokens(first.party_name)
    second_tokens = _tokens(second.party_name)

    return bool(first_tokens and second_tokens and first_tokens & second_tokens)


def _same_reference(
    first: FinancialRecord,
    second: FinancialRecord,
) -> bool:
    first_reference = _normalize(first.reference)
    second_reference = _normalize(second.reference)

    # Empty references must never be treated as matching references.
    return bool(
        first_reference
        and second_reference
        and first_reference == second_reference
        and len(first_reference) >= 4
    )


def _same_date(
    first: FinancialRecord,
    second: FinancialRecord,
) -> bool:
    return (
        first.transaction_date is not None
        and second.transaction_date is not None
        and first.transaction_date == second.transaction_date
    )


def _record_evidence_ids(
    first: FinancialRecord,
    second: FinancialRecord,
) -> list[str]:
    return list(
        dict.fromkeys(
            evidence.evidence_id
            for record in (first, second)
            for evidence in record.evidence
        )
    )


def _finding(
    first: FinancialRecord,
    second: FinancialRecord,
    reason: str,
    severity: FindingSeverity = FindingSeverity.WARNING,
) -> FinancialFinding:
    record_ids = sorted([first.record_id, second.record_id])

    return FinancialFinding(
        finding_id="dup-" + _stable_id(*record_ids),
        finding_type=FindingType.POSSIBLE_DUPLICATE,
        severity=severity,
        summary=f"Possible duplicate {first.record_type.value} records",
        record_ids=record_ids,
        evidence_ids=_record_evidence_ids(first, second),
        status="open",
        explanation=reason,
    )


def _invoice_or_receipt_duplicate(
    first: FinancialRecord,
    second: FinancialRecord,
) -> str | None:
    """
    Return an explanation when two invoices or receipts look duplicated.

    A matching reference is the strongest signal. Without a reference,
    require the same party, amount, currency, and date.
    """
    same_reference = _same_reference(first, second)
    same_party = _same_party(first, second)
    same_amount = _same_amount(first, second)
    same_currency = _same_currency(first, second)
    same_date = _same_date(first, second)

    if same_reference and same_party:
        if same_amount and same_currency:
            return (
                "The records share the same reference, counterparty, "
                "amount, and currency. Verify whether they represent "
                "the same underlying obligation or receipt."
            )

        return (
            "The records share the same reference and counterparty, "
            "but their amounts or currencies differ. Investigate the "
            "conflicting records before changing any financial balance."
        )

    # Reference-free fallback: require all fields to match.
    if (
        not first.reference
        and not second.reference
        and same_party
        and same_amount
        and same_currency
        and same_date
    ):
        return (
            "The records have no reference and share the same "
            "counterparty, amount, currency, and date. They may be "
            "duplicates; verify the source documents."
        )

    return None


def _bank_transaction_duplicate(
    first: FinancialRecord,
    second: FinancialRecord,
) -> str | None:
    """
    Compare bank transactions conservatively.

    Different dates are not duplicates under this rule, which helps
    preserve recurring rent and vendor payments.
    """
    if not _same_date(first, second):
        return None

    if not _same_amount(first, second):
        return None

    if not _same_currency(first, second):
        return None

    same_reference = _same_reference(first, second)
    same_party = _same_party(first, second)

    if same_reference and same_party:
        return (
            "The transactions share the same reference, description/"
            "counterparty, date, amount, and currency. Verify whether "
            "the bank statement contains a duplicated transaction."
        )

    # If there is no reference, require matching descriptions.
    if (
        not first.reference
        and not second.reference
        and same_party
    ):
        return (
            "The transactions have no reference and share the same "
            "date, amount, currency, and counterparty/description. "
            "Verify against the original statement."
        )

    return None


def detect_duplicates(
    records: list[FinancialRecord],
) -> list[FinancialFinding]:
    """
    Return possible-duplicate findings without modifying input records.

    This function does not delete, merge, block, or settle records.
    Records from different categories are never compared as duplicates.
    """
    findings: list[FinancialFinding] = []
    seen_pairs: set[tuple[str, str]] = set()

    eligible = [
        record
        for record in records
        if record.record_type in SOURCE_TYPES
    ]

    for index, first in enumerate(eligible):
        for second in eligible[index + 1:]:
            if first.record_id == second.record_id:
                continue

            if first.record_type != second.record_type:
                continue

            pair = tuple(sorted([first.record_id, second.record_id]))

            if pair in seen_pairs:
                continue

            reason = None

            if first.record_type in (
                FinancialRecordType.INVOICE,
                FinancialRecordType.RENT_RECEIPT,
            ):
                reason = _invoice_or_receipt_duplicate(first, second)

            elif first.record_type == FinancialRecordType.BANK_TRANSACTION:
                reason = _bank_transaction_duplicate(first, second)

            if reason is None:
                continue

            seen_pairs.add(pair)
            findings.append(_finding(first, second, reason))

    return findings
