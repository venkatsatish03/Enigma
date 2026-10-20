from pathlib import Path

from agents.document_agent import DocumentIntelligenceAgent
from tools.document_processor import PageResult, ProcessedDocument


class FakeProcessor:
    """Return controlled text without initializing PaddleOCR."""

    def __init__(self, text: str) -> None:
        self.text = text

    def process(self, file_path: str | Path) -> ProcessedDocument:
        return ProcessedDocument(
            file_name=Path(file_path).name,
            pages=[
                PageResult(
                    page_number=1,
                    text=self.text,
                    extraction_method="embedded_text",
                )
            ],
        )


def create_agent(text: str) -> DocumentIntelligenceAgent:
    return DocumentIntelligenceAgent(processor=FakeProcessor(text))


def main() -> None:
    # A real file is needed because the agent checks the path before processing.
    pdf_path = Path("data/output/scanned_invoice_test.pdf")
    assert pdf_path.is_file(), f"Test PDF not found: {pdf_path}"

    # Test 1: Missing required invoice fields.
    incomplete_text = """MAINTENANCE INVOICE
Total Due: USD 575.00
"""

    result = create_agent(incomplete_text).process(pdf_path)

    assert result.status == "needs_review", result.model_dump_json(indent=2)

    document = result.documents[0]
    assert "vendor_name" in document.missing_fields
    assert "invoice_number" in document.missing_fields
    assert "document_date" in document.missing_fields
    assert document.requires_human_review

    print("PASS: Missing required fields trigger human review.")

    # Test 2: Subtotal + tax does not equal total.
    mismatch_text = """MAINTENANCE INVOICE
Vendor: Northstar Plumbing Services
Invoice Number: NSP-2026-1048
Invoice Date: 14 October 2026
Subtotal: USD 100.00
Tax: USD 10.00
Total Due: USD 125.00
"""

    result = create_agent(mismatch_text).process(pdf_path)

    assert result.status == "needs_review", result.model_dump_json(indent=2)

    document = result.documents[0]
    assert any(
        "Subtotal plus tax does not match total amount." in warning
        for warning in document.warnings
    )
    assert document.requires_human_review

    print("PASS: Arithmetic mismatches trigger human review.")

    print("\nDOCUMENT VALIDATION TESTS PASSED")


if __name__ == "__main__":
    main()