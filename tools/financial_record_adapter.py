
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ValidationError

from agents.document_agent import DocumentAgentResult
from schemas.document import FinancialDocument
from schemas.financial_operations import (
    EvidenceReference,
    FinancialRecord,
    FinancialRecordType,
)


def _stable_id(*parts: str) -> str:
    """Create a deterministic identifier from source values."""
    raw = "|".join(parts)
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def _parse_date(value: Any) -> date | None:
    """Parse common date formats without guessing ambiguous dates."""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None

    # ISO dates and ISO timestamps are unambiguous.
    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")
        ).date()
    except ValueError:
        pass

    # These formats explicitly define the month/day order.
    for fmt in ("%Y/%m/%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    # Do not guess whether values such as 03/04/2026 mean
    # March 4 or April 3.
    return None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _document_id(document: FinancialDocument, index: int) -> str:
    """
    Prefer the Document Agent's real ID.

    The fallback is deterministic for the same source file and position.
    """
    if document.document_id and document.document_id.strip():
        return document.document_id.strip()

    return f"doc-{_stable_id(document.source_file, str(index))}"


def _field_evidence(
    document: FinancialDocument,
    document_id: str,
) -> list[EvidenceReference]:
    evidence: list[EvidenceReference] = []

    for item in document.field_evidence:
        evidence.append(
            EvidenceReference(
                evidence_id=(
                    f"ev-{_stable_id(document_id, item.field_name, str(item.page_number), item.value)}"
                ),
                document_id=document_id,
                field_name=item.field_name,
                extracted_value=item.value,
                page_number=item.page_number,
            )
        )

    return evidence


def _document_validation_metadata(
    document: FinancialDocument,
) -> dict[str, str]:
    metadata: dict[str, str] = {
        "source_file": document.source_file,
        "extraction_method": document.extraction_method,
        "currency_status": document.currency_status,
        "total_is_calculated": str(document.total_is_calculated).lower(),
        "is_total_validated": str(document.is_total_validated).lower(),
        "requires_human_review": str(
            document.requires_human_review
        ).lower(),
    }

    if document.validation_dimensions is not None:
        dimensions = document.validation_dimensions

        metadata.update(
            {
                "extraction_completeness": dimensions.extraction_completeness,
                "field_validation": dimensions.field_validation,
                "arithmetic_validation": dimensions.arithmetic_validation,
                "currency_validation": dimensions.currency_validation,
                "temporal_consistency": dimensions.temporal_consistency,
                "cross_document_matching": dimensions.cross_document_matching,
                "reconciliation_status": dimensions.reconciliation_status,
                "human_review_required": str(
                    dimensions.human_review_required
                ).lower(),
            }
        )

    if document.warnings:
        metadata["source_warnings"] = " | ".join(document.warnings)

    if document.missing_fields:
        metadata["missing_fields"] = ", ".join(document.missing_fields)

    if document.currency_evidence:
        metadata["currency_evidence"] = document.currency_evidence

    return metadata


def _normalize_document(
    document: FinancialDocument,
    index: int,
) -> list[FinancialRecord]:
    doc_id = _document_id(document, index)
    evidence = _field_evidence(document, doc_id)
    metadata = _document_validation_metadata(document)

    common = {
        "document_id": doc_id,
        "evidence": evidence,
        "metadata": metadata,
    }

    if document.document_type in ("invoice", "maintenance_invoice"):
        return [
            FinancialRecord(
                record_id=f"invoice-{_stable_id(doc_id)}",
                record_type=FinancialRecordType.INVOICE,
                reference=_as_text(document.invoice_number),
                party_name=_as_text(document.vendor_name),
                amount=_as_decimal(document.total_amount),
                currency=_as_text(document.currency),
                transaction_date=_parse_date(document.document_date),
                due_date=_parse_date(document.due_date),
                source_status=(
                    document.validation_dimensions.reconciliation_status
                    if document.validation_dimensions
                    else None
                ),
                **common,
            )
        ]

    if document.document_type == "rent_receipt":
        return [
            FinancialRecord(
                record_id=f"receipt-{_stable_id(doc_id)}",
                record_type=FinancialRecordType.RENT_RECEIPT,
                reference=(
                    _as_text(document.payment_reference)
                    or _as_text(document.receipt_number)
                ),
                party_name=_as_text(document.tenant_name),
                party_id=_as_text(document.unit),
                amount=_as_decimal(document.amount_paid),
                currency=_as_text(document.currency),
                transaction_date=_parse_date(document.payment_date),
                source_status=(
                    document.validation_dimensions.reconciliation_status
                    if document.validation_dimensions
                    else None
                ),
                **common,
            )
        ]

    if document.document_type == "bank_statement":
        records: list[FinancialRecord] = []

        for tx_index, transaction in enumerate(document.bank_transactions):
            tx_reference = _as_text(transaction.reference)
            tx_date = _parse_date(transaction.date)
            tx_amount = _as_decimal(transaction.amount)

            # BankTransaction.amount is the source magnitude. Normalize
            # only when the source provides a recognized transaction type
            # or an explicit sign.
            sign_status = "unresolved"

            if tx_amount is not None:
                if transaction.transaction_type == "debit":
                    tx_amount = -abs(tx_amount)
                    sign_status = "validated_debit"
                elif transaction.transaction_type == "credit":
                    tx_amount = abs(tx_amount)
                    sign_status = "validated_credit"
                elif transaction.sign.strip() == "-":
                    tx_amount = -abs(tx_amount)
                    sign_status = "explicit_negative_sign"
                elif transaction.sign.strip() == "+":
                    tx_amount = abs(tx_amount)
                    sign_status = "explicit_positive_sign"

            tx_id = (
                f"transaction-{_stable_id(doc_id, str(tx_index), transaction.description, str(transaction.date), str(transaction.amount), tx_reference or '')}"
            )

            tx_metadata = dict(metadata)
            tx_metadata.update(
                {
                    "transaction_description": transaction.description,
                    "transaction_type": transaction.transaction_type,
                    "source_sign": transaction.sign,
                    "sign_status": sign_status,
                    "is_sign_validated": str(
                        transaction.is_sign_validated
                    ).lower(),
                    "sign_validation_note": (
                        transaction.sign_validation_note or ""
                    ),
                    "source_page_number": (
                        str(transaction.page_number)
                        if transaction.page_number is not None
                        else ""
                    ),
                }
            )

            if transaction.confidence is not None:
                tx_metadata["transaction_confidence"] = str(
                    transaction.confidence
                )

            tx_evidence = list(evidence)

            # Link each transaction to its originating statement. Do not
            # invent an extracted field value when the source lacks it.
            tx_evidence.append(
                EvidenceReference(
                    evidence_id=f"ev-{_stable_id(tx_id, 'bank_transaction')}",
                    document_id=doc_id,
                    field_name="bank_transaction",
                    extracted_value=transaction.description,
                    page_number=(
                        transaction.page_number
                        if transaction.page_number is not None
                        and transaction.page_number >= 1
                        else None
                    ),
                )
            )

            records.append(
                FinancialRecord(
                    record_id=tx_id,
                    record_type=FinancialRecordType.BANK_TRANSACTION,
                    document_id=doc_id,
                    reference=tx_reference,
                    party_name=_as_text(transaction.description),
                    amount=tx_amount,
                    currency=(
                        _as_text(transaction.currency)
                        or _as_text(document.currency)
                    ),
                    transaction_date=tx_date,
                    source_status=(
                        "sign_validated"
                        if transaction.is_sign_validated
                        else "sign_unverified"
                    ),
                    evidence=tx_evidence,
                    metadata=tx_metadata,
                )
            )

        return records

    # Leases and unknown document types remain available to the
    # Document Agent but are not silently reclassified as financial records.
    return []


def adapt_document_agent_output(
    result: DocumentAgentResult | dict[str, Any],
) -> list[FinancialRecord]:
    """
    Convert existing Document Agent output into normalized financial records.

    This function does not invoke extraction, OCR, or reconciliation.
    """
    if isinstance(result, dict):
        try:
            result = DocumentAgentResult.model_validate(result)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid Document Agent output: {exc}"
            ) from exc

    if result.status == "failed":
        raise ValueError(
            f"Cannot process failed Document Agent output: "
            f"{result.error or 'unknown error'}"
        )

    records: list[FinancialRecord] = []

    for index, document in enumerate(result.documents):
        records.extend(_normalize_document(document, index))

    return records


