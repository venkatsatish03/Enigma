
from decimal import Decimal
from pathlib import Path

from agents.document_agent import DocumentIntelligenceAgent
from tools.document_processor import PageResult, ProcessedDocument


class FakeProcessor:
    def process(self, file_path):
        text = """BANK STATEMENT
Account Ending: 1234
Opening Balance: 5000.00
Date | Description | Reference | Debit | Credit
2026-10-01 | Rent payment | RENT-001 | 1200.00 | 0.00
Closing Balance: 3900.00
"""
        return ProcessedDocument(
            file_name=Path(file_path).name,
            pages=[
                PageResult(
                    page_number=1,
                    text=text,
                    extraction_method="embedded_text",
                )
            ],
        )


def main():
    pdf_path = Path("data/output/scanned_invoice_test.pdf")
    assert pdf_path.is_file(), f"Test PDF not found: {pdf_path}"

    agent = DocumentIntelligenceAgent(processor=FakeProcessor())
    result = agent.process(pdf_path)

    assert len(result.documents) == 1
    document = result.documents[0]

    assert document.document_type == "bank_statement"
    assert document.opening_balance == Decimal("5000.00")
    assert document.closing_balance == Decimal("3900.00")

    warnings = result.warnings + document.warnings

    assert any(
        "balance mismatch" in warning.lower()
        for warning in warnings
    ), f"Expected reconciliation warning; got: {warnings}"

    assert document.requires_human_review
    assert result.status == "needs_review"

    print("PASS: Opening and closing balances extracted.")
    print("PASS: Incorrect closing balance flagged.")
    print("PASS: Agent requires human review.")
    print("\nSTATEMENT BALANCE INTEGRATION TEST PASSED")


if __name__ == "__main__":
    main()
