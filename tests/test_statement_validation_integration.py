from decimal import Decimal
from pathlib import Path

from agents.document_agent import DocumentIntelligenceAgent
from schemas.document import BankTransaction
from tools.document_processor import PageResult, ProcessedDocument


class FakeProcessor:
    def process(self, file_path: str | Path) -> ProcessedDocument:
        text = """BANK STATEMENT
Account Ending: 1234
Date | Description | Reference | Debit | Credit
2026-10-01 | Rent payment | RENT-001 | 1200.00 | 0.00
2026-10-01 | Rent payment | RENT-001 | 1200.00 | 0.00
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


def main() -> None:
    pdf_path = Path("data/output/scanned_invoice_test.pdf")
    assert pdf_path.is_file(), f"Test PDF not found: {pdf_path}"

    agent = DocumentIntelligenceAgent(processor=FakeProcessor())
    result = agent.process(pdf_path)

    assert len(result.documents) == 1
    document = result.documents[0]

    assert document.document_type == "bank_statement"
    assert len(document.bank_transactions) == 2
    assert result.status == "needs_review"
    assert document.requires_human_review
    print("Agent warnings:", result.warnings)
    print("Document warnings:", document.warnings)

    assert any(
        "duplicate" in warning.lower()
        for warning in result.warnings + document.warnings
    )
    print("PASS: Duplicate transaction warning reaches agent result.")
    print("\nSTATEMENT VALIDATION INTEGRATION TEST PASSED")


if __name__ == "__main__":
    main()