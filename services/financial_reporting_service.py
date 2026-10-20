"""Financial Reporting Service.

Calculates property-level metrics:
- Rent billed, collected, outstanding, and collection rate
- Operating expenses grouped by category
- Pending approvals & unpaid invoice pipelines
- Reconciliation discrepancies
- Net Operating Income (NOI) and cash flows
- Owner statement generation with configurable management fees and double-deduction prevention
- Multi-currency isolation
- CSV exports for spreadsheets and external accounting tools
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, List, Optional

from repository.domain_repository import DomainRepository, EntityNotFoundError
from schemas.domain import (
    BankTransactionMatchStatus,
    ExpenseApprovalStatus,
    ExpenseCategory,
    ExpenseCategorySummary,
    ExpenseRecord,
    ExpenseStatus,
    FinancialSummaryReport,
    InvoiceRecord,
    InvoiceStatus,
    InvoiceType,
    OwnerStatementReport,
    PaymentRecord,
    PaymentStatus,
)


def _round(val: Decimal) -> Decimal:
    """Format decimal to exactly 2 places with standard rounding."""
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class FinancialReportingService:
    def __init__(self, domain_repo: DomainRepository):
        self.repo = domain_repo

    def generate_financial_summary(
        self,
        property_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        currency: str = "USD",
    ) -> FinancialSummaryReport:
        """
        Aggregate financial performance for a property within an optional date range and currency.
        """
        reporting_currency = (currency or "USD").strip().upper()
        invoices = self.repo.list_invoices(property_id=property_id, limit=2000)
        payments = self.repo.list_payments(property_id=property_id, limit=2000)
        expenses = self.repo.list_expenses(property_id=property_id, limit=2000)
        bank_txs = self.repo.list_bank_transactions(property_id=property_id, limit=2000)

        def in_range(d: Optional[date]) -> bool:
            if d is None:
                return True
            if start_date and d < start_date:
                return False
            if end_date and d > end_date:
                return False
            return True

        # Invoices / Rent Billed & Outstanding Rent Obligations
        rent_billed = Decimal("0.00")
        rent_outstanding = Decimal("0.00")
        unpaid_invoices_count = 0
        unpaid_invoices_amount = Decimal("0.00")

        for inv in invoices:
            if getattr(inv, "currency", "USD").strip().upper() != reporting_currency:
                continue
            inv_date = getattr(inv, "issue_date", None) or getattr(inv, "invoice_date", None)
            if in_range(inv_date):
                if inv.invoice_type == InvoiceType.RENT:
                    rent_billed += inv.amount
                    # Outstanding rent calculated directly from invoice obligations and allocations
                    rent_outstanding += inv.outstanding_amount
                if inv.status in (InvoiceStatus.UNPAID, InvoiceStatus.PARTIALLY_PAID):
                    unpaid_invoices_count += 1
                    unpaid_invoices_amount += inv.outstanding_amount

        # Realized Payments / Rent Collected (settled/reconciled only) vs Pending Pipeline
        rent_collected = Decimal("0.00")
        cash_inflows = Decimal("0.00")
        pending_rent_amount = Decimal("0.00")
        pending_payments_count = 0

        for p in payments:
            if getattr(p, "currency", "USD").strip().upper() != reporting_currency:
                continue
            if in_range(p.payment_date):
                # Exclude unsettled payments (PENDING, FAILED, REFUNDED) from realized cash
                if p.status in (PaymentStatus.SETTLED, PaymentStatus.RECONCILED):
                    rent_collected += p.amount
                    cash_inflows += p.amount
                elif p.status == PaymentStatus.PENDING:
                    pending_rent_amount += p.amount
                    pending_payments_count += 1
                # Note: FAILED and REFUNDED payments are non-realized / reversed, excluded from cash inflows

        # Collection rate: If rent_billed > 0, compute percentage; otherwise N/A (None)
        collection_rate = (
            _round((rent_collected / rent_billed) * Decimal("100.00"))
            if rent_billed > Decimal("0.00")
            else None
        )

        # Expenses
        total_expenses = Decimal("0.00")
        cash_outflows = Decimal("0.00")
        pending_approvals_count = 0
        pending_approvals_amount = Decimal("0.00")
        exp_by_cat: dict[ExpenseCategory, dict] = {}

        for exp in expenses:
            if (getattr(exp, "currency", "USD") or "USD").strip().upper() != reporting_currency:
                continue
            if in_range(exp.expense_date):
                total_expenses += exp.amount
                if exp.status in (ExpenseStatus.PAID, ExpenseStatus.APPROVED):
                    cash_outflows += exp.amount

                if exp.approval_status == ExpenseApprovalStatus.PENDING:
                    pending_approvals_count += 1
                    pending_approvals_amount += exp.amount

                if exp.category not in exp_by_cat:
                    exp_by_cat[exp.category] = {"amount": Decimal("0.00"), "count": 0}
                exp_by_cat[exp.category]["amount"] += exp.amount
                exp_by_cat[exp.category]["count"] += 1

        cat_summaries = [
            ExpenseCategorySummary(
                category=cat,
                total_amount=_round(vals["amount"]),
                count=vals["count"],
            )
            for cat, vals in sorted(exp_by_cat.items(), key=lambda x: x[0].value)
        ]

        # Reconciliation discrepancies
        unmatched_txs = [
            tx for tx in bank_txs
            if (getattr(tx, "currency", "USD") or "USD").strip().upper() == reporting_currency
            and in_range(tx.transaction_date)
            and tx.match_status in (BankTransactionMatchStatus.UNMATCHED, BankTransactionMatchStatus.DISCREPANCY)
        ]
        reconciliation_discrepancies_count = len(unmatched_txs)
        reconciliation_discrepancy_amount = sum((abs(tx.amount) for tx in unmatched_txs), Decimal("0.00"))

        net_operating_income = rent_collected - total_expenses
        net_cash_flow = cash_inflows - cash_outflows

        return FinancialSummaryReport(
            property_id=property_id,
            period_start=start_date,
            period_end=end_date,
            currency=reporting_currency,
            total_rent_billed=_round(rent_billed),
            total_rent_collected=_round(rent_collected),
            total_rent_outstanding=_round(rent_outstanding),
            collection_rate_percentage=collection_rate,
            pending_rent_amount=_round(pending_rent_amount),
            pending_payments_count=pending_payments_count,
            total_expenses=_round(total_expenses),
            net_operating_income=_round(net_operating_income),
            pending_expense_approvals_count=pending_approvals_count,
            pending_expense_approvals_amount=_round(pending_approvals_amount),
            unpaid_invoices_count=unpaid_invoices_count,
            unpaid_invoices_amount=_round(unpaid_invoices_amount),
            reconciliation_discrepancies_count=reconciliation_discrepancies_count,
            reconciliation_discrepancy_amount=_round(reconciliation_discrepancy_amount),
            cash_inflows=_round(cash_inflows),
            cash_outflows=_round(cash_outflows),
            net_cash_flow=_round(net_cash_flow),
            expenses_by_category=cat_summaries,
            generated_at=datetime.now(timezone.utc),
        )

    def generate_owner_statement(
        self,
        property_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        management_fee_rate: Optional[Decimal] = None,
        currency: str = "USD",
    ) -> OwnerStatementReport:
        """
        Generate a monthly or period owner statement showing rental revenue,
        itemized operating expenses, management fees, and net distribution.
        Prevents double-deduction if management fees are already present in operating expenses.
        """
        reporting_currency = (currency or "USD").strip().upper()
        prop = self.repo.get_property(property_id)
        prop_name = prop.name if prop else f"Property {property_id}"

        summary = self.generate_financial_summary(
            property_id,
            start_date=start_date,
            end_date=end_date,
            currency=reporting_currency,
        )
        expenses = self.repo.list_expenses(property_id=property_id, limit=2000)
        payments = self.repo.list_payments(property_id=property_id, limit=2000)

        effective_start = start_date or date(date.today().year, date.today().month, 1)
        effective_end = end_date or date.today()

        def in_range(d: Optional[date]) -> bool:
            if d is None:
                return True
            if start_date and d < start_date:
                return False
            if end_date and d > end_date:
                return False
            return True

        gross_rental = summary.total_rent_collected
        other_income = Decimal("0.00")
        total_income = gross_rental + other_income

        # Filter expenses in period and matching currency
        period_expenses = [
            exp for exp in expenses
            if in_range(exp.expense_date)
            and (getattr(exp, "currency", "USD") or "USD").strip().upper() == reporting_currency
        ]
        total_operating = summary.total_expenses

        # Check if management fees are already recorded in operating expenses
        mgmt_expenses = [
            exp for exp in period_expenses
            if exp.category == ExpenseCategory.MANAGEMENT
        ]
        mgmt_already_in_expenses = sum((exp.amount for exp in mgmt_expenses), Decimal("0.00"))

        # Determine effective management fee rate:
        # 1. Parameter passed in (per-call / per-agreement override)
        # 2. Configured on property record
        # 3. Environment variable ENIGMA_MANAGEMENT_FEE_RATE
        # 4. Default documented rate: 0.08 (8%)
        if management_fee_rate is not None:
            effective_rate = Decimal(str(management_fee_rate))
        elif prop and getattr(prop, "management_fee_rate", None) is not None:
            effective_rate = Decimal(str(prop.management_fee_rate))
        else:
            env_rate = os.getenv("ENIGMA_MANAGEMENT_FEE_RATE", "0.08")
            try:
                effective_rate = Decimal(env_rate)
            except Exception:
                effective_rate = Decimal("0.08")

        if mgmt_already_in_expenses > Decimal("0.00"):
            # Management fee is ALREADY part of operating expenses; do NOT double-deduct from distribution
            mgmt_fee = _round(mgmt_already_in_expenses)
            net_operating = total_income - total_operating
            distribution = max(Decimal("0.00"), net_operating)
        else:
            # Management fee is calculated on collected gross rental and deducted from distribution
            mgmt_fee = _round(gross_rental * effective_rate)
            net_operating = total_income - total_operating
            distribution = max(Decimal("0.00"), net_operating - mgmt_fee)

        itemized_income = [
            {
                "payment_id": p.payment_id,
                "date": str(p.payment_date),
                "resident_id": p.resident_id,
                "amount": f"{p.amount:.2f}",
                "status": p.status.value,
                "reference": getattr(p, "reference_number", None) or getattr(p, "reference", "") or "",
            }
            for p in payments
            if in_range(p.payment_date)
            and getattr(p, "currency", "USD").strip().upper() == reporting_currency
            and p.status in (PaymentStatus.SETTLED, PaymentStatus.RECONCILED)
        ]

        itemized_exp = [
            {
                "expense_id": exp.expense_id,
                "date": str(exp.expense_date),
                "category": exp.category.value,
                "vendor_id": exp.vendor_id,
                "description": exp.description,
                "amount": f"{exp.amount:.2f}",
                "approval_status": exp.approval_status if isinstance(exp.approval_status, str) else getattr(exp.approval_status, "value", "approved"),
            }
            for exp in period_expenses
        ]

        return OwnerStatementReport(
            property_id=property_id,
            property_name=prop_name,
            statement_date=date.today(),
            period_start=effective_start,
            period_end=effective_end,
            currency=reporting_currency,
            gross_rental_income=_round(gross_rental),
            other_income=_round(other_income),
            total_income=_round(total_income),
            total_operating_expenses=_round(total_operating),
            management_fee_rate=effective_rate,
            management_fee_amount=_round(mgmt_fee),
            net_operating_income=_round(net_operating),
            reserve_withheld=Decimal("0.00"),
            owner_distribution_amount=_round(distribution),
            itemized_income=itemized_income,
            itemized_expenses=itemized_exp,
            generated_at=datetime.now(timezone.utc),
        )

    def export_summary_csv(self, report: FinancialSummaryReport) -> str:
        """Export financial summary metrics to CSV string."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Property Financial Summary", report.property_id])
        writer.writerow(["Period Start", str(report.period_start or "All Time")])
        writer.writerow(["Period End", str(report.period_end or "All Time")])
        writer.writerow(["Currency", report.currency])
        writer.writerow(["Generated At", report.generated_at.isoformat()])
        writer.writerow([])
        writer.writerow(["Metric", "Value"])
        writer.writerow(["Rent Billed", f"${report.total_rent_billed:.2f}"])
        writer.writerow(["Rent Collected", f"${report.total_rent_collected:.2f}"])
        writer.writerow(["Rent Outstanding", f"${report.total_rent_outstanding:.2f}"])
        rate_str = f"{report.collection_rate_percentage:.2f}%" if report.collection_rate_percentage is not None else "N/A"
        writer.writerow(["Collection Rate", rate_str])
        writer.writerow(["Pending Rent Pipeline Amount", f"${report.pending_rent_amount:.2f}"])
        writer.writerow(["Pending Payments Count", report.pending_payments_count])
        writer.writerow(["Operating Expenses", f"${report.total_expenses:.2f}"])
        writer.writerow(["Net Operating Income (NOI)", f"${report.net_operating_income:.2f}"])
        writer.writerow(["Cash Inflows", f"${report.cash_inflows:.2f}"])
        writer.writerow(["Cash Outflows", f"${report.cash_outflows:.2f}"])
        writer.writerow(["Net Cash Flow", f"${report.net_cash_flow:.2f}"])
        writer.writerow(["Pending Expense Approvals Count", report.pending_expense_approvals_count])
        writer.writerow(["Pending Expense Approvals Amount", f"${report.pending_expense_approvals_amount:.2f}"])
        writer.writerow(["Unpaid Invoices Count", report.unpaid_invoices_count])
        writer.writerow(["Unpaid Invoices Amount", f"${report.unpaid_invoices_amount:.2f}"])
        writer.writerow(["Reconciliation Discrepancies Count", report.reconciliation_discrepancies_count])
        writer.writerow(["Reconciliation Discrepancy Amount", f"${report.reconciliation_discrepancy_amount:.2f}"])
        writer.writerow([])
        writer.writerow(["Expense Category Breakdown"])
        writer.writerow(["Category", "Total Amount", "Count"])
        for cat in report.expenses_by_category:
            writer.writerow([cat.category.value, f"${cat.total_amount:.2f}", cat.count])

        return buf.getvalue()

    def export_owner_statement_csv(self, statement: OwnerStatementReport) -> str:
        """Export owner statement to CSV format with itemized entries."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Owner Statement", statement.property_name, f"({statement.property_id})"])
        writer.writerow(["Period", f"{statement.period_start} to {statement.period_end}"])
        writer.writerow(["Currency", statement.currency])
        writer.writerow(["Generated At", statement.generated_at.isoformat()])
        writer.writerow([])
        writer.writerow(["Summary", f"Amount ({statement.currency})"])
        writer.writerow(["Gross Rental Income", f"{statement.gross_rental_income:.2f}"])
        writer.writerow(["Other Income", f"{statement.other_income:.2f}"])
        writer.writerow(["Total Income", f"{statement.total_income:.2f}"])
        writer.writerow(["Operating Expenses", f"{statement.total_operating_expenses:.2f}"])
        fee_pct = statement.management_fee_rate * Decimal("100.00")
        writer.writerow([f"Management Fee ({fee_pct:.1f}%)", f"{statement.management_fee_amount:.2f}"])
        writer.writerow(["Net Operating Income", f"{statement.net_operating_income:.2f}"])
        writer.writerow(["Owner Distribution", f"{statement.owner_distribution_amount:.2f}"])
        writer.writerow([])
        writer.writerow(["Itemized Expenses"])
        writer.writerow(["Expense ID", "Date", "Category", "Vendor ID", "Description", f"Amount ({statement.currency})", "Approval Status"])
        for exp in statement.itemized_expenses:
            writer.writerow([
                exp.get("expense_id"),
                exp.get("date"),
                exp.get("category"),
                exp.get("vendor_id"),
                exp.get("description"),
                exp.get("amount"),
                exp.get("approval_status"),
            ])
        return buf.getvalue()
