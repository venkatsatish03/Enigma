from decimal import Decimal

from tools.financial_extractor import _extract_line_items


def main() -> None:
    text = """MAINTENANCE INVOICE
Description | Qty | Unit Price | Total
Pipe repair | 2 | 100.00 | 200.00
Replacement valve | 1 | 75.00 | 75.00
Subtotal: USD 275.00
Tax: USD 0.00
Total Due: USD 275.00
"""

    items, warnings = _extract_line_items(text)

    assert len(items) == 2, items
    assert items[0].description == "Pipe repair"
    assert items[0].quantity == Decimal("2")
    assert items[0].unit_price == Decimal("100.00")
    assert items[0].total == Decimal("200.00")
    assert items[1].description == "Replacement valve"
    assert not warnings, warnings

    # A mismatch must be surfaced for review.
    mismatch_text = """Description | Qty | Unit Price | Total
Pipe repair | 2 | 100.00 | 250.00
Subtotal: USD 250.00
"""

    mismatch_items, mismatch_warnings = _extract_line_items(mismatch_text)

    assert len(mismatch_items) == 1
    assert any("arithmetic mismatch" in warning for warning in mismatch_warnings)

    print("PASS: Line items and numeric values extracted.")
    print("PASS: Line-item arithmetic mismatch detected.")
    print("\nLINE ITEM EXTRACTION TESTS PASSED")


if __name__ == "__main__":
    main()