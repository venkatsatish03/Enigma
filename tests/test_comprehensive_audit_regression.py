from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from agents.document_agent import (
    DocumentIntelligenceAgent,
    ExtractedPage,
)
from schemas.document import (
    BankTransaction,
    FinancialDocument,
    LineItem,
)
from tools.document_processor import PageResult, ProcessedDocument
from tools.financial_extractor import extract_financial_documents
from tools.statement_validator import validate_statement

_FIXTURE_PATH = Path("data/output/scanned_invoice_test.pdf")
if not _FIXTURE_PATH.is_file():
    _src = Path(__file__).parent / "fixtures" / "scanned_invoice_test.pdf"
    if _src.is_file():
        _FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy(_src, _FIXTURE_PATH)


class ControlledMockProcessor:
    """Mock processor returning structured test document text without invoking PaddleOCR."""

    def __init__(self, pages: list[tuple[int, str, str, float | None]]) -> None:
        self._pages = [
            PageResult(
                page_number=p[0],
                text=p[1],
                extraction_method=p[2],
                ocr_confidence=p[3],
            )
            for p in pages
        ]

    def process(self, file_path: str | Path) -> ProcessedDocument:
        return ProcessedDocument(
            file_name=Path(file_path).name,
            pages=self._pages,
        )


def test_a_searchable_invoice() -> None:
    """Test A: Searchable invoice with 3 line items, 8% tax, $405 total, full arithmetic pass."""
    text = """Page 1 — Searchable maintenance invoice
Vendor: Greenline Property Repairs Ltd.
Invoice Number: GL-INV-2026-0317
Invoice Date: 12 October 2026
Due Date: 26 October 2026
Bill To: ENIGMA Sample Property Management
Property: 44 Oak Lane, Apartment 12, Manchester
Description | Qty | Unit price | Line total
Drain inspection | 1 | USD 90.00 | USD 90.00
Replacement parts | 2 | USD 42.50 | USD 85.00
Labour | 2.5 h | USD 80.00 | USD 200.00
Subtotal: USD 375.00
Tax (8%): USD 30.00
Total Due: USD 405.00
Payment terms: Net 14 days. Please quote GL-INV-2026-0317.
"""
    processed = ProcessedDocument(
        file_name="greenline_invoice.pdf",
        pages=[PageResult(page_number=1, text=text, extraction_method="embedded_text")],
    )
    docs = extract_financial_documents(processed)
    assert len(docs) == 1
    doc = docs[0]

    assert doc.document_type == "maintenance_invoice"
    assert doc.vendor_name == "Greenline Property Repairs Ltd."
    assert doc.invoice_number == "GL-INV-2026-0317"
    assert doc.document_date == "2026-10-12"
    assert doc.due_date == "2026-10-26"
    assert doc.bill_to == "ENIGMA Sample Property Management"
    assert doc.unit == "Apartment 12"
    assert doc.payment_terms == "Net 14 days. Please quote GL-INV-2026-0317."
    assert doc.tax_rate == "8%"
    assert doc.currency == "USD"
    assert doc.currency_status == "explicit"
    assert not doc.total_is_calculated
    assert doc.subtotal == Decimal("375.00")
    assert doc.tax == Decimal("30.00")
    assert doc.total_amount == Decimal("405.00")

    assert len(doc.line_items) == 3
    assert doc.line_items[0].description == "Drain inspection"
    assert doc.line_items[0].quantity == Decimal("1")
    assert doc.line_items[0].unit_price == Decimal("90.00")
    assert doc.line_items[0].total == Decimal("90.00")
    assert doc.line_items[0].is_arithmetic_valid is True

    assert doc.line_items[1].quantity == Decimal("2")
    assert doc.line_items[1].unit_price == Decimal("42.50")
    assert doc.line_items[1].total == Decimal("85.00")
    assert doc.line_items[1].is_arithmetic_valid is True

    assert doc.line_items[2].description == "Labour"
    assert doc.line_items[2].quantity == Decimal("2.5")
    assert doc.line_items[2].unit_of_measure == "h"
    assert doc.line_items[2].unit_price == Decimal("80.00")
    assert doc.line_items[2].total == Decimal("200.00")
    assert doc.line_items[2].is_arithmetic_valid is True

    assert doc.is_total_validated is True
    assert doc.validation_dimensions is not None
    assert doc.validation_dimensions.arithmetic_validation == "passed"
    assert doc.validation_dimensions.currency_validation == "explicit_iso"
    assert doc.validation_dimensions.field_validation == "passed"
    assert not doc.requires_human_review
    print("PASS: Test A (Searchable invoice)")


