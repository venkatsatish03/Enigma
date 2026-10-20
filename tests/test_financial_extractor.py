from pathlib import Path

from tools.document_processor import DocumentProcessor
from tools.financial_extractor import extract_financial_documents


def test_financial_extractor() -> None:
    main()


def main() -> None:
    pdf_path = Path("data/output/scanned_invoice_test.pdf")
    if not pdf_path.is_file():
        fallback = Path(__file__).parent / "fixtures" / "scanned_invoice_test.pdf"
        if fallback.is_file():
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy(fallback, pdf_path)

    processed = DocumentProcessor().process(pdf_path)
    documents = extract_financial_documents(processed)

    assert len(documents) == 1, f"Expected 1 document, got {len(documents)}"

    document = documents[0]

    print(document.model_dump_json(indent=2))

    assert document.document_type == "maintenance_invoice"
    assert document.vendor_name == "Northstar Plumbing Services"
    assert document.invoice_number == "NSP-2026-1048"
    assert str(document.total_amount) == "575.00"
    assert document.document_date == "2026-10-14"
    assert document.property_address == "17 Willow Court, Unit 3B"
    assert document.field_evidence

    print("\nFINANCIAL EXTRACTION TEST PASSED")


if __name__ == "__main__":
    main()
