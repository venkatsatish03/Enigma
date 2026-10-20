from pathlib import Path

import pymupdf

from tools.document_processor import DocumentProcessor


def main() -> None:
    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = output_dir / "processor_smoke_test.pdf"

    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text(
            (72, 100),
            "ENIGMA DOCUMENT PROCESSOR TEST\n"
            "Invoice Number: INV-2001\n"
            "Total Due: USD 405.00",
            fontsize=18,
        )
        doc.save(pdf_path)

    processor = DocumentProcessor()
    result = processor.process(pdf_path)

    print("File:", result.file_name)
    print("Page count:", result.page_count)
    print("Extraction method:", result.pages[0].extraction_method)
    print("Extracted text:")
    print(result.combined_text)

    assert result.page_count == 1
    assert "INV-2001" in result.combined_text
    assert "405.00" in result.combined_text
    assert result.pages[0].extraction_method == "embedded_text"

    print("\nDOCUMENT PROCESSOR SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