def test_b_scanned_invoice_and_date_conflict() -> None:
    """Test B: Scanned Northstar invoice, calculated total, unresolved currency, and bank debit date conflict."""
    invoice_text = """MAINTENANCE INVOICE
Vendor: Northstar Plumbing Services
Invoice Number: NSP-2026-1048
Invoice Date: 14 October 2026
Bill To: ENIGMA Sample Property Management
Property: 17 Willow Court, Unit 3B
Service: Emergency pipe repair and replacement
Payment Terms: Net 14 days
Description
Qty
Unit Price
Line Total
Emergency call-out
1
$120.00
$120.00
Pipe and fittings
2
$85.00
$170.00
Labour (hours)
3
$95.00
$285.00
"""
    stmt_text = """BANK ACCOUNT STATEMENT
Account Name: ENIGMA Demo Property Operations
Statement Period: 01 October 2026 - 15 October 2026
Account Ending: 4821
Currency: USD
Date | Description | Reference | Amount
04 Oct 2026 | Northstar Plumbing | NSP-2026-1048 | -575.00
"""
    mock_processor = ControlledMockProcessor([
        (1, invoice_text, "paddleocr", 0.99),
        (2, stmt_text, "paddleocr", 0.99),
    ])
    agent = DocumentIntelligenceAgent(processor=mock_processor)
    result = agent.process("data/output/scanned_invoice_test.pdf")

    assert len(result.documents) == 2
    inv = next(d for d in result.documents if d.document_type == "maintenance_invoice")

    assert inv.vendor_name == "Northstar Plumbing Services"
    assert inv.invoice_number == "NSP-2026-1048"
    assert inv.document_date == "2026-10-14"
    assert inv.unit == "Unit 3B"
    assert inv.service_description == "Emergency pipe repair and replacement"
    assert len(inv.line_items) == 3
    assert inv.total_amount == Decimal("575.00")
    assert inv.total_is_calculated is True
    assert inv.currency_status == "inferred"
    assert inv.currency == "USD"

    # Date conflict relationship check
    assert len(result.cross_document_relationships) >= 1
    rel = next(r for r in result.cross_document_relationships if r.reference == "NSP-2026-1048")
    assert rel.status == "MATCHED_WITH_DATE_CONFLICT"
    assert rel.is_validated is False
    assert rel.human_review_required is True
    assert rel.conflict_reason is not None
    assert "2026-10-04" in rel.conflict_reason
    assert "2026-10-14" in rel.conflict_reason
    assert rel.source_date == "2026-10-14"
    assert rel.target_date == "2026-10-04"
    assert rel.matched_amount == Decimal("575.00")

    assert inv.validation_dimensions.temporal_consistency == "date_conflict"
    assert inv.validation_dimensions.reconciliation_status == "MATCHED_WITH_DATE_CONFLICT"
    assert inv.requires_human_review is True
    assert result.status == "needs_review"
    print("PASS: Test B (Scanned invoice and date conflict)")


