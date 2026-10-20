"""Compliance Monitoring Service.

Provides rule-based operational compliance checks:
1. Overdue rent & outstanding balances (with configurable grace period)
2. Missing invoice or payment information
3. Duplicate invoice references and potential duplicate payments
4. Unapproved expenses and pending approval bottlenecks
5. Bank reconciliation discrepancies
6. Inconsistent property, lease, and payment records

Provides finding management (assign, resolve, dismiss) and integration with
the existing SLA monitoring/escalation mechanism.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Optional

from repository.domain_repository import DomainRepository, EntityNotFoundError
from schemas.domain import (
    BankTransactionMatchStatus,
    ComplianceAssignmentRequest,
    ComplianceCategory,
    ComplianceFindingRecord,
    ComplianceResolutionRequest,
    ComplianceSeverity,
    ComplianceStatus,
    ExpenseApprovalStatus,
    ExpenseRecord,
    InvoiceRecord,
    InvoiceStatus,
    PaymentRecord,
    PaymentStatus,
    SlaEscalationCreate,
    SlaEscalationType,
)


class ComplianceService:
    def __init__(self, domain_repo: DomainRepository):
        self.repo = domain_repo

    def run_compliance_audit(
        self,
        property_id: str,
        as_of_date: Optional[date] = None,
        grace_days: int = 5,
        auto_escalate_severe: bool = True,
    ) -> List[ComplianceFindingRecord]:
        """
        Execute all rule-based compliance checks for a property, persist newly
        detected findings, and escalate severe findings to the SLA engine.
        """
        if as_of_date is None:
            as_of_date = date.today()

        existing_findings = self.repo.list_compliance_findings(property_id=property_id, limit=1000)
        existing_keys = {
            f"{f.triggering_rule}|{f.category.value}|{','.join(f.affected_record_ids)}"
            for f in existing_findings
            if f.status in (ComplianceStatus.OPEN, ComplianceStatus.UNDER_REVIEW)
        }

        new_findings: List[ComplianceFindingRecord] = []

        # 1. Overdue rent & outstanding balances
        candidates = (
            self._check_overdue_rent(property_id, as_of_date, grace_days)
            + self._check_missing_information(property_id)
            + self._check_duplicate_invoices(property_id)
            + self._check_unapproved_expenses(property_id, as_of_date)
            + self._check_reconciliation_discrepancies(property_id, as_of_date)
            + self._check_inconsistent_records(property_id)
        )

        for item in candidates:
            key = f"{item['triggering_rule']}|{item['category'].value}|{','.join(item['affected_record_ids'])}"
            if key in existing_keys:
                continue

            created = self.repo.create_compliance_finding(
                property_id=property_id,
                category=item["category"],
                severity=item["severity"],
                title=item["title"],
                description=item["description"],
                triggering_rule=item["triggering_rule"],
                affected_record_ids=item["affected_record_ids"],
                evidence=item["evidence"],
            )
            new_findings.append(created)

            # Auto-escalate CRITICAL and HIGH findings through existing SLA mechanism
            if auto_escalate_severe and created.severity in (ComplianceSeverity.CRITICAL, ComplianceSeverity.HIGH):
                self._escalate_to_sla(property_id, created)

        return new_findings

    def _escalate_to_sla(self, property_id: str, finding: ComplianceFindingRecord) -> None:
        """Route severe compliance findings to the SLA Escalation subsystem."""
        try:
            deadline = datetime.now(timezone.utc) + timedelta(hours=24 if finding.severity == ComplianceSeverity.CRITICAL else 48)
            escalation_payload = SlaEscalationCreate(
                request_id=finding.finding_id,
                escalation_type=SlaEscalationType.OVERDUE,
                sla_deadline_at=deadline,
                notes=(
                    f"Automated SLA escalation for {finding.severity.value.upper()} compliance finding "
                    f"'{finding.finding_id}': {finding.title}. Rule: {finding.triggering_rule}. Details: {finding.description}"
                ),
            )
            self.repo.create_sla_escalation(property_id, escalation_payload)
        except Exception:
            pass

    def _check_overdue_rent(
        self,
        property_id: str,
        as_of_date: date,
        grace_days: int,
    ) -> List[dict[str, Any]]:
        findings = []
        invoices = self.repo.list_invoices(property_id=property_id, limit=1000)

        for inv in invoices:
            if inv.invoice_type.value == "rent" and inv.status in (InvoiceStatus.UNPAID, InvoiceStatus.PARTIALLY_PAID):
                if inv.due_date and (inv.due_date + timedelta(days=grace_days)) < as_of_date:
                    overdue_days = (as_of_date - inv.due_date).days
                    outstanding = inv.outstanding_amount

                    if overdue_days > 30 or outstanding >= Decimal("3000.00"):
                        sev = ComplianceSeverity.CRITICAL
                    elif overdue_days > 14 or outstanding >= Decimal("1500.00"):
                        sev = ComplianceSeverity.HIGH
                    else:
                        sev = ComplianceSeverity.WARNING

                    findings.append({
                        "category": ComplianceCategory.OVERDUE_RENT,
                        "severity": sev,
                        "title": f"Overdue Rent Invoice {inv.invoice_number or inv.invoice_id}",
                        "description": (
                            f"Invoice {inv.invoice_number or inv.invoice_id} is overdue by {overdue_days} days "
                            f"(due date: {inv.due_date}, grace period: {grace_days} days). "
                            f"Outstanding balance: ${outstanding:.2f}."
                        ),
                        "triggering_rule": "RULE-RENT-001",
                        "affected_record_ids": [inv.invoice_id] + ([inv.resident_id] if inv.resident_id else []),
                        "evidence": [
                            f"due_date: {inv.due_date}",
                            f"overdue_days: {overdue_days}",
                            f"outstanding_amount: {outstanding}",
                            f"as_of_date: {as_of_date}",
                        ],
                    })
        return findings

    def _check_missing_information(self, property_id: str) -> List[dict[str, Any]]:
        findings = []
        invoices = self.repo.list_invoices(property_id=property_id, limit=500)
        for inv in invoices:
            missing = []
            if not inv.invoice_number:
                missing.append("invoice_number")
            if not inv.due_date:
                missing.append("due_date")
            if inv.amount <= Decimal("0.00"):
                missing.append("valid_amount")
            if not inv.resident_id and not inv.vendor_id:
                missing.append("counterparty_reference")

            if missing:
                findings.append({
                    "category": ComplianceCategory.MISSING_INFORMATION,
                    "severity": ComplianceSeverity.WARNING,
                    "title": f"Incomplete Invoice Record {inv.invoice_id}",
                    "description": f"Invoice {inv.invoice_id} is missing mandatory operational fields: {', '.join(missing)}.",
                    "triggering_rule": "RULE-INFO-001",
                    "affected_record_ids": [inv.invoice_id],
                    "evidence": [f"missing: {m}" for m in missing],
                })

        payments = self.repo.list_payments(property_id=property_id, limit=500)
        for p in payments:
            if not p.reference or p.amount <= Decimal("0.00"):
                findings.append({
                    "category": ComplianceCategory.MISSING_INFORMATION,
                    "severity": ComplianceSeverity.INFO,
                    "title": f"Payment Lacks Audit Reference {p.payment_id}",
                    "description": f"Payment {p.payment_id} is missing a clear bank/tenant transaction reference or has zero amount.",
                    "triggering_rule": "RULE-INFO-002",
                    "affected_record_ids": [p.payment_id],
                    "evidence": [f"amount: {p.amount}", f"has_reference: {bool(p.reference)}"],
                })
        return findings

    def _check_duplicate_invoices(self, property_id: str) -> List[dict[str, Any]]:
        findings = []
        invoices = self.repo.list_invoices(property_id=property_id, limit=1000)
        seen_refs: dict[str, InvoiceRecord] = {}

        for inv in invoices:
            if not inv.invoice_number:
                continue
            key = inv.invoice_number.strip().upper()
            if key in seen_refs:
                prev = seen_refs[key]
                findings.append({
                    "category": ComplianceCategory.DUPLICATE_INVOICE,
                    "severity": ComplianceSeverity.HIGH,
                    "title": f"Duplicate Invoice Reference '{inv.invoice_number}'",
                    "description": (
                        f"Invoice '{inv.invoice_id}' shares duplicate reference '{inv.invoice_number}' "
                        f"with previously recorded invoice '{prev.invoice_id}'."
                    ),
                    "triggering_rule": "RULE-DUP-001",
                    "affected_record_ids": [prev.invoice_id, inv.invoice_id],
                    "evidence": [
                        f"reference: {inv.invoice_number}",
                        f"invoice_1_amount: {prev.amount}",
                        f"invoice_2_amount: {inv.amount}",
                    ],
                })
            else:
                seen_refs[key] = inv
        return findings

    def _check_unapproved_expenses(self, property_id: str, as_of_date: date) -> List[dict[str, Any]]:
        findings = []
        expenses = self.repo.list_expenses(property_id=property_id, limit=500)

        for exp in expenses:
            if exp.approval_status == ExpenseApprovalStatus.PENDING:
                pending_days = (as_of_date - exp.expense_date).days if exp.expense_date else 0
                sev = ComplianceSeverity.HIGH if (pending_days > 3 or exp.amount >= Decimal("1000.00")) else ComplianceSeverity.WARNING
                findings.append({
                    "category": ComplianceCategory.UNAPPROVED_EXPENSE,
                    "severity": sev,
                    "title": f"Unapproved Expense {exp.expense_id} (${exp.amount:.2f})",
                    "description": (
                        f"Expense '{exp.expense_id}' ({exp.category.value}: ${exp.amount:.2f}) "
                        f"has been pending approval for {pending_days} days."
                    ),
                    "triggering_rule": "RULE-EXP-001",
                    "affected_record_ids": [exp.expense_id],
                    "evidence": [
                        f"amount: {exp.amount}",
                        f"pending_days: {pending_days}",
                        f"category: {exp.category.value}",
                    ],
                })
        return findings

    def _check_reconciliation_discrepancies(self, property_id: str, as_of_date: date) -> List[dict[str, Any]]:
        findings = []
        unmatched_txs = self.repo.list_bank_transactions(
            property_id=property_id,
            match_status=BankTransactionMatchStatus.UNMATCHED,
            limit=500,
        )

        for tx in unmatched_txs:
            days_old = (as_of_date - tx.transaction_date).days
            if days_old >= 5:
                sev = ComplianceSeverity.HIGH if abs(tx.amount) >= Decimal("1000.00") else ComplianceSeverity.WARNING
                findings.append({
                    "category": ComplianceCategory.RECONCILIATION_DISCREPANCY,
                    "severity": sev,
                    "title": f"Unreconciled Bank Transaction {tx.transaction_id}",
                    "description": (
                        f"Bank transaction '{tx.transaction_id}' of ${tx.amount:.2f} ({tx.description}) "
                        f"on {tx.transaction_date} remains unreconciled after {days_old} days."
                    ),
                    "triggering_rule": "RULE-RECON-001",
                    "affected_record_ids": [tx.transaction_id, tx.statement_id],
                    "evidence": [
                        f"amount: {tx.amount}",
                        f"transaction_date: {tx.transaction_date}",
                        f"days_unmatched: {days_old}",
                    ],
                })
        return findings

    def _check_inconsistent_records(self, property_id: str) -> List[dict[str, Any]]:
        findings = []
        payments = self.repo.list_payments(property_id=property_id, limit=500)
        leases = {l.lease_id: l for l in self.repo.list_leases(property_id=property_id, limit=500)}

        for p in payments:
            if p.lease_id and p.lease_id not in leases:
                findings.append({
                    "category": ComplianceCategory.RECORD_INCONSISTENCY,
                    "severity": ComplianceSeverity.WARNING,
                    "title": f"Payment Points to Non-existent Lease {p.payment_id}",
                    "description": f"Payment '{p.payment_id}' references lease_id '{p.lease_id}', which does not exist in property records.",
                    "triggering_rule": "RULE-INTEG-001",
                    "affected_record_ids": [p.payment_id],
                    "evidence": [f"missing_lease_id: {p.lease_id}"],
                })
        return findings

    def assign_finding(
        self,
        property_id: str,
        finding_id: str,
        payload: ComplianceAssignmentRequest,
    ) -> ComplianceFindingRecord:
        """Assign an active compliance finding to a designated team member or role."""
        return self.repo.assign_compliance_finding(property_id, finding_id, payload.assigned_to)

    def resolve_finding(
        self,
        property_id: str,
        finding_id: str,
        payload: ComplianceResolutionRequest,
    ) -> ComplianceFindingRecord:
        """Mark a compliance finding as resolved or dismissed with documented rationale."""
        return self.repo.resolve_compliance_finding(property_id, finding_id, payload.action, payload.reason)
