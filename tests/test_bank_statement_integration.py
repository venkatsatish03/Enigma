from tools.document_processor import PageResult, ProcessedDocument
from tools.financial_extractor import extract_financial_documents


def main() -> None:
    statement_text = """BANK STATEMENT
Account Ending: 1234
Date | Description | Reference | Debit | Credit
2026-10-01 | Rent payment | RENT-001 | 1200.00 | 0.00
2026-10-02 | Tenant deposit | DEP-002 | 0.00 | 850.00
Closing Balance: USD 4650.00
"""

    processed = ProcessedDocument(
        file_name="sample_bank_statement.pdf",
        pages=[
            PageResult(
                page_number=1,
                text=statement_text,
                extraction_method="embedded_text",
            )
        ],
    )

    documents = extract_financial_documents(processed)

    assert len(documents) == 1
    document = documents[0]

    assert document.document_type == "bank_statement"
    assert len(document.bank_transactions) == 2

    assert document.bank_transactions[0].description == "Rent payment"
    assert document.bank_transactions[0].transaction_type == "debit"
    assert str(document.bank_transactions[0].amount) == "1200.00"

    assert document.bank_transactions[1].description == "Tenant deposit"
    assert document.bank_transactions[1].transaction_type == "credit"
    assert str(document.bank_transactions[1].amount) == "850.00"

    print("PASS: Bank transactions appear in the financial document.")
    print("\nBANK STATEMENT INTEGRATION TEST PASSED")


if __name__ == "__main__":
    main()