def test_c_rent_receipt() -> None:
    """Test C: Rent receipt RCPT-77821, Morgan Lee, matches bank credit RENT-ML-2026-10."""
    receipt_text = """RENT PAYMENT RECEIPT
Receipt No: RCPT-77821
Received From: Morgan Lee
Property: 17 Willow Court, Unit 3B
Rent Period: 01 October 2026 - 31 October 2026
Payment Date: 02 October 2026
Payment Method: Bank transfer
Reference: RENT-ML-2026-10
Amount Received: USD 1,450.00
Balance Due: USD 0.00
"""
    stmt_text = """BANK ACCOUNT STATEMENT
Account Ending: 4821
Currency: USD
Date | Description | Reference | Amount
02 Oct 2026 | Rent - Morgan Lee | RENT-ML-2026-10 | +1450.00
"""
    mock_processor = ControlledMockProcessor([
        (1, receipt_text, "paddleocr", 0.995),
        (2, stmt_text, "paddleocr", 0.996),
    ])
    agent = DocumentIntelligenceAgent(processor=mock_processor)
    result = agent.process("data/output/scanned_invoice_test.pdf")

    rec = next(d for d in result.documents if d.document_type == "rent_receipt")
    assert rec.receipt_number == "RCPT-77821"
    assert rec.tenant_name == "Morgan Lee"
    assert rec.rent_period_start == "2026-10-01"
    assert rec.rent_period_end == "2026-10-31"
    assert rec.payment_date == "2026-10-02"
    assert rec.payment_method == "Bank transfer"
    assert rec.payment_reference == "RENT-ML-2026-10"
    assert rec.amount_paid == Decimal("1450.00")
    assert rec.outstanding_balance == Decimal("0.00")
    assert rec.currency == "USD"

    rel = next(r for r in result.cross_document_relationships if r.relationship_type == "rent_deposit")
    assert rel.status == "PAYMENT_CONFIRMED"
    assert rel.is_validated is True
    assert rel.human_review_required is False
    assert rel.amount == Decimal("1450.00")
    assert rec.validation_dimensions.reconciliation_status == "PAYMENT_CONFIRMED"
    print("PASS: Test C (Rent receipt reconciliation)")


def test_d_bank_statement_missing_balances() -> None:
    """Test D: Bank statement with 4 transactions, missing opening/closing balances -> NOT_POSSIBLE_MISSING_BALANCE."""
    stmt_text = """BANK ACCOUNT STATEMENT
Account Name: ENIGMA Demo Property Operations
Statement Period: 01 October 2026 - 15 October 2026
Account Ending: 4821
Currency: USD
Date
Description
Reference
Amount
02 Oct 2026
Rent - Morgan Lee
RENT-ML-2026-10
+1450.00
04 Oct 2026
Northstar Plumbing
NSP-2026-1048
-575.00
07 Oct 2026
Utility payment
UTIL-1007
-126.40
10 Oct 2026
Rent - Jamie Patel
RENT-JP-2026-10
+1450.00
"""
    processed = ProcessedDocument(
        file_name="bank_statement.pdf",
        pages=[PageResult(page_number=1, text=stmt_text, extraction_method="paddleocr", ocr_confidence=0.99)],
    )
    docs = extract_financial_documents(processed)
    assert len(docs) == 1
    doc = docs[0]

    assert doc.document_type == "bank_statement"
    assert doc.account_name == "ENIGMA Demo Property Operations"
    assert doc.account_identifier == "4821"
    assert doc.statement_period_start == "2026-10-01"
    assert doc.statement_period_end == "2026-10-15"
    assert doc.currency == "USD"
    assert doc.opening_balance is None
    assert doc.closing_balance is None
    assert doc.statement_reconciliation_status == "NOT_POSSIBLE_MISSING_BALANCE"

    assert len(doc.bank_transactions) == 4
    t0, t1, t2, t3 = doc.bank_transactions
    assert t0.sign == "+" and t0.transaction_type == "credit" and t0.amount == Decimal("1450.00") and t0.signed_amount == "+1450.00"
    assert t1.sign == "-" and t1.transaction_type == "debit" and t1.amount == Decimal("575.00") and t1.signed_amount == "-575.00"
    assert t2.sign == "-" and t2.transaction_type == "debit" and t2.amount == Decimal("126.40") and t2.signed_amount == "-126.40"
    assert t3.sign == "+" and t3.transaction_type == "credit" and t3.amount == Decimal("1450.00") and t3.signed_amount == "+1450.00"

    for tx in doc.bank_transactions:
        assert tx.is_sign_validated is True
        assert tx.currency == "USD"
        assert tx.page_number == 1
    print("PASS: Test D (Bank statement missing balances & transaction signs)")


