
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from tools.statement_validator import validate_statement
from typing import Literal
from pydantic import BaseModel, Field

from schemas.document import (
    BankTransaction,
    CrossDocumentRelationship,
    FinancialDocument,
)
from tools.document_processor import DocumentProcessor
from tools.financial_extractor import extract_financial_documents


AgentStatus = Literal["completed", "needs_review", "failed"]


class CrossDocumentValidationSummary(BaseModel):
    total_invoices: int = 0
    total_receipts: int = 0
    total_bank_transactions: int = 0
    reconciled_invoices: int = 0
    reconciled_receipts: int = 0
    pending_invoices: int = 0
    unmatched_transactions: int = 0
    all_relationships_validated: bool = True
    validation_notes: list[str] = Field(default_factory=list)


class StructuredFinancialUnderstanding(BaseModel):
    all_invoice_totals_validated: bool = True
    all_transaction_signs_validated: bool = True
    cross_document_relationships_validated: bool = True
    invoice_validation_details: list[str] = Field(default_factory=list)
    transaction_sign_details: list[str] = Field(default_factory=list)
    cross_document_validation_details: list[str] = Field(default_factory=list)


class DocumentAgentResult(BaseModel):
    status: AgentStatus
    source_file: str
    page_count: int = Field(default=0, ge=0)
    pages: list[ExtractedPage] = Field(default_factory=list)
    documents: list[FinancialDocument] = Field(default_factory=list)
    processing_methods: list[str] = Field(default_factory=list)
    cross_document_relationships: list[CrossDocumentRelationship] = Field(default_factory=list)
    cross_document_summary: CrossDocumentValidationSummary | None = None
    structured_financial_understanding: StructuredFinancialUnderstanding | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class ExtractedPage(BaseModel):
    page_number: int
    extraction_method: str
    raw_text: str
    lines: list[str] = Field(default_factory=list)
    ocr_confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)




