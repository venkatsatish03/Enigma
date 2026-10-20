
"""Orchestrate ENIGMA's financial operations analysis pipeline.

This module coordinates analysis only. It does not execute payments,
change accounting records, or approve proposed actions.
"""

from __future__ import annotations

import hashlib

from schemas.financial_operations import (
    FinancialFinding,
    FinancialOperationsResult,
    FinancialRecord,
    ReconciliationMatch,
)
from tools.balance_assessor import assess_balances
from tools.duplicate_detector import detect_duplicates
from tools.discrepancy_detector import detect_discrepancies
from tools.financial_record_adapter import adapt_document_agent_output
from tools.transaction_matcher import reconcile_records
from schemas.payment_allocation import PaymentAllocationInstruction
from tools.payment_allocator import allocate_payments

def _stable_operation_id(records: list[FinancialRecord]) -> str:
    """Create a deterministic operation ID from the input record identities."""
    record_keys = sorted(
        f"{record.record_id}|{record.document_id}"
        for record in records
    )
    digest = hashlib.sha256(
        "\n".join(record_keys).encode("utf-8")
    ).hexdigest()[:20]
    return f"financial_ops_{digest}"


def _deduplicate_findings(
    findings: list[FinancialFinding],
) -> list[FinancialFinding]:
    """Deduplicate findings by ID and keep the output ordering stable."""
    unique: dict[str, FinancialFinding] = {}

    for finding in findings:
        unique.setdefault(finding.finding_id, finding)

    return sorted(
        unique.values(),
        key=lambda item: (item.finding_type.value, item.finding_id),
    )


def _deduplicate_matches(
    matches: list[ReconciliationMatch],
) -> list[ReconciliationMatch]:
    unique: dict[str, ReconciliationMatch] = {}

    for match in matches:
        unique.setdefault(match.match_id, match)

    return sorted(unique.values(), key=lambda item: item.match_id)


def run_financial_operations_on_records(
    records: list[FinancialRecord],
    allocation_instructions: list[PaymentAllocationInstruction] | None = None,
) -> FinancialOperationsResult:
    """Run reconciliation, duplicate detection, discrepancy detection,
    and balance assessment on normalized financial records.
    """
    if not isinstance(records, list):
        raise TypeError("records must be a list of FinancialRecord objects")

    if any(not isinstance(record, FinancialRecord) for record in records):
        raise TypeError("Every item in records must be a FinancialRecord")

    # Each module is analysis-only and does not mutate the input records.
    matches = _deduplicate_matches(reconcile_records(records))
    duplicate_findings = detect_duplicates(records)
    discrepancy_findings = detect_discrepancies(records, matches)

    allocation_result = allocate_payments(
        records,
        matches=matches,
        instructions=allocation_instructions,
    )

    balance_assessments = assess_balances(
        records,
        matches,
        allocations=allocation_result.allocations,
    )

    findings = _deduplicate_findings(
        [*duplicate_findings, *discrepancy_findings]
    )

    return FinancialOperationsResult(
        operation_id=_stable_operation_id(records),
        status="completed",
        records=records,
        matches=matches,
        findings=findings,
        balance_assessments=balance_assessments,
        proposed_actions=[],
        payment_allocations=allocation_result.allocations,
        unallocated_payment_amounts=allocation_result.unallocated_amounts,
        unresolved_allocation_issues=allocation_result.unresolved_issues,
    )

def run_financial_operations(
    document_agent_output: object,
    allocation_instructions: list[PaymentAllocationInstruction] | None = None,
) -> FinancialOperationsResult:
    records = adapt_document_agent_output(document_agent_output)
    return run_financial_operations_on_records(
        records,
        allocation_instructions=allocation_instructions,
    )