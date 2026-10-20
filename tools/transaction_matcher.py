
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import re

from schemas.financial_operations import (
    FinancialRecord,
    FinancialRecordType,
    MatchStatus,
    ReconciliationMatch,
)


AMOUNT_TOLERANCE = Decimal("0.02")


def _normalize(value: str | None) -> str:
    """Normalize a reference or party name for comparison."""
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


def _make_match_id(source_id: str, target_id: str) -> str:
    raw = f"{source_id}|{target_id}"
    return "match-" + sha256(raw.encode("utf-8")).hexdigest()[:20]


def _reference_matches(
    source: FinancialRecord,
    transaction: FinancialRecord,
) -> bool:
    source_ref = _normalize(source.reference)
    transaction_ref = _normalize(transaction.reference)

    if not source_ref or not transaction_ref:
        return False

    # Require a meaningful reference. Avoid matching tiny generic values.
    return (
        len(source_ref) >= 4
        and (
            source_ref == transaction_ref
            or source_ref in transaction_ref
            or transaction_ref in source_ref
        )
    )


def _party_matches(
    source: FinancialRecord,
    transaction: FinancialRecord,
) -> bool:
    source_tokens = _tokens(source.party_name)
    transaction_tokens = _tokens(transaction.party_name)

    # A single shared generic token is weak evidence. Require at least
    # one meaningful shared token, with the amount/reference checked later.
    return bool(source_tokens & transaction_tokens)


def _evidence_ids(*records: FinancialRecord) -> list[str]:
    return list(
        dict.fromkeys(
            item.evidence_id
            for record in records
            for item in record.evidence
        )
    )


def _identity_matches(
    source: FinancialRecord,
    transaction: FinancialRecord,
) -> tuple[bool, list[str], float]:
    matched_fields: list[str] = []
    score = 0.0

    if _reference_matches(source, transaction):
        matched_fields.append("reference")
        score += 0.60

    if _party_matches(source, transaction):
        matched_fields.append("counterparty_or_description")
        score += 0.40

    return bool(matched_fields), matched_fields, min(score, 1.0)


def _transaction_is_usable(
    transaction: FinancialRecord,
) -> bool:
    if transaction.record_type != FinancialRecordType.BANK_TRANSACTION:
        return False

    # The adapter preserves the sign-validation status. An unresolved
    # sign must never be treated as a confirmed debit or credit.
    if transaction.metadata.get("sign_status") == "unresolved":
        return False

    if transaction.amount is None or transaction.amount == 0:
        return False

    return True


def _expected_direction(
    source: FinancialRecord,
    transaction: FinancialRecord,
) -> bool:
    if source.record_type == FinancialRecordType.INVOICE:
        # Outgoing invoice payments are bank debits.
        return transaction.amount is not None and transaction.amount < 0

    if source.record_type == FinancialRecordType.RENT_RECEIPT:
        # Rent deposits are bank credits.
        return transaction.amount is not None and transaction.amount > 0

    return False


def _build_match(
    source: FinancialRecord,
    transaction: FinancialRecord,
    status: MatchStatus,
    matched_fields: list[str],
    conflicting_fields: list[str],
    score: float,
    explanation: str,
) -> ReconciliationMatch:
    return ReconciliationMatch(
        match_id=_make_match_id(source.record_id, transaction.record_id),
        record_ids=[source.record_id, transaction.record_id],
        status=status,
        match_method="+".join(matched_fields) or "candidate_review",
        score=score,
        matched_fields=matched_fields,
        conflicting_fields=conflicting_fields,
        evidence_ids=_evidence_ids(source, transaction),
        explanation=explanation,
    )


