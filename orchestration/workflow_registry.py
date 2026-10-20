"""Workflow definition registry and step graph routing."""

from __future__ import annotations

from orchestration.schemas import StepName, WorkflowType

WORKFLOW_STEPS: dict[WorkflowType, list[StepName]] = {
    WorkflowType.INVOICE_PROCESSING: [
        StepName.DOCUMENT_ANALYSIS,
        StepName.FINANCIAL_OPERATIONS,
        StepName.POLICY_VALIDATION,
        StepName.APPROVAL_GATE,
        StepName.EXECUTE_ACTION,
        StepName.VERIFY_OUTCOME,
    ],
    WorkflowType.RENT_RECONCILIATION: [
        StepName.DOCUMENT_ANALYSIS,
        StepName.FINANCIAL_OPERATIONS,
        StepName.POLICY_VALIDATION,
        StepName.APPROVAL_GATE,
        StepName.EXECUTE_ACTION,
        StepName.VERIFY_OUTCOME,
    ],
    WorkflowType.FINANCIAL_AUDIT: [
        StepName.DOCUMENT_ANALYSIS,
        StepName.FINANCIAL_OPERATIONS,
        StepName.POLICY_VALIDATION,
        StepName.VERIFY_OUTCOME,
    ],
    WorkflowType.MAINTENANCE_LIFECYCLE: [
        StepName.MAINTENANCE_TRIAGE,
        StepName.MAINTENANCE_DISPATCH,
        StepName.POLICY_VALIDATION,
        StepName.APPROVAL_GATE,
        StepName.EXECUTE_ACTION,
        StepName.VERIFY_OUTCOME,
    ],
}


def get_workflow_step_sequence(workflow_type: WorkflowType) -> list[StepName]:
    """Retrieve ordered sequence of steps for a given workflow type."""
    steps = WORKFLOW_STEPS.get(workflow_type)
    if not steps:
        raise ValueError(f"Unregistered workflow type '{workflow_type}'.")
    return list(steps)


def get_step_index(workflow_type: WorkflowType, step_name: StepName) -> int:
    """Retrieve 0-based sequential index for a step within a workflow."""
    seq = get_workflow_step_sequence(workflow_type)
    try:
        return seq.index(step_name)
    except ValueError:
        return -1


def get_next_step(workflow_type: WorkflowType, current_step: StepName | None) -> StepName | None:
    """Determine next sequential step or return None if workflow is at completion."""
    seq = get_workflow_step_sequence(workflow_type)
    if current_step is None:
        return seq[0] if seq else None
    try:
        idx = seq.index(current_step)
        if idx + 1 < len(seq):
            return seq[idx + 1]
        return None
    except ValueError:
        return None
