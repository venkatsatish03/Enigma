from pathlib import Path

import pymupdf

from tools.ocr_engine import PaddleOCREngine


def main() -> None:
    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = output_dir / "ocr_smoke_test.pdf"

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (72, 100),
        "ENIGMA OCR TEST\nInvoice: INV-1001\nTotal Due: USD 405.00",
        fontsize=18,
    )
    doc.save(pdf_path)
    doc.close()

    # Render the page to an image so this tests OCR, not embedded PDF text.
    doc = pymupdf.open(pdf_path)
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    image_path = output_dir / "ocr_smoke_test.png"
    pix.save(image_path)
    doc.close()

    engine = PaddleOCREngine()
    result = engine.extract_text(image_path)

    print("Recognized text:")
    print(result["text"])
    print("Average OCR confidence:", result["average_confidence"])

    normalized = result["text"].lower()
    assert "enigma" in normalized, "OCR failed to recognize ENIGMA"
    assert "inv-1001" in normalized, "OCR failed to recognize invoice number"
    assert "405.00" in normalized, "OCR failed to recognize total"

    print("\nPADDLEOCR SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
