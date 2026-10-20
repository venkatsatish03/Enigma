
from pathlib import Path

from tools.document_processor import DocumentProcessor

pdf_path = Path("data/input/invoice_test.pdf")

if not pdf_path.is_file():
    raise FileNotFoundError(f"PDF not found: {pdf_path.resolve()}")

processor = DocumentProcessor()
processed = processor.process(pdf_path)

for page in processed.pages:
    print("\n" + "=" * 70)
    print(f"PAGE: {page.page_number}")
    print(f"METHOD: {page.extraction_method}")
    print(f"TEXT LENGTH: {len(page.text)}")
    print(f"OCR CONFIDENCE: {page.ocr_confidence}")
    print("-" * 70)
    print(page.text[:5000] or "[NO TEXT EXTRACTED]")
    print("\nWARNINGS:", page.warnings)
