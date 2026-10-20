
"""Connect deterministic action policy checks to the approval workflow.

This service creates approval requests only. It never executes payments.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from schemas.approvals import ApprovalRequest, ApprovalRequestCreate
from tools.action_policy_validator import (
    ActionPolicyResult,
    PolicyStatus,
    validate_proposed_action,
)


@dataclass(frozen=True)
class ActionApprovalSubmission:
    policy_result: ActionPolicyResult
    approval_request: ApprovalRequest | None

    @property
    def submitted(self) -> bool:
        return self.approval_request is not None


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().lower()


def submit_action_for_approval(
    action: Any,
    workflow: Any,
    *,
    requested_by: str,
    approver_ids: list[str],
    expires_at: datetime,
    findings: Iterable[Any] = (),
    records: Iterable[Any] = (),
    matches: Iterable[Any] = (),
    allocations: Iterable[Any] = (),
    correlation_id: str | None = None,
    property_id: str | None = None,
) -> ActionApprovalSubmission:
    """Validate an action and submit it for approval if eligible.

    BLOCKED and MANUAL_REVIEW actions do not create approval requests.
    The caller must separately handle manual-review cases.

    Approval does not authorize or execute a financial transaction.
    """
    policy_result = validate_proposed_action(
        action,
        findings=findings,
        records=records,
        matches=matches,
        allocations=allocations,
    )

    if policy_result.status != PolicyStatus.ELIGIBLE:
        return ActionApprovalSubmission(
            policy_result=policy_result,
            approval_request=None,
        )

    action_id = _get(action, "action_id", _get(action, "id"))
    action_type = _get(action, "action_type")

    if hasattr(action_type, "value"):
        action_type = action_type.value

    
    rationale = _get(action, "rationale")

    if not isinstance(rationale, str) or not rationale.strip():
        rationale = _get(action, "description")

    if not isinstance(rationale, str) or not rationale.strip():
        rationale = (
            f"Approval requested for proposed action "
            f"{action_id} ({action_type})."
        )

    request = ApprovalRequestCreate(
        action_id=str(action_id),
        action_type=str(action_type),
        requested_by=requested_by,
        approver_ids=approver_ids,
        expires_at=expires_at,
        rationale=rationale.strip(),
        correlation_id=correlation_id,
        property_id=property_id,
    )


    approval_request = workflow.create_request(request)

    return ActionApprovalSubmission(
        policy_result=policy_result,
        approval_request=approval_request,
    )
