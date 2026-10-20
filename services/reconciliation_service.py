"""Bank Statement Import and Reconciliation Service.

Handles CSV parsing, transaction normalization, matching against domain records
(invoices, payments, expenses), discrepancy detection, and review decisions.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional, Tuple

from repository.domain_repository import DomainRepository, EntityNotFoundError, IntegrityConflictError
from schemas.domain import (
    BankStatementRecord,
    BankTransactionMatchStatus,
    BankTransactionRecord,
    ExpenseRecord,
    InvoiceRecord,
    MatchDecisionRequest,
    PaymentRecord,
)


class ReconciliationError(Exception):
    """Base exception for reconciliation errors."""
    pass


class StatementValidationError(ReconciliationError):
    """Raised when CSV validation fails."""
    pass


def _hash_content(content: bytes | str) -> str:
    """Compute SHA-256 hash of content."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _parse_date(val: str) -> date:
    """Parse multiple common date formats into a date object."""
    cleaned = val.strip()
    if not cleaned:
        raise ValueError("Empty date string")

    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d %b %Y",
        "%d-%b-%Y",
        "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"Unrecognized date format: {val}")


def _parse_decimal(val: str) -> Decimal:
    """Parse currency strings into Decimal."""
    cleaned = re.sub(r"[^\d.-]", "", val.strip())
    if not cleaned:
        raise ValueError("Empty amount string")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Invalid monetary amount: {val}")


