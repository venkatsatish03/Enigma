
from decimal import Decimal

from schemas.document import BankTransaction
from tools.statement_validator import validate_statement


def transaction(amount, transaction_type="debit", description="Rent"):
    return BankTransaction(
        date="2026-10-01",
        description=description,
        reference="REF-001",
        amount=Decimal(amount),
        transaction_type=transaction_type,
    )


def main():
    opening = Decimal("5000.00")
    debit = transaction("1200.00")

    # Correct balance.
    assert validate_statement(
        [debit], opening, Decimal("3800.00")
    ) == []
    print("PASS: Correct statement balance reconciles.")

    # Incorrect closing balance.
    warnings = validate_statement(
        [debit], opening, Decimal("3900.00")
    )
    assert any("balance mismatch" in w.lower() for w in warnings)
    print("PASS: Incorrect closing balance is detected.")

    # Missing closing balance.
    warnings = validate_statement([debit], opening, None)
    assert any("missing" in w.lower() for w in warnings)
    print("PASS: Missing closing balance is flagged.")

    # Missing opening balance.
    warnings = validate_statement([debit], None, Decimal("3800.00"))
    assert any("missing" in w.lower() for w in warnings)
    print("PASS: Missing opening balance is flagged.")

    # Unknown transaction type must prevent reconciliation.
    unknown = transaction("1200.00", "unknown")
    warnings = validate_statement(
        [unknown], opening, Decimal("3800.00")
    )
    assert any("unknown transaction type" in w.lower() for w in warnings)
    assert not any("balance mismatch" in w.lower() for w in warnings)
    print("PASS: Unknown transaction type prevents reconciliation.")

    # A difference equal to the tolerance is accepted.
    assert validate_statement(
        [debit], opening, Decimal("3800.02"),
        tolerance=Decimal("0.02"),
    ) == []
    print("PASS: Difference equal to tolerance is accepted.")

    # A difference greater than the tolerance is rejected.
    warnings = validate_statement(
        [debit], opening, Decimal("3800.03"),
        tolerance=Decimal("0.02"),
    )
    assert any("balance mismatch" in w.lower() for w in warnings)
    print("PASS: Difference above tolerance is flagged.")

    # Duplicate detection.
    warnings = validate_statement(
        [debit, debit], opening, Decimal("2600.00")
    )
    assert any("duplicate" in w.lower() for w in warnings)
    print("PASS: Potential duplicate transactions are detected.")

    print("\nSTATEMENT VALIDATOR TESTS PASSED")


if __name__ == "__main__":
    main()
