from pathlib import Path

import pymupdf

from tools.document_processor import DocumentProcessor


def main() -> None:
    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    image_path = output_dir / "scanned_invoice_source.png"
    pdf_path = output_dir / "scanned_invoice_test.pdf"

    # Create an image containing invoice text.
    image_doc = pymupdf.open()
    image_page = image_doc.new_page(width=595, height=842)
    image_page.insert_text(
        (60, 100),
        "MAINTENANCE INVOICE\n"
        "Vendor: Northstar Plumbing Services\n"
        "Invoice Number: NSP-2026-1048\n"
        "Invoice Date: 14 October 2026\n"
        "Property: 17 Willow Court, Unit 3B\n"
        "Total Due: USD 575.00",
        fontsize=15,
        lineheight=1.8,
    )
    pix = image_page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    pix.save(image_path)
    image_doc.close()

    # Embed only the image into the PDF; do not insert selectable text.
    pdf_doc = pymupdf.open()
    page = pdf_doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(image_path))
    pdf_doc.save(pdf_path)
    pdf_doc.close()

    processor = DocumentProcessor(min_text_chars=30)
    result = processor.process(pdf_path)

    page = result.pages[0]

    print("File:", result.file_name)
    print("Page count:", result.page_count)
    print("Extraction method:", page.extraction_method)
    print("OCR confidence:", page.ocr_confidence)
    print("Recognized text:")
    print(result.combined_text)

    assert result.page_count == 1
    assert page.extraction_method == "paddleocr"
    assert "NSP-2026-1048" in page.text
    assert "575.00" in page.text

    print("\nSCANNED PDF TEST PASSED")


if __name__ == "__main__":
    main()