class ReconciliationService:
    def __init__(self, domain_repo: DomainRepository):
        self.repo = domain_repo

    def parse_statement_csv(
        self,
        csv_content: str | bytes,
        filename: str = "statement.csv",
    ) -> Tuple[List[dict], Decimal, Decimal, Decimal, Optional[date], Optional[date]]:
        """
        Parse CSV content and validate columns and row data.
        Returns:
            (parsed_rows, opening_balance, closing_balance, net_amount, start_date, end_date)
        """
        if isinstance(csv_content, bytes):
            text = csv_content.decode("utf-8", errors="replace")
        else:
            text = csv_content

        if not text.strip():
            raise StatementValidationError("The statement CSV file is empty.")

        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration:
            raise StatementValidationError("The statement CSV file has no header row.")

        header_normalized = [c.strip().lower().replace(" ", "_") for c in header]

        has_date = any(k in header_normalized for k in ["date", "txn_date", "transaction_date"])
        has_desc = any(k in header_normalized for k in ["description", "desc", "memo", "narration", "details"])
        has_amount = any(k in header_normalized for k in ["amount", "net_amount"])
        has_debit_credit = (
            any(k in header_normalized for k in ["debit", "withdrawal"])
            and any(k in header_normalized for k in ["credit", "deposit"])
        )

        if not has_date:
            raise StatementValidationError("Missing required column: 'Date' or 'transaction_date'")
        if not has_desc:
            raise StatementValidationError("Missing required column: 'Description' or 'memo'")
        if not (has_amount or has_debit_credit):
            raise StatementValidationError("Statement must have 'Amount' or both 'Debit' and 'Credit' columns")

        def find_col(candidates: list[str]) -> Optional[int]:
            for cand in candidates:
                if cand in header_normalized:
                    return header_normalized.index(cand)
            return None

        idx_date = find_col(["date", "txn_date", "transaction_date"])
        idx_desc = find_col(["description", "desc", "memo", "narration", "details"])
        idx_ref = find_col(["reference", "ref", "ref_no", "chq_no", "transaction_id"])
        idx_amount = find_col(["amount", "net_amount"])
        idx_debit = find_col(["debit", "withdrawal"])
        idx_credit = find_col(["credit", "deposit"])
        idx_balance = find_col(["balance", "running_balance", "balance_after"])

        parsed_rows = []
        net_amount = Decimal("0.00")
        first_balance: Optional[Decimal] = None
        last_balance: Optional[Decimal] = None
        dates: list[date] = []

        row_num = 1
        for row in reader:
            row_num += 1
            if not row or not any(field.strip() for field in row):
                continue

            try:
                date_str = row[idx_date] if idx_date < len(row) else ""
                txn_date = _parse_date(date_str)
                dates.append(txn_date)
            except Exception as e:
                raise StatementValidationError(f"Row {row_num}: Invalid transaction date: {e}")

            desc = row[idx_desc].strip() if idx_desc is not None and idx_desc < len(row) else ""
            ref = row[idx_ref].strip() if idx_ref is not None and idx_ref < len(row) else ""

            try:
                if idx_amount is not None and idx_amount < len(row) and row[idx_amount].strip():
                    amt = _parse_decimal(row[idx_amount])
                elif idx_debit is not None and idx_credit is not None:
                    debit_str = row[idx_debit].strip() if idx_debit < len(row) else ""
                    credit_str = row[idx_credit].strip() if idx_credit < len(row) else ""
                    if debit_str and credit_str:
                        amt = _parse_decimal(credit_str) - _parse_decimal(debit_str)
                    elif debit_str:
                        amt = -abs(_parse_decimal(debit_str))
                    elif credit_str:
                        amt = abs(_parse_decimal(credit_str))
                    else:
                        amt = Decimal("0.00")
                else:
                    raise StatementValidationError(f"Row {row_num}: No valid amount or debit/credit specified.")
            except Exception as e:
                raise StatementValidationError(f"Row {row_num}: Invalid amount format: {e}")

            bal_val: Optional[Decimal] = None
            if idx_balance is not None and idx_balance < len(row) and row[idx_balance].strip():
                try:
                    bal_val = _parse_decimal(row[idx_balance])
                    if first_balance is None:
                        first_balance = bal_val
                    last_balance = bal_val
                except Exception:
                    pass

            net_amount += amt
            parsed_rows.append({
                "transaction_date": txn_date,
                "description": desc,
                "reference": ref,
                "amount": amt,
                "transaction_type": "credit" if amt >= Decimal("0.00") else "debit",
                "balance_after": bal_val,
                "currency": "USD",
            })

        if not parsed_rows:
            raise StatementValidationError("The statement CSV file contains no transaction rows.")

        opening_balance = (first_balance - parsed_rows[0]["amount"]) if (first_balance is not None and len(parsed_rows) > 0) else Decimal("0.00")
        closing_balance = last_balance if last_balance is not None else (opening_balance + net_amount)
        start_date = min(dates) if dates else None
        end_date = max(dates) if dates else None

        return parsed_rows, opening_balance, closing_balance, net_amount, start_date, end_date

    def import_bank_statement(
        self,
        property_id: str,
        csv_content: str | bytes,
        filename: str = "statement.csv",
        account_identifier: str = "XXXX-XXXX",
        imported_by: str = "system",
    ) -> Tuple[BankStatementRecord, List[BankTransactionRecord]]:
        """
        Import and validate bank statement CSV, normalize transactions,
        run auto-matching against property financial records, and persist to database.
        """
        file_hash = _hash_content(csv_content)

        parsed_rows, opening_bal, closing_bal, _, start_date, end_date = self.parse_statement_csv(csv_content, filename)

        statement_record = self.repo.create_bank_statement(
            property_id=property_id,
            filename=filename,
            opening_balance=opening_bal,
            closing_balance=closing_bal,
            statement_start_date=start_date,
            statement_end_date=end_date,
            total_transactions=len(parsed_rows),
            file_checksum=file_hash,
            account_identifier=account_identifier,
        )

        # Batch insert transactions
        transactions = self.repo.create_bank_transactions(
            property_id=property_id,
            statement_id=statement_record.statement_id,
            transactions=parsed_rows,
        )

        # Auto-match transactions against property domain records
        payments = self.repo.list_payments(property_id=property_id)
        invoices = self.repo.list_invoices(property_id=property_id)
        expenses = self.repo.list_expenses(property_id=property_id)

        # Track entities that are already matched to prevent duplicate matching
        existing_matched = self.repo.list_bank_transactions(property_id=property_id, match_status=BankTransactionMatchStatus.MATCHED)
        matched_entity_ids = {tx.matched_entity_id for tx in existing_matched if tx.matched_entity_id}

        matched_records: List[BankTransactionRecord] = []
        for tx in transactions:
            amt = tx.amount
            tx_date = tx.transaction_date
            desc = tx.description
            ref = tx.reference or ""

            best_match = None
            if tx.transaction_type == "credit":
                avail_payments = [p for p in payments if p.payment_id not in matched_entity_ids]
                avail_invoices = [inv for inv in invoices if inv.invoice_id not in matched_entity_ids]
                best_match = self._find_credit_match(amt, tx_date, desc, ref, avail_payments, avail_invoices)
            else:
                avail_expenses = [e for e in expenses if e.expense_id not in matched_entity_ids]
                avail_invoices = [inv for inv in invoices if inv.invoice_id not in matched_entity_ids]
                best_match = self._find_debit_match(amt, tx_date, desc, ref, avail_expenses, avail_invoices)

            if best_match:
                matched_type, matched_id, confidence, notes = best_match
                matched_entity_ids.add(matched_id)
                updated = self.repo.update_bank_transaction_match(
                    property_id=property_id,
                    transaction_id=tx.transaction_id,
                    match_status=BankTransactionMatchStatus.PARTIALLY_MATCHED if confidence < 0.95 else BankTransactionMatchStatus.MATCHED,
                    matched_entity_type=matched_type,
                    matched_entity_id=matched_id,
                    match_confidence=float(confidence),
                    match_notes=notes,
                    reconciled_by="auto-reconciliation",
                )
                matched_records.append(updated)
            else:
                matched_records.append(tx)

        return statement_record, matched_records

    def _find_credit_match(
        self,
        amount: Decimal,
        tx_date: date,
        description: str,
        reference: str,
        payments: List[PaymentRecord],
        invoices: List[InvoiceRecord],
    ) -> Optional[Tuple[str, str, float, str]]:
        norm_ref = reference.strip().lower()
        norm_desc = description.strip().lower()

        # 1. Exact payment reference match
        if norm_ref:
            for p in payments:
                p_ref = getattr(p, "reference_number", None) or getattr(p, "reference", None)
                if p_ref and p_ref.strip().lower() == norm_ref:
                    return ("payment", p.payment_id, 0.98, f"Exact payment reference match: {p_ref}")

        # 2. Match against payments with exact amount & close date (+- 7 days)
        for p in payments:
            if abs(p.amount - amount) <= Decimal("0.01"):
                day_diff = abs((p.payment_date - tx_date).days)
                if day_diff <= 7:
                    conf = 0.90 if day_diff <= 3 else 0.80
                    return ("payment", p.payment_id, conf, f"Amount match with payment on {p.payment_date} ({day_diff} days apart)")

        # 3. Match against rent invoices
        for inv in invoices:
            if inv.invoice_number and norm_ref and inv.invoice_number.strip().lower() == norm_ref:
                return ("invoice", inv.invoice_id, 0.95, f"Exact invoice reference match: {inv.invoice_number}")
            if abs(inv.amount - amount) <= Decimal("0.01"):
                day_diff = abs((inv.issue_date - tx_date).days) if hasattr(inv, "issue_date") and inv.issue_date else 10
                if day_diff <= 10:
                    return ("invoice", inv.invoice_id, 0.85, f"Amount match with invoice '{inv.invoice_number}' ({day_diff} days apart)")

        # 4. Description match with resident ID
        for p in payments:
            if p.resident_id and p.resident_id.lower() in norm_desc:
                return ("payment", p.payment_id, 0.75, f"Description mentions resident {p.resident_id}")

        return None

    def _find_debit_match(
        self,
        amount: Decimal,
        tx_date: date,
        description: str,
        reference: str,
        expenses: List[ExpenseRecord],
        invoices: List[InvoiceRecord],
    ) -> Optional[Tuple[str, str, float, str]]:
        abs_amount = abs(amount)
        norm_ref = reference.strip().lower()
        norm_desc = description.strip().lower()

        # 1. Match against expenses with source invoice or exact ID
        for exp in expenses:
            exp_ref = getattr(exp, "invoice_id", None) or getattr(exp, "document_id", None) or exp.expense_id
            if exp_ref and norm_ref and exp_ref.strip().lower() == norm_ref:
                return ("expense", exp.expense_id, 0.98, f"Exact invoice reference match in expense: {exp_ref}")
            if abs(exp.amount - abs_amount) <= Decimal("0.01"):
                day_diff = abs((exp.expense_date - tx_date).days)
                if day_diff <= 7:
                    conf = 0.90 if day_diff <= 3 else 0.80
                    return ("expense", exp.expense_id, conf, f"Amount match with expense {exp.expense_id} ({day_diff} days apart)")

        # 2. Match against vendor invoices
        for inv in invoices:
            if inv.invoice_number and norm_ref and inv.invoice_number.strip().lower() == norm_ref:
                return ("invoice", inv.invoice_id, 0.96, f"Exact vendor invoice match: {inv.invoice_number}")
            if abs(inv.amount - abs_amount) <= Decimal("0.01"):
                inv_date = getattr(inv, "issue_date", None) or getattr(inv, "invoice_date", None)
                day_diff = abs((inv_date - tx_date).days) if inv_date else 10
                if day_diff <= 10:
                    return ("invoice", inv.invoice_id, 0.85, f"Amount match with invoice {inv.invoice_number}")

        return None

    def decide_match(
        self,
        property_id: str,
        transaction_id: str,
        decision_request: MatchDecisionRequest,
        decided_by: str = "system",
    ) -> BankTransactionRecord:
        """
        Apply a user decision to a proposed match (approve, reject, or correct).
        """
        tx = self.repo.get_bank_transaction(property_id, transaction_id)
        if not tx:
            raise EntityNotFoundError(f"Bank transaction '{transaction_id}' not found.")

        decision = decision_request.decision.lower().strip()
        now_iso = datetime.now().isoformat()

        if decision == "approve":
            matched_type = tx.matched_entity_type or decision_request.matched_entity_type
            matched_id = tx.matched_entity_id or decision_request.matched_entity_id
            if not matched_type or not matched_id:
                raise ValueError("Cannot approve match: transaction has no proposed match and no entity was specified.")
            new_status = BankTransactionMatchStatus.MATCHED
            notes = f"Match approved by {decided_by} at {now_iso}. " + (decision_request.notes or "")
            conf = 1.0
        elif decision == "reject":
            new_status = BankTransactionMatchStatus.UNMATCHED
            notes = f"Proposed match rejected by {decided_by} at {now_iso}. Reason: {decision_request.notes or 'No reason provided'}"
            matched_type = None
            matched_id = None
            conf = 0.0
        elif decision == "correct":
            if not decision_request.matched_entity_type or not decision_request.matched_entity_id:
                raise ValueError("Correction requires 'matched_entity_type' and 'matched_entity_id'")
            matched_type = decision_request.matched_entity_type
            matched_id = decision_request.matched_entity_id

            # Ensure this entity is not already matched to another transaction
            existing_active = self.repo.list_bank_transactions(property_id=property_id, match_status=BankTransactionMatchStatus.MATCHED)
            conflicting = [t for t in existing_active if t.matched_entity_id == matched_id and t.transaction_id != transaction_id]
            if conflicting:
                raise ValueError(f"Entity '{matched_id}' is already reconciled to bank transaction '{conflicting[0].transaction_id}'.")

            new_status = BankTransactionMatchStatus.MATCHED
            notes = f"Manually matched to {decision_request.matched_entity_type}:{decision_request.matched_entity_id} by {decided_by} at {now_iso}. Notes: {decision_request.notes or ''}"
            conf = 1.0
        else:
            raise ValueError(f"Invalid decision '{decision}'. Expected: 'approve', 'reject', or 'correct'.")

        updated = self.repo.update_bank_transaction_match(
            property_id=property_id,
            transaction_id=transaction_id,
            match_status=new_status,
            matched_entity_type=matched_type,
            matched_entity_id=matched_id,
            match_confidence=conf,
            match_notes=notes,
            reconciled_by=decided_by,
        )
        return updated