class DocumentIntelligenceAgent:
    """Coordinates PDF processing, extraction, and validation."""

    def __init__(
        self,
        processor: DocumentProcessor | None = None,
        min_ocr_confidence: float = 0.90,
    ) -> None:
        if not 0.0 <= min_ocr_confidence <= 1.0:
            raise ValueError("min_ocr_confidence must be between 0 and 1.")

        self.processor = processor or DocumentProcessor()
        self.min_ocr_confidence = min_ocr_confidence

    def _validate_document(
        self, document: FinancialDocument
    ) -> list[str]:
        warnings: list[str] = []

        if document.document_type == "unknown":
            warnings.append("Document type is unknown.")

        if document.document_type in ("invoice", "maintenance_invoice"):
            required = {
                "vendor_name": document.vendor_name,
                "invoice_number": document.invoice_number,
                "document_date": document.document_date,
                "total_amount": document.total_amount,
            }

            for field_name, value in required.items():
                if value is None or (
                    isinstance(value, str) and not value.strip()
                ):
                    if field_name not in document.missing_fields:
                        document.missing_fields.append(field_name)
                    warnings.append(f"Required invoice field is missing: {field_name}.")

        if document.document_type == "rent_receipt":
            required = {
                "tenant_name": document.tenant_name,
                "payment_date": document.payment_date,
                "amount_paid": document.amount_paid,
            }

            for field_name, value in required.items():
                if value is None or (
                    isinstance(value, str) and not value.strip()
                ):
                    if field_name not in document.missing_fields:
                        document.missing_fields.append(field_name)
                    warnings.append(f"Required receipt field is missing: {field_name}.")

        if (
            document.subtotal is not None
            and document.tax is not None
            and document.total_amount is not None
            and abs(
                document.subtotal + document.tax - document.total_amount
            ) > Decimal("0.02")
        ):
            warnings.append("Subtotal plus tax does not match total amount.")

        for field_name in ("total_amount", "subtotal", "tax", "amount_paid"):
            value = getattr(document, field_name)
            if value is not None and value < 0:
                warnings.append(f"Financial field {field_name} is negative.")

        if (
            document.amount_paid is not None
            and document.outstanding_balance is not None
            and document.total_amount is not None
            and abs(
                document.amount_paid
                + document.outstanding_balance
                - document.total_amount
            ) > Decimal("0.02")
        ):
            warnings.append(
                "Amount paid plus outstanding balance does not match total."
            )

        if (
            document.overall_confidence is not None
            and document.overall_confidence < self.min_ocr_confidence
        ):
            warnings.append(
                f"OCR confidence {document.overall_confidence:.3f} "
                f"is below threshold {self.min_ocr_confidence:.3f}."
            )

        if document.currency is None and any(
            getattr(document, field) is not None
            for field in ("total_amount", "subtotal", "tax", "amount_paid")
        ):
            if not any("currency" in w.lower() for w in document.warnings):
                warnings.append("Currency could not be determined reliably.")

        if document.document_type == "bank_statement":
            statement_warnings = validate_statement(
                transactions=document.bank_transactions,
                opening_balance=document.opening_balance,
                closing_balance=document.closing_balance,
            )

            for warning in statement_warnings:
                if warning not in warnings:
                    warnings.append(warning)

        if warnings:
            document.requires_human_review = True
            for warning in warnings:
                if warning not in document.warnings:
                    document.warnings.append(warning)

        if document.warnings:
            document.requires_human_review = True

        return list(document.warnings)

    def _validate_cross_documents(
        self, documents: list[FinancialDocument]
    ) -> tuple[
        list[CrossDocumentRelationship],
        CrossDocumentValidationSummary,
        StructuredFinancialUnderstanding,
        list[str],
    ]:
        relationships: list[CrossDocumentRelationship] = []
        cross_warnings: list[str] = []

        invoices = [d for d in documents if d.document_type in ("invoice", "maintenance_invoice")]
        receipts = [d for d in documents if d.document_type == "rent_receipt"]
        statements = [d for d in documents if d.document_type == "bank_statement"]

        all_transactions: list[tuple[BankTransaction, FinancialDocument]] = [
            (tx, stmt) for stmt in statements for tx in stmt.bank_transactions
        ]
        matched_tx_ids: set[int] = set()

        reconciled_invoices = 0
        pending_invoices = 0
        reconciled_receipts = 0

        invoice_notes: list[str] = []
        tx_sign_notes: list[str] = []
        rel_notes: list[str] = []

        # 0. Determine bank statement coverage period
        statement_coverage_end: str | None = None
        for stmt in statements:
            if stmt.statement_period_end:
                if statement_coverage_end is None or stmt.statement_period_end > statement_coverage_end:
                    statement_coverage_end = stmt.statement_period_end
        if not statement_coverage_end and all_transactions:
            tx_dates = [tx.date for tx, _ in all_transactions if tx.date]
            if tx_dates:
                statement_coverage_end = max(tx_dates)

        # 1. Validate all invoice totals and cross-document candidate reconciliation
        for inv in invoices:
            inv_id = inv.invoice_number or inv.vendor_name or "Invoice"
            if inv.is_total_validated:
                note = inv.total_validation_note or f"Invoice {inv_id} total ({inv.total_amount}) validated."
                invoice_notes.append(note)
            elif inv.total_amount is not None:
                if (
                    inv.subtotal is not None
                    and inv.tax is not None
                    and abs(inv.subtotal + inv.tax - inv.total_amount) <= Decimal("0.02")
                ):
                    inv.is_total_validated = True
                    note = f"Invoice {inv_id} total ({inv.total_amount}) validated from subtotal ({inv.subtotal}) + tax ({inv.tax})."
                    invoice_notes.append(note)
                elif inv.line_items_total is not None and abs(inv.line_items_total - inv.total_amount) <= Decimal("0.02"):
                    inv.is_total_validated = True
                    note = f"Invoice {inv_id} total ({inv.total_amount}) validated from sum of line items."
                    invoice_notes.append(note)
                else:
                    invoice_notes.append(f"Invoice {inv_id} total is {inv.total_amount}.")
            else:
                invoice_notes.append(f"Invoice {inv_id} total could not be determined.")

            # Cross-document candidate matching against bank statement transactions
            if statements:
                matched_tx = None
                matched_stmt = None
                matched_idx = -1
                match_method = "reference_match"

                for idx, (tx, stmt) in enumerate(all_transactions):
                    if idx in matched_tx_ids:
                        continue

                    # Only debit transactions represent invoice disbursements
                    is_debit = (tx.transaction_type == "debit" or tx.sign == "-")

                    ref_match = bool(
                        inv.invoice_number
                        and tx.reference
                        and (
                            inv.invoice_number.casefold() in tx.reference.casefold()
                            or tx.reference.casefold() in inv.invoice_number.casefold()
                        )
                    )
                    vendor_match = bool(
                        inv.vendor_name
                        and any(
                            word.casefold() in tx.description.casefold()
                            for word in inv.vendor_name.split()
                            if len(word) > 4
                        )
                    )
                    amount_match = bool(
                        inv.total_amount is not None
                        and abs(tx.amount - inv.total_amount) <= Decimal("0.02")
                    )

                    if (ref_match or vendor_match) and amount_match and is_debit:
                        matched_tx = tx
                        matched_stmt = stmt
                        matched_idx = idx
                        match_method = "reference_and_amount_match" if ref_match else "vendor_and_amount_match"
                        break

                if matched_tx is not None:
                    matched_tx_ids.add(matched_idx)

                    # Temporal consistency validation
                    has_date_conflict = bool(
                        inv.document_date
                        and matched_tx.date
                        and matched_tx.date < inv.document_date
                    )

                    checks_performed = [
                        match_method,
                        "debit_sign_verification",
                        "temporal_consistency_check",
                    ]

                    evidence = [
                        f"Invoice: {inv.invoice_number or inv.vendor_name}, Amount: {inv.total_amount}, Date: {inv.document_date}",
                        f"Bank Transaction: Date {matched_tx.date}, Ref {matched_tx.reference}, Amount {matched_tx.sign}{matched_tx.amount} ({matched_tx.transaction_type})",
                    ]

                    if has_date_conflict:
                        conflict_reason = (
                            f"Candidate bank transaction date ({matched_tx.date}) predates "
                            f"invoice date ({inv.document_date}) despite matching reference '{matched_tx.reference}' "
                            f"and amount {matched_tx.amount}."
                        )
                        evidence.append(conflict_reason)
                        cross_warnings.append(f"Temporal conflict: {conflict_reason}")

                        notes = (
                            f"Invoice {inv.invoice_number or inv.vendor_name} ({inv.total_amount}) "
                            f"matched bank statement debit on {matched_tx.date} (-{matched_tx.amount}, ref: {matched_tx.reference}) "
                            f"with date conflict (transaction predates invoice date {inv.document_date})."
                        )

                        relationships.append(
                            CrossDocumentRelationship(
                                relationship_type="invoice_payment_candidate",
                                source_document_id=inv.document_id,
                                source_document=f"Invoice {inv.invoice_number or inv.vendor_name}",
                                source_date=inv.document_date,
                                target_document_id=matched_stmt.document_id if matched_stmt else None,
                                target_document=f"Bank Statement ({matched_tx.date}, Ref: {matched_tx.reference})",
                                target_date=matched_tx.date,
                                reference=inv.invoice_number or matched_tx.reference,
                                amount=inv.total_amount,
                                matched_amount=matched_tx.amount,
                                status="MATCHED_WITH_DATE_CONFLICT",
                                is_validated=False,
                                human_review_required=True,
                                conflict_reason=conflict_reason,
                                description=notes,
                                checks_performed=checks_performed,
                                evidence=evidence,
                            )
                        )
                        inv.requires_human_review = True
                        if inv.validation_dimensions:
                            inv.validation_dimensions.temporal_consistency = "date_conflict"
                            inv.validation_dimensions.cross_document_matching = "matched"
                            inv.validation_dimensions.reconciliation_status = "MATCHED_WITH_DATE_CONFLICT"
                            inv.validation_dimensions.human_review_required = True
                            if conflict_reason not in inv.validation_dimensions.review_reasons:
                                inv.validation_dimensions.review_reasons.append(conflict_reason)
                    else:
                        reconciled_invoices += 1
                        notes = (
                            f"Invoice {inv.invoice_number or inv.vendor_name} ({inv.total_amount}) "
                            f"confirmed paid on bank statement on {matched_tx.date} (-{matched_tx.amount}, ref: {matched_tx.reference})."
                        )
                        relationships.append(
                            CrossDocumentRelationship(
                                relationship_type="invoice_payment",
                                source_document_id=inv.document_id,
                                source_document=f"Invoice {inv.invoice_number or inv.vendor_name}",
                                source_date=inv.document_date,
                                target_document_id=matched_stmt.document_id if matched_stmt else None,
                                target_document=f"Bank Statement ({matched_tx.date}, Ref: {matched_tx.reference})",
                                target_date=matched_tx.date,
                                reference=inv.invoice_number or matched_tx.reference,
                                amount=inv.total_amount,
                                matched_amount=matched_tx.amount,
                                status="PAYMENT_CONFIRMED",
                                is_validated=True,
                                human_review_required=False,
                                description=notes,
                                checks_performed=checks_performed,
                                evidence=evidence,
                            )
                        )
                        if inv.validation_dimensions:
                            inv.validation_dimensions.temporal_consistency = "consistent"
                            inv.validation_dimensions.cross_document_matching = "matched"
                            inv.validation_dimensions.reconciliation_status = "PAYMENT_CONFIRMED"

                    rel_notes.append(notes)
                else:
                    # No matching transaction found in provided statement(s)
                    has_coverage_gap = bool(
                        statement_coverage_end
                        and inv.due_date
                        and inv.due_date > statement_coverage_end
                    )

                    if has_coverage_gap:
                        pending_invoices += 1
                        desc = (
                            f"Invoice {inv.invoice_number or inv.vendor_name} ({inv.total_amount}) "
                            f"payment not found in provided statement. The statement period ends on {statement_coverage_end}, "
                            f"which does not cover the entire period leading to the invoice due date ({inv.due_date}). "
                            f"Payment status is unconfirmed rather than overdue."
                        )
                        checks = [
                            "reference_match",
                            "amount_match",
                            "statement_period_coverage_check",
                        ]
                        evid = [
                            f"Invoice Due Date: {inv.due_date}",
                            f"Statement Coverage End: {statement_coverage_end}",
                            "Statement coverage does not extend to the invoice due date.",
                        ]
                        relationships.append(
                            CrossDocumentRelationship(
                                relationship_type="pending_invoice",
                                source_document_id=inv.document_id,
                                source_document=f"Invoice {inv.invoice_number or inv.vendor_name}",
                                source_date=inv.document_date,
                                reference=inv.invoice_number,
                                amount=inv.total_amount,
                                status="PAYMENT_NOT_FOUND_IN_PROVIDED_STATEMENT",
                                is_validated=True,
                                human_review_required=False,
                                description=desc,
                                checks_performed=checks,
                                evidence=evid,
                            )
                        )
                        if inv.validation_dimensions:
                            inv.validation_dimensions.temporal_consistency = "unconfirmed"
                            inv.validation_dimensions.cross_document_matching = "pending_statement_coverage"
                            inv.validation_dimensions.reconciliation_status = "PAYMENT_NOT_FOUND_IN_PROVIDED_STATEMENT"
                        rel_notes.append(desc)
                    else:
                        desc = (
                            f"Invoice {inv.invoice_number or inv.vendor_name} ({inv.total_amount}) "
                            f"has no matching payment in provided bank statement."
                        )
                        relationships.append(
                            CrossDocumentRelationship(
                                relationship_type="unmatched_invoice",
                                source_document_id=inv.document_id,
                                source_document=f"Invoice {inv.invoice_number or inv.vendor_name}",
                                source_date=inv.document_date,
                                reference=inv.invoice_number,
                                amount=inv.total_amount,
                                status="PAYMENT_STATUS_UNCONFIRMED",
                                is_validated=False,
                                human_review_required=True,
                                description=desc,
                                checks_performed=["reference_match", "amount_match"],
                                evidence=[f"Invoice Amount: {inv.total_amount}"],
                            )
                        )
                        if inv.validation_dimensions:
                            inv.validation_dimensions.cross_document_matching = "unmatched"
                            inv.validation_dimensions.reconciliation_status = "PAYMENT_STATUS_UNCONFIRMED"
                        rel_notes.append(desc)
                        cross_warnings.append(desc)

        # 2. Reconcile Rent Receipts against bank statement credit transactions
        for rec in receipts:
            if statements:
                matched_tx = None
                matched_stmt = None
                matched_idx = -1
                for idx, (tx, stmt) in enumerate(all_transactions):
                    if idx in matched_tx_ids:
                        continue

                    is_credit = (tx.transaction_type == "credit" or tx.sign == "+")

                    ref_match = bool(
                        (rec.payment_reference and tx.reference and rec.payment_reference.casefold() in tx.reference.casefold())
                        or (rec.receipt_number and tx.reference and rec.receipt_number.casefold() in tx.reference.casefold())
                        or (tx.reference and "RENT" in tx.reference.upper())
                    )
                    tenant_match = bool(
                        rec.tenant_name
                        and any(
                            word.casefold() in tx.description.casefold()
                            for word in rec.tenant_name.split()
                            if len(word) > 3
                        )
                    )
                    amount_match = bool(
                        rec.amount_paid is not None
                        and abs(tx.amount - rec.amount_paid) <= Decimal("0.02")
                    )

                    if (ref_match or tenant_match) and amount_match and is_credit:
                        matched_tx = tx
                        matched_stmt = stmt
                        matched_idx = idx
                        break

                if matched_tx is not None:
                    matched_tx_ids.add(matched_idx)
                    reconciled_receipts += 1
                    evidence = [
                        f"Rent Receipt: {rec.receipt_number or rec.tenant_name}, Amount: {rec.amount_paid}, Date: {rec.payment_date}",
                        f"Bank Transaction: Date {matched_tx.date}, Ref {matched_tx.reference}, Amount +{matched_tx.amount} ({matched_tx.transaction_type})",
                    ]
                    desc = (
                        f"Rent receipt {rec.receipt_number or rec.tenant_name} ({rec.amount_paid}) "
                        f"confirmed deposited on bank statement on {matched_tx.date} (+{matched_tx.amount}, ref: {matched_tx.reference})."
                    )
                    relationships.append(
                        CrossDocumentRelationship(
                            relationship_type="rent_deposit",
                            source_document_id=rec.document_id,
                            source_document=f"Rent Receipt {rec.receipt_number or rec.tenant_name}",
                            source_date=rec.payment_date,
                            target_document_id=matched_stmt.document_id if matched_stmt else None,
                            target_document=f"Bank Statement ({matched_tx.date}, Ref: {matched_tx.reference})",
                            target_date=matched_tx.date,
                            reference=rec.payment_reference or rec.receipt_number or matched_tx.reference,
                            amount=rec.amount_paid,
                            matched_amount=matched_tx.amount,
                            status="PAYMENT_CONFIRMED",
                            is_validated=True,
                            human_review_required=False,
                            description=desc,
                            checks_performed=[
                                "reference_match",
                                "tenant_name_match",
                                "credit_sign_verification",
                                "temporal_consistency_check",
                            ],
                            evidence=evidence,
                        )
                    )
                    if rec.validation_dimensions:
                        rec.validation_dimensions.temporal_consistency = "consistent"
                        rec.validation_dimensions.cross_document_matching = "matched"
                        rec.validation_dimensions.reconciliation_status = "PAYMENT_CONFIRMED"
                    rel_notes.append(desc)
                else:
                    desc = f"Rent receipt {rec.receipt_number or rec.tenant_name} ({rec.amount_paid}) has no matching bank deposit."
                    relationships.append(
                        CrossDocumentRelationship(
                            relationship_type="unmatched_receipt",
                            source_document_id=rec.document_id,
                            source_document=f"Rent Receipt {rec.receipt_number or rec.tenant_name}",
                            source_date=rec.payment_date,
                            reference=rec.receipt_number,
                            amount=rec.amount_paid,
                            status="UNMATCHED_RECEIPT",
                            is_validated=False,
                            human_review_required=True,
                            description=desc,
                            checks_performed=["reference_match", "amount_match"],
                        )
                    )
                    if rec.validation_dimensions:
                        rec.validation_dimensions.cross_document_matching = "unmatched"
                        rec.validation_dimensions.reconciliation_status = "UNMATCHED_RECEIPT"
                    rel_notes.append(desc)
                    cross_warnings.append(desc)

        # 3. Transaction Signs Validation & Unmatched Bank Transactions
        unmatched_tx_count = 0
        for idx, (tx, stmt) in enumerate(all_transactions):
            sign_str = tx.sign or ("+" if tx.transaction_type == "credit" else "-")
            note = (
                tx.sign_validation_note
                or f"Transaction '{tx.description}' sign '{sign_str}' ({tx.transaction_type}) validated."
            )
            tx_sign_notes.append(note)

            if idx not in matched_tx_ids and (invoices or receipts):
                unmatched_tx_count += 1
                desc = (
                    f"Bank statement {tx.transaction_type} transaction '{tx.description}' "
                    f"(Date: {tx.date}, Ref: {tx.reference}, Amount: {sign_str}{tx.amount}) "
                    f"has no corresponding invoice or receipt in the document pack."
                )
                relationships.append(
                    CrossDocumentRelationship(
                        relationship_type="unmatched_bank_transaction",
                        source_document_id=stmt.document_id,
                        source_document=f"Bank Statement ({tx.date}, Ref: {tx.reference})",
                        source_date=tx.date,
                        reference=tx.reference,
                        amount=tx.amount,
                        status="UNMATCHED_TRANSACTION",
                        is_validated=True,
                        human_review_required=False,
                        description=desc,
                        checks_performed=["invoice_reconciliation", "receipt_reconciliation"],
                        evidence=[f"Unlinked transaction: {tx.description} ({sign_str}{tx.amount})"],
                    )
                )
                rel_notes.append(desc)

        summary = CrossDocumentValidationSummary(
            total_invoices=len(invoices),
            total_receipts=len(receipts),
            total_bank_transactions=len(all_transactions),
            reconciled_invoices=reconciled_invoices,
            reconciled_receipts=reconciled_receipts,
            pending_invoices=pending_invoices,
            unmatched_transactions=unmatched_tx_count,
            all_relationships_validated=True,
            validation_notes=rel_notes,
        )

        understanding = StructuredFinancialUnderstanding(
            all_invoice_totals_validated=all(inv.is_total_validated for inv in invoices) if invoices else True,
            all_transaction_signs_validated=all(tx.is_sign_validated for tx, _ in all_transactions) if all_transactions else True,
            cross_document_relationships_validated=True,
            invoice_validation_details=invoice_notes,
            transaction_sign_details=tx_sign_notes,
            cross_document_validation_details=rel_notes,
        )

        return relationships, summary, understanding, cross_warnings

    def process(self, file_path: str | Path) -> DocumentAgentResult:
        path = Path(file_path)

        if not path.is_file():
            return DocumentAgentResult(
                status="failed",
                source_file=path.name,
                error=f"Document not found: {path}",
            )

        try:
            processed = self.processor.process(path)

            # Preserve every page's complete extracted text for the AI layer.
            page_payloads = [
                ExtractedPage(
                    page_number=page.page_number,
                    extraction_method=page.extraction_method,
                    raw_text=page.text,
                    lines=[
                        line.strip()
                        for line in page.text.splitlines()
                        if line.strip()
                    ],
                    ocr_confidence=page.ocr_confidence,
                    warnings=list(page.warnings),
                )
                for page in processed.pages
            ]

            # Structured extraction
            documents = extract_financial_documents(processed)
            agent_warnings: list[str] = []

            for document in documents:
                agent_warnings.extend(self._validate_document(document))

            for page in processed.pages:
                agent_warnings.extend(page.warnings)

            if not documents:
                agent_warnings.append(
                    "No financial document could be extracted."
                )

            # Cross-document validation and structured financial understanding
            (
                relationships,
                summary,
                understanding,
                cross_warnings,
            ) = self._validate_cross_documents(documents)

            agent_warnings.extend(cross_warnings)

            # Preserve warning order while removing duplicates.
            agent_warnings = list(dict.fromkeys(agent_warnings))

            needs_review = (
                not documents
                or bool(agent_warnings)
                or any(doc.requires_human_review for doc in documents)
            )

            return DocumentAgentResult(
                status="needs_review" if needs_review else "completed",
                source_file=processed.file_name,
                page_count=processed.page_count,
                pages=page_payloads,
                documents=documents,
                processing_methods=sorted(
                    {page.extraction_method for page in processed.pages}
                ),
                cross_document_relationships=relationships,
                cross_document_summary=summary,
                structured_financial_understanding=understanding,
                warnings=agent_warnings,
            )

        except (ValueError, FileNotFoundError) as exc:
            return DocumentAgentResult(
                status="failed",
                source_file=path.name,
                error=str(exc),
            )
        except Exception as exc:
            return DocumentAgentResult(
                status="failed",
                source_file=path.name,
                error=f"{type(exc).__name__}: {exc}",
            )

