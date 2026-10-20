
"""Deterministic policy checks for proposed financial actions.

This module validates whether an action may enter the approval workflow.
It does not approve, reject, or execute financial transactions.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable


class PolicyStatus(str, Enum):
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class ActionPolicyResult:
    status: PolicyStatus
    reasons: list[str] = field(default_factory=list)

    @property
    def eligible_for_approval(self) -> bool:
        return self.status == PolicyStatus.ELIGIBLE


def _get(value: Any, key: str, default: Any = None) -> Any:
    """Read a field from a Pydantic model, dataclass, or dictionary."""
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _enum_value(value: Any) -> str:
    """Normalize enum values without depending on a specific enum class."""
    return str(getattr(value, "value", value)).strip().lower()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    if not result.is_finite():
        return None
    return result


def validate_proposed_action(
    action: Any,
    *,
    findings: Iterable[Any] = (),
    records: Iterable[Any] = (),
    matches: Iterable[Any] = (),
    allocations: Iterable[Any] = (),
) -> ActionPolicyResult:
    """Evaluate a proposed action before it enters the approval workflow.

    Conservative policy:
    - Reject malformed or non-positive payment amounts.
    - Reject actions with an explicitly unsafe status.
    - Require evidence for payment actions.
    - Block payment actions when a relevant duplicate-payment or amount-conflict
      finding exists.
    - Require manual review for unresolved issues or missing verification.
    - Never treat an approval request as permission to execute a payment.
    """
    reasons: list[str] = []

    action_type = _enum_value(_get(action, "action_type", _get(action, "action_type", "")))
    status = _enum_value(_get(action, "status", ""))
    amount = _decimal(_get(action, "amount"))
    currency = _get(action, "currency")
    evidence = _get(action, "evidence", _get(action, "evidence_references", []))
    unresolved = _get(action, "unresolved_issues", [])
    action_id = _get(action, "action_id", _get(action, "id"))

    is_payment = "payment" in action_type or "pay" in action_type

    if not action_id:
        reasons.append("Missing action identifier")

    if status in {"rejected", "cancelled", "canceled", "blocked", "failed", "executed"}:
        reasons.append(f"Action status is not eligible for approval: {status}")

    if is_payment:
        if amount is None or amount <= 0:
            reasons.append("Payment amount must be a finite positive number")
        if not isinstance(currency, str) or not currency.strip():
            reasons.append("Payment currency is missing")
        if not evidence:
            reasons.append("Payment action has no supporting evidence")

    if unresolved:
        reasons.append("Action contains unresolved issues")

    finding_values = list(findings)
    for finding in finding_values:
        finding_type = _enum_value(_get(finding, "finding_type", _get(finding, "type", "")))
        severity = _enum_value(_get(finding, "severity", ""))
        finding_status = _enum_value(_get(finding, "status", ""))
        if finding_status in {"resolved", "dismissed", "false_positive"}:
            continue

        if any(token in finding_type for token in ("duplicate", "double_payment")):
            reasons.append("An unresolved duplicate-payment risk exists")
        elif any(token in finding_type for token in ("amount_conflict", "amount_mismatch")):
            reasons.append("An unresolved amount conflict exists")
        elif severity in {"critical", "high"}:
            reasons.append("A high-severity unresolved finding requires manual review")

    # A payment should not be approved if its referenced invoice/payment records
    # cannot be independently located in the supplied records.
    record_values = list(records)
    if is_payment and record_values:
        references = {
            str(_get(record, "record_id", _get(record, "id", "")))
            for record in record_values
        }
        referenced_ids = _get(action, "record_ids", [])
        missing = [str(item) for item in referenced_ids if str(item) not in references]
        if missing:
            reasons.append("One or more referenced financial records are missing")

    # An explicitly failed or conflicting reconciliation blocks payment approval.
    for match in matches:
        match_status = _enum_value(_get(match, "status", ""))
        if match_status in {"conflict", "amount_conflict", "rejected"}:
            reasons.append("A related reconciliation match is conflicting")

    for allocation in allocations:
        allocation_status = _enum_value(_get(allocation, "status", ""))
        if allocation_status in {"conflict", "rejected", "invalid"}:
            reasons.append("A related payment allocation is invalid or conflicting")

    # De-duplicate reasons while preserving their order.
    reasons = list(dict.fromkeys(reasons))

    blocked_reason_markers = (
    "missing action identifier",
    "not eligible for approval",
    "positive number",
    "currency is missing",
    "duplicate-payment risk",
    "amount conflict",
    "conflicting",
    "referenced financial records are missing",
    "allocation is invalid",
)

    if any(
        marker in reason.lower()
        for reason in reasons
        for marker in blocked_reason_markers
    ):
        return ActionPolicyResult(PolicyStatus.BLOCKED, reasons)

    if reasons:
        return ActionPolicyResult(PolicyStatus.MANUAL_REVIEW, reasons)

    return ActionPolicyResult(PolicyStatus.ELIGIBLE, [])