def test_e_unmatched_transactions() -> None:
    """Test E: Utility debit $126.40 and Jamie Patel rent credit $1450.00 remain unmatched without fraud claims."""
    receipt_text = """RENT PAYMENT RECEIPT
Receipt No: RCPT-77821
Received From: Morgan Lee
Amount Received: USD 1,450.00
Payment Date: 02 October 2026
Reference: RENT-ML-2026-10
"""
    stmt_text = """BANK ACCOUNT STATEMENT
Account Ending: 4821
Currency: USD
Date | Description | Reference | Amount
02 Oct 2026 | Rent - Morgan Lee | RENT-ML-2026-10 | +1450.00
07 Oct 2026 | Utility payment | UTIL-1007 | -126.40
10 Oct 2026 | Rent - Jamie Patel | RENT-JP-2026-10 | +1450.00
"""
    mock_processor = ControlledMockProcessor([
        (1, receipt_text, "embedded_text", None),
        (2, stmt_text, "embedded_text", None),
    ])
    agent = DocumentIntelligenceAgent(processor=mock_processor)
    result = agent.process("data/output/scanned_invoice_test.pdf")

    unmatched = [r for r in result.cross_document_relationships if r.status == "UNMATCHED_TRANSACTION"]
    assert len(unmatched) == 2

    util_rel = next(r for r in unmatched if r.reference == "UTIL-1007")
    assert util_rel.amount == Decimal("126.40")
    assert util_rel.human_review_required is False
    assert "fraud" not in util_rel.description.lower()

    patel_rel = next(r for r in unmatched if r.reference == "RENT-JP-2026-10")
    assert patel_rel.amount == Decimal("1450.00")
    assert patel_rel.human_review_required is False
    assert "fraud" not in patel_rel.description.lower()
    print("PASS: Test E (Unmatched transactions preserved without false alarms)")


def test_f_payment_status_inference() -> None:
    """Test F: Greenline invoice due 2026-10-26 vs statement ending 2026-10-15 -> PAYMENT_NOT_FOUND_IN_PROVIDED_STATEMENT."""
    inv_text = """MAINTENANCE INVOICE
Vendor: Greenline Property Repairs Ltd.
Invoice Number: GL-INV-2026-0317
Invoice Date: 12 October 2026
Due Date: 26 October 2026
Total Due: USD 405.00
"""
    stmt_text = """BANK ACCOUNT STATEMENT
Account Ending: 4821
Statement Period: 01 October 2026 - 15 October 2026
Currency: USD
Date | Description | Reference | Amount
02 Oct 2026 | Other vendor | OTHER-001 | -50.00
"""
    mock_processor = ControlledMockProcessor([
        (1, inv_text, "embedded_text", None),
        (2, stmt_text, "embedded_text", None),
    ])
    agent = DocumentIntelligenceAgent(processor=mock_processor)
    result = agent.process("data/output/scanned_invoice_test.pdf")

    inv = result.documents[0]
    assert inv.validation_dimensions.reconciliation_status == "PAYMENT_NOT_FOUND_IN_PROVIDED_STATEMENT"
    assert inv.validation_dimensions.cross_document_matching == "pending_statement_coverage"

    rel = next(r for r in result.cross_document_relationships if r.reference == "GL-INV-2026-0317")
    assert rel.status == "PAYMENT_NOT_FOUND_IN_PROVIDED_STATEMENT"
    assert "does not cover the entire period" in rel.description
    assert rel.human_review_required is False
    print("PASS: Test F (Evidence-based payment status inference with statement coverage)")