def reconcile_records(
    records: list[FinancialRecord],
    amount_tolerance: Decimal = AMOUNT_TOLERANCE,
) -> list[ReconciliationMatch]:
    """
    Reconcile invoices and rent receipts against bank transactions.

    Matching is deterministic and does not call an LLM.

    A bank transaction can be assigned to at most one source record
    in a single run. Ambiguous or conflicting matches remain reviewable.

    Amounts use Decimal. Currency mismatches are never silently converted.
    """
    if amount_tolerance < 0:
        raise ValueError("amount_tolerance cannot be negative")

    obligations = [
        record
        for record in records
        if record.record_type in (
            FinancialRecordType.INVOICE,
            FinancialRecordType.RENT_RECEIPT,
        )
    ]

    transactions = [
        record
        for record in records
        if _transaction_is_usable(record)
    ]

    matches: list[ReconciliationMatch] = []
    used_transaction_ids: set[str] = set()

    # Process in source order for reproducibility.
    for source in obligations:
        if source.amount is None:
            continue

        best: tuple[
            FinancialRecord,
            list[str],
            float,
        ] | None = None

        for transaction in transactions:
            if transaction.record_id in used_transaction_ids:
                continue

            if not _expected_direction(source, transaction):
                continue

            identity_ok, matched_fields, score = _identity_matches(
                source, transaction
            )

            if not identity_ok:
                continue

            if best is None or score > best[2]:
                best = (transaction, matched_fields, score)

        if best is None:
            continue

        transaction, matched_fields, score = best

        # Currency must be explicit and equal before confirming an amount.
        source_currency = (
            source.currency.upper() if source.currency else None
        )
        transaction_currency = (
            transaction.currency.upper()
            if transaction.currency
            else None
        )

        if (
            source_currency is None
            or transaction_currency is None
        ):
            matches.append(
                _build_match(
                    source,
                    transaction,
                    MatchStatus.REQUIRES_REVIEW,
                    matched_fields,
                    ["currency_missing"],
                    score,
                    "Candidate identity match found, but currency evidence "
                    "is incomplete. Settlement is not confirmed.",
                )
            )
            used_transaction_ids.add(transaction.record_id)
            continue

        if source_currency != transaction_currency:
            matches.append(
                _build_match(
                    source,
                    transaction,
                    MatchStatus.AMOUNT_CONFLICT,
                    matched_fields,
                    ["currency"],
                    score,
                    f"Currency mismatch: source uses {source_currency}; "
                    f"bank transaction uses {transaction_currency}. "
                    "No currency conversion was applied.",
                )
            )
            used_transaction_ids.add(transaction.record_id)
            continue

        source_amount = abs(source.amount)
        transaction_amount = abs(transaction.amount)

        if abs(source_amount - transaction_amount) > amount_tolerance:
            matches.append(
                _build_match(
                    source,
                    transaction,
                    MatchStatus.AMOUNT_CONFLICT,
                    matched_fields,
                    ["amount"],
                    score,
                    f"Amounts differ: source amount {source_amount} "
                    f"{source_currency}, bank amount "
                    f"{transaction_amount} {transaction_currency}.",
                )
            )
            used_transaction_ids.add(transaction.record_id)
            continue

        # A payment that predates the source document requires review.
        date_conflict = (
            source.transaction_date is not None
            and transaction.transaction_date is not None
            and transaction.transaction_date < source.transaction_date
        )

        if date_conflict:
            matches.append(
                _build_match(
                    source,
                    transaction,
                    MatchStatus.MATCHED_WITH_DATE_CONFLICT,
                    matched_fields + ["amount", "currency"],
                    ["date"],
                    score,
                    "Reference/counterparty, amount, and currency match, "
                    "but the bank transaction predates the source record. "
                    "Settlement requires human review.",
                )
            )
        else:
            matches.append(
                _build_match(
                    source,
                    transaction,
                    MatchStatus.MATCHED,
                    matched_fields + ["amount", "currency"],
                    [],
                    score,
                    "Identity, amount, currency, and transaction direction "
                    "match. Review other findings before treating the "
                    "obligation as settled.",
                )
            )

        used_transaction_ids.add(transaction.record_id)

    return matches
