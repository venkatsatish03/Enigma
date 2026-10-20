
from pathlib import Path

from agents.document_agent import DocumentIntelligenceAgent


def test_document_agent_integration() -> None:
    main()


def main() -> None:
    agent = DocumentIntelligenceAgent()
    pdf_path = Path("data/output/scanned_invoice_test.pdf")
    if not pdf_path.is_file():
        fallback = Path(__file__).parent / "fixtures" / "scanned_invoice_test.pdf"
        if fallback.is_file():
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy(fallback, pdf_path)

    result = agent.process(pdf_path)
    print(result.model_dump_json(indent=2))

    assert result.source_file == pdf_path.name
    assert result.page_count == 1
    assert len(result.documents) == 1
    assert result.status == "completed"

    document = result.documents[0]
    assert document.vendor_name == "Northstar Plumbing Services"
    assert document.invoice_number == "NSP-2026-1048"
    assert str(document.total_amount) == "575.00"

    # The test invoice has no extracted invoice due date or subtotal/tax.
    assert "document_date" not in document.missing_fields
    assert "total_amount" not in document.missing_fields
    assert not document.requires_human_review
    assert not result.warnings

    missing_result = agent.process("data/input/does_not_exist.pdf")
    assert missing_result.status == "failed"
    assert missing_result.error is not None

    print("\nDOCUMENT AGENT VALIDATION TEST PASSED")


if __name__ == "__main__":
    main()