def test_g_edge_and_negative_cases() -> None:
    """Test G: Negative and edge cases (duplicates, mismatches, missing fields, malformed amounts)."""
    # 1. Duplicate transactions
    dup_tx = [
        BankTransaction(date="2026-10-01", description="Rent", reference="R-1", amount=Decimal("1000.00"), transaction_type="credit"),
        BankTransaction(date="2026-10-01", description="Rent", reference="R-1", amount=Decimal("1000.00"), transaction_type="credit"),
    ]
    dup_warnings = validate_statement(dup_tx, Decimal("5000.00"), Decimal("7000.00"))
    assert any("duplicate" in w.lower() for w in dup_warnings)

    # 2. Amount mismatch with matching reference
    inv_text_mismatch = """MAINTENANCE INVOICE
Vendor: Apex Repairs
Invoice Number: APX-9901
Invoice Date: 10 October 2026
Total Due: USD 500.00
"""
    stmt_text_mismatch = """BANK ACCOUNT STATEMENT
Account Ending: 4821
Date | Description | Reference | Amount
12 Oct 2026 | Apex Repairs | APX-9901 | -450.00
"""
    agent = DocumentIntelligenceAgent(processor=ControlledMockProcessor([
        (1, inv_text_mismatch, "embedded_text", None),
        (2, stmt_text_mismatch, "embedded_text", None),
    ]))
    res = agent.process("data/output/scanned_invoice_test.pdf")
    # APX-9901 should not reconcile due to amount mismatch (500 vs 450)
    assert not any(r.status == "PAYMENT_CONFIRMED" for r in res.cross_document_relationships if r.reference == "APX-9901")

    # 3. Currency mismatch / ambiguous symbol
    inv_unresolved_currency = """MAINTENANCE INVOICE
Vendor: Local Handyman
Invoice Number: LH-1111
Invoice Date: 05 October 2026
Total Due: $250.00
"""
    processed = ProcessedDocument(
        file_name="local_handyman.pdf",
        pages=[PageResult(page_number=1, text=inv_unresolved_currency, extraction_method="embedded_text")],
    )
    docs = extract_financial_documents(processed)
    assert docs[0].currency_status == "inferred"
    assert docs[0].currency == "USD"

    # 3b. Missing currency entirely
    inv_no_currency = """MAINTENANCE INVOICE
Vendor: Local Handyman
Invoice Number: LH-1112
Invoice Date: 05 October 2026
Total Due: 250.00
"""
    docs_no_curr = extract_financial_documents(ProcessedDocument(
        file_name="no_currency.pdf",
        pages=[PageResult(page_number=1, text=inv_no_currency, extraction_method="embedded_text")],
    ))
    assert docs_no_curr[0].currency_status == "unresolved"
    assert docs_no_curr[0].currency is None
    assert docs_no_curr[0].requires_human_review is True

    # 3c. International currency symbol mapping
    for sym, expected_iso in [("€", "EUR"), ("£", "GBP"), ("₹", "INR"), ("¥", "JPY"), ("C$", "CAD")]:
        inv_intl = f"""MAINTENANCE INVOICE
Vendor: Intl Vendor
Invoice Number: IV-999
Invoice Date: 05 October 2026
Total Due: {sym}100.00
"""
        doc_intl = extract_financial_documents(ProcessedDocument(
            file_name="intl.pdf",
            pages=[PageResult(page_number=1, text=inv_intl, extraction_method="embedded_text")],
        ))[0]
        assert doc_intl.currency == expected_iso, f"Failed for symbol {sym}"
        assert doc_intl.currency_status == "inferred"

    # 4. Missing required invoice date
    inv_missing_date = """MAINTENANCE INVOICE
Vendor: No Date Repairs
Invoice Number: NDR-0001
Total Due: USD 300.00
"""
    docs_no_date = extract_financial_documents(ProcessedDocument(
        file_name="no_date.pdf",
        pages=[PageResult(page_number=1, text=inv_missing_date, extraction_method="embedded_text")],
    ))
    agent = DocumentIntelligenceAgent(processor=ControlledMockProcessor([(1, inv_missing_date, "embedded_text", None)]))
    res_no_date = agent.process("data/output/scanned_invoice_test.pdf")
    assert "document_date" in res_no_date.documents[0].missing_fields
    assert res_no_date.documents[0].requires_human_review is True

    # 5. Low-confidence OCR triggers review
    low_conf_doc = extract_financial_documents(ProcessedDocument(
        file_name="blurry.pdf",
        pages=[PageResult(page_number=1, text=inv_missing_date, extraction_method="paddleocr", ocr_confidence=0.65)],
    ))
    assert low_conf_doc[0].requires_human_review is True

    # 6. Malformed transaction rows discarded with warning
    malformed_stmt = """BANK STATEMENT
Date | Description | Amount
2026-10-01 | Test | not-a-valid-amount
"""
    docs_malformed = extract_financial_documents(ProcessedDocument(
        file_name="stmt.pdf",
        pages=[PageResult(page_number=1, text=malformed_stmt, extraction_method="embedded_text")],
    ))
    assert len(docs_malformed[0].bank_transactions) == 0

    print("PASS: Test G (Negative and edge cases)")


def main() -> None:
    print("=== RUNNING ENIGMA COMPREHENSIVE AUDIT REGRESSION SUITE ===")
    test_a_searchable_invoice()
    test_b_scanned_invoice_and_date_conflict()
    test_c_rent_receipt()
    test_d_bank_statement_missing_balances()
    test_e_unmatched_transactions()
    test_f_payment_status_inference()
    test_g_edge_and_negative_cases()
    print("\nALL REGRESSION TESTS A THROUGH G PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    main()
