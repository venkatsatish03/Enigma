
from decimal import Decimal

from tools.financial_extractor import _extract_bank_transactions


def main() -> None:
    text = """BANK STATEMENT
Date | Description | Reference | Debit | Credit
2026-10-01 | Rent payment | RENT-001 | 1200.00 | 0.00
2026-10-02 | Tenant deposit | DEP-002 | 0.00 | 850.00
Closing Balance: USD 5000.00
"""

    transactions, warnings = _extract_bank_transactions(text)

    assert len(transactions) == 2, transactions
    assert not warnings, warnings

    debit = transactions[0]
    assert debit.date == "2026-10-01"
    assert debit.description == "Rent payment"
    assert debit.reference == "RENT-001"
    assert debit.amount == Decimal("1200.00")
    assert debit.transaction_type == "debit"

    credit = transactions[1]
    assert credit.date == "2026-10-02"
    assert credit.description == "Tenant deposit"
    assert credit.reference == "DEP-002"
    assert credit.amount == Decimal("850.00")
    assert credit.transaction_type == "credit"

    # Invalid transaction rows must not silently become valid transactions.
    invalid_text = """BANK STATEMENT
Date | Description | Amount
2026-10-03 | Invalid payment | not-a-number
"""

    invalid_transactions, invalid_warnings = _extract_bank_transactions(
        invalid_text
    )
    assert not invalid_transactions
    assert invalid_warnings

    print("PASS: Debit and credit transactions extracted.")
    print("PASS: Invalid transaction amounts flagged.")
    print("\nBANK STATEMENT EXTRACTION TESTS PASSED")


if __name__ == "__main__":
    main()
