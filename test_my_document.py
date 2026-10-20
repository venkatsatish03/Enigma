
import json
import sys
from pathlib import Path

from agents.document_agent import DocumentIntelligenceAgent

INPUT_DIR = Path("data/input")
OUTPUT_DIR = Path("data/output")


def main():
    pdf_files = sorted(INPUT_DIR.glob("*.pdf"))

    if not pdf_files:
        print(f"No PDF files found in: {INPUT_DIR.resolve()}")
        print("Copy your PDF into data/input and run again.")
        sys.exit(1)

    print("PDF files available:")
    for index, path in enumerate(pdf_files, start=1):
        print(f"{index}. {path.name}")

    if len(sys.argv) > 1:
        choice = sys.argv[1].strip()
    else:
        choice = input("Enter the number of the PDF to test: ").strip()

    if not choice.isdigit() or not 1 <= int(choice) <= len(pdf_files):
        print("Invalid selection.")
        sys.exit(1)

    pdf_path = pdf_files[int(choice) - 1]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nProcessing: {pdf_path.name}")
    agent = DocumentIntelligenceAgent()
    result = agent.process(pdf_path)

    output_path = OUTPUT_DIR / f"{pdf_path.stem}_audit.json"
    output_path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )

    print("\n========== ENIGMA RESULT ==========")
    print(f"Status: {result.status}")
    print(f"Pages: {result.page_count}")
    print(f"Processing methods: {result.processing_methods}")
    print(f"Documents extracted: {len(result.documents)}")
    
    print("\n========== PAGE-BY-PAGE EXTRACTION ==========")

    for page in result.pages:
        print(f"\n--- Page {page.page_number} ---")
        print(f"Method: {page.extraction_method}")
        print(f"OCR confidence: {page.ocr_confidence}")
        print(f"Characters extracted: {len(page.raw_text)}")
        print("Full extracted text:")
        print(page.raw_text)
        print(f"Page warnings: {page.warnings}")


    for index, document in enumerate(result.documents, start=1):
        print(f"\n--- Extracted document {index} ---")
        print(f"Type: {document.document_type}")
        print(f"Vendor: {document.vendor_name}")
        print(f"Tenant: {document.tenant_name}")
        print(f"Invoice number: {document.invoice_number}")
        print(f"Date: {document.document_date}")
        print(f"Total: {document.total_amount} (Validated: {document.is_total_validated})")
        print(f"Opening balance: {document.opening_balance}")
        print(f"Closing balance: {document.closing_balance}")
        print(f"Line items ({len(document.line_items)}):")
        for item in document.line_items:
            print(f"  - {item.description}: Qty {item.quantity} @ {item.unit_price} = {item.total} (arithmetic valid: {item.is_arithmetic_valid})")
        print(f"Transactions ({len(document.bank_transactions)}):")
        for tx in document.bank_transactions:
            print(f"  - {tx.date} | {tx.description} | {tx.reference} | {tx.sign}{tx.amount} ({tx.transaction_type})")
        print(f"Human review required: {document.requires_human_review}")
        print(f"Missing fields: {document.missing_fields}")
        print(f"Warnings: {document.warnings}")

    if result.cross_document_relationships:
        print("\n========== CROSS-DOCUMENT RELATIONSHIPS ==========")
        for rel in result.cross_document_relationships:
            print(f"[{rel.status.upper()}] {rel.relationship_type}: {rel.description}")

    if result.structured_financial_understanding:
        u = result.structured_financial_understanding
        print("\n========== STRUCTURED FINANCIAL UNDERSTANDING ==========")
        print(f"All invoice totals validated: {u.all_invoice_totals_validated}")
        print(f"All transaction signs validated: {u.all_transaction_signs_validated}")
        print(f"Cross-document relationships validated: {u.cross_document_relationships_validated}")

    if result.warnings:
        print("\nAgent warnings:")
        for warning in result.warnings:
            print(f"- {warning}")

    print(f"\nFull JSON report: {output_path.resolve()}")



if __name__ == "__main__":
    main()