def adapt_domain_records(
    invoices: list[Any] | None = None,
    payments: list[Any] | None = None,
    expenses: list[Any] | None = None,
    bank_transactions: list[Any] | None = None,
) -> list[FinancialRecord]:
    """Convert domain model records into normalized FinancialRecord objects.

    Allows invoices, payments, expenses, and bank transactions from the database
    to be analyzed uniformly by transaction_matcher, duplicate_detector, etc.
    """
    records: list[FinancialRecord] = []

    if invoices:
        for inv in invoices:
            party = getattr(inv, "vendor_name", None) or getattr(inv, "vendor_id", None) or getattr(inv, "resident_id", None) or getattr(inv, "property_id", "")
            tx_date = getattr(inv, "issue_date", None) or getattr(inv, "invoice_date", None)
            records.append(
                FinancialRecord(
                    record_id=f"invoice-{inv.invoice_id}",
                    record_type=FinancialRecordType.INVOICE,
                    document_id=inv.invoice_id,
                    reference=inv.invoice_number,
                    party_name=party,
                    party_id=inv.resident_id,
                    amount=inv.amount,
                    currency=inv.currency,
                    transaction_date=tx_date,
                    due_date=inv.due_date,
                    source_status=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
                    metadata={"property_id": inv.property_id, "invoice_type": str(inv.invoice_type)},
                )
            )

    if payments:
        for pay in payments:
            ref = getattr(pay, "reference", None) or getattr(pay, "reference_number", None) or pay.payment_id
            records.append(
                FinancialRecord(
                    record_id=f"payment-{pay.payment_id}",
                    record_type=FinancialRecordType.RENT_RECEIPT,
                    document_id=pay.payment_id,
                    reference=ref,
                    party_name=pay.resident_id,
                    party_id=pay.resident_id,
                    amount=pay.amount,
                    currency=pay.currency,
                    transaction_date=pay.payment_date,
                    source_status=pay.status.value if hasattr(pay.status, "value") else str(pay.status),
                    metadata={"property_id": pay.property_id, "payment_method": str(pay.payment_method)},
                )
            )

    if expenses:
        for exp in expenses:
            exp_party = getattr(exp, "vendor_name", None) or getattr(exp, "vendor_id", None) or (exp.category.value if hasattr(exp.category, "value") else str(exp.category))
            records.append(
                FinancialRecord(
                    record_id=f"expense-{exp.expense_id}",
                    record_type=FinancialRecordType.INVOICE,
                    document_id=exp.expense_id,
                    reference=getattr(exp, "invoice_id", None) or getattr(exp, "document_id", None) or exp.expense_id,
                    party_name=exp_party,
                    amount=-abs(exp.amount),
                    currency=exp.currency,
                    transaction_date=exp.expense_date,
                    source_status=exp.status.value if hasattr(exp.status, "value") else str(exp.status),
                    metadata={"property_id": exp.property_id, "category": str(exp.category)},
                )
            )

    if bank_transactions:
        for tx in bank_transactions:
            records.append(
                FinancialRecord(
                    record_id=f"tx-{tx.transaction_id}",
                    record_type=FinancialRecordType.BANK_TRANSACTION,
                    document_id=tx.statement_id,
                    reference=tx.reference,
                    party_name=tx.description,
                    amount=tx.amount,
                    currency=tx.currency,
                    transaction_date=tx.transaction_date,
                    source_status=tx.match_status.value if hasattr(tx.match_status, "value") else str(tx.match_status),
                    metadata={"property_id": tx.property_id, "statement_id": tx.statement_id},
                )
            )

    return records

