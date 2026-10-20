
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from schemas.document import BankTransaction


def validate_statement(
    transactions: list[BankTransaction],
    opening_balance: Decimal | None = None,
    closing_balance: Decimal | None = None,
    tolerance: Decimal = Decimal("0.02"),
) -> list[str]:
    """Validate duplicate transactions and reconcile statement balances."""
    if tolerance < 0:
        raise ValueError("tolerance must not be negative.")

    warnings: list[str] = []

    # Detect exact duplicate transaction records.
    fingerprints = [
        (
            transaction.date,
            transaction.description.strip().casefold(),
            transaction.reference,
            transaction.amount,
            transaction.transaction_type,
        )
        for transaction in transactions
    ]

    counts = Counter(fingerprints)
    duplicate_count = sum(
        count - 1 for count in counts.values() if count > 1
    )

    if duplicate_count:
        warnings.append(
            f"Potential duplicate transactions detected: {duplicate_count}."
        )

    # Missing balances mean reconciliation cannot be verified.
    if opening_balance is None or closing_balance is None:
        warnings.append(
            "Statement balance reconciliation unavailable: "
            "opening or closing balance is missing."
        )
    else:
        calculated_balance = opening_balance
        can_reconcile = True

        for transaction in transactions:
            if transaction.transaction_type == "credit":
                calculated_balance += transaction.amount
            elif transaction.transaction_type == "debit":
                calculated_balance -= transaction.amount
            else:
                can_reconcile = False

        if not can_reconcile:
            warnings.append(
                "Balance cannot be fully reconciled because a transaction "
                "has an unknown transaction type."
            )
        elif abs(calculated_balance - closing_balance) > tolerance:
            warnings.append(
                "Statement balance mismatch: opening balance plus net "
                "transactions does not equal closing balance."
            )

    return warnings
