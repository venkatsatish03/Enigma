"""ENIGMA Master Orchestrator: durable, stateful workflow coordination engine."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agents.document_agent import DocumentIntelligenceAgent
from agents.maintenance_agent import MaintenanceAgent
from orchestration.checkpoint_store import SqliteCheckpointStore
from orchestration.exceptions import (
    ConcurrentExecutionError,
    PolicyValidationError,
    StepExecutionError,
    TransientStepError,
    WorkflowAuthorizationError,
    WorkflowNotFoundError,
    WorkflowStateError,
)
from orchestration.nodes import (
    ActionExecutionNode,
    ApprovalGateNode,
    DocumentAnalysisNode,
    FinancialOperationsNode,
    MaintenanceDispatchNode,
    MaintenanceTriageNode,
    OutcomeVerificationNode,
    PolicyValidationNode,
)
from orchestration.schemas import (
    StepName,
    StepStatus,
    WorkflowCancelRequest,
    WorkflowCreateRequest,
    WorkflowResumeRequest,
    WorkflowStepRecord,
    WorkflowStatus,
    WorkflowSummary,
    WorkflowType,
)
from orchestration.state import WorkflowState
from orchestration.workflow_registry import (
    get_next_step,
    get_step_index,
    get_workflow_step_sequence,
)
from repository.domain_repository import DomainRepository
from schemas.audit import AuditEventCreate
from tools.approval_workflow import ApprovalWorkflow
from tools.audit_trail import AuditTrail

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MasterOrchestrator:
    """Coordinates deterministic agent pipelines with durable checkpoints and audit tracking."""

    def __init__(
        self,
        checkpoint_store: SqliteCheckpointStore,
        domain_repo: DomainRepository,
        approval_workflow: ApprovalWorkflow,
        audit_trail: AuditTrail,
        document_agent: DocumentIntelligenceAgent | None = None,
        maintenance_agent: MaintenanceAgent | None = None,
        max_retries: int = 3,
        worker_id: str | None = None,
    ) -> None:
        self.store = checkpoint_store
        self.domain_repo = domain_repo
        self.approval_workflow = approval_workflow
        self.audit_trail = audit_trail
        self.document_agent = document_agent or DocumentIntelligenceAgent()
        self.maintenance_agent = maintenance_agent or MaintenanceAgent(self.domain_repo)
        self.max_retries = max_retries
        self.worker_id = worker_id or f"worker-{uuid.uuid4()}"

        # Initialize node handlers
        self.nodes = {
            StepName.DOCUMENT_ANALYSIS: DocumentAnalysisNode(self.document_agent),
            StepName.FINANCIAL_OPERATIONS: FinancialOperationsNode(),
            StepName.POLICY_VALIDATION: PolicyValidationNode(),
            StepName.APPROVAL_GATE: ApprovalGateNode(self.approval_workflow),
            StepName.EXECUTE_ACTION: ActionExecutionNode(self.domain_repo),
            StepName.VERIFY_OUTCOME: OutcomeVerificationNode(self.domain_repo),
            StepName.MAINTENANCE_TRIAGE: MaintenanceTriageNode(self.maintenance_agent),
            StepName.MAINTENANCE_DISPATCH: MaintenanceDispatchNode(self.maintenance_agent, self.domain_repo),
        }

    def _emit_audit(
        self,
        event_type: str,
        state: WorkflowState,
        payload: dict[str, Any] | None = None,
        actor_id: str | None = None,
    ) -> None:
        try:
            self.audit_trail.append(
                AuditEventCreate(
                    actor_id=actor_id or state.initiated_by,
                    actor_type="orchestrator",
                    event_type=event_type,
                    entity_type="workflow",
                    entity_id=state.workflow_id,
                    property_id=state.property_id,
                    correlation_id=state.correlation_id,
                    payload=payload or {},
                )
            )
        except Exception as exc:
            logger.warning("Audit emission failed for %s on workflow %s: %s", event_type, state.workflow_id, exc)

    def create_workflow(
        self,
        property_id: str,
        initiated_by: str,
        request: WorkflowCreateRequest,
        correlation_id: str | None = None,
    ) -> WorkflowState:
        """Initialize, persist, and audit a new property-scoped workflow."""
        workflow_id = f"wf-{uuid.uuid4()}"
        cid = correlation_id or f"corr-{uuid.uuid4()}"

        state = WorkflowState(
            workflow_id=workflow_id,
            property_id=property_id,
            initiated_by=initiated_by,
            workflow_type=request.workflow_type,
            status=WorkflowStatus.CREATED,
            input_reference=request.input_reference,
            allocation_instructions=[
                instr.model_dump(mode="json") if hasattr(instr, "model_dump") else instr
                for instr in (request.allocation_instructions or [])
            ] or None,
            correlation_id=cid,
            metadata=request.metadata,
        )

        saved = self.store.save_workflow(state)
        self._emit_audit(
            "workflow.created",
            saved,
            payload={
                "workflow_type": request.workflow_type.value,
                "input_reference": request.input_reference,
            },
        )
        return saved

    def get_workflow(self, property_id: str, workflow_id: str) -> WorkflowState:
        state = self.store.get_workflow(property_id, workflow_id)
        if not state:
            raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found in property '{property_id}'.")
        return state

    def list_workflows(
        self,
        property_id: str,
        status: WorkflowStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowSummary]:
        return self.store.list_workflows(
            property_id=property_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    def list_steps(self, property_id: str, workflow_id: str) -> list[WorkflowStepRecord]:
        self.get_workflow(property_id, workflow_id)  # Validate property ownership
        return self.store.list_steps(workflow_id)

    def run_workflow(self, property_id: str, workflow_id: str) -> WorkflowState:
        """Deterministically advance the workflow through its registered steps with durable checkpoints."""
        state = self.get_workflow(property_id, workflow_id)

        if state.status.is_terminal:
            return state

        # Atomic concurrency protection: claim lock before execution
        claimed = self.store.claim_step(workflow_id, self.worker_id)
        if not claimed:
            raise ConcurrentExecutionError(
                f"Workflow '{workflow_id}' is actively being processed by another worker."
            )

        try:
            sequence = get_workflow_step_sequence(state.workflow_type)

            for step_idx, step_name in enumerate(sequence):
                # Check existing step execution record in checkpoint store
                existing_step = self.store.get_step(workflow_id, step_name)
                if existing_step and existing_step.status == StepStatus.COMPLETED:
                    continue  # Already completed in prior run, resume forward without repeating!

                state.status = WorkflowStatus.RUNNING
                state.current_step = step_name
                self.store.save_workflow(state)

                step_record = WorkflowStepRecord(
                    step_id=f"step-{uuid.uuid4()}",
                    workflow_id=workflow_id,
                    step_name=step_name,
                    step_index=step_idx,
                    status=StepStatus.RUNNING,
                    started_at=_utc_now(),
                    retry_count=existing_step.retry_count if existing_step else 0,
                )
                self.store.record_step(step_record)
                self._emit_audit("workflow.step_started", state, payload={"step_name": step_name.value})

                # Execute step with bounded retry for transient faults
                attempt = 0
                node = self.nodes.get(step_name)
                if not node:
                    raise StepExecutionError(f"No node handler registered for step '{step_name.value}'.")

                step_succeeded = False
                while attempt <= self.max_retries and not step_succeeded:
                    try:
                        state = node.execute(state)
                        step_succeeded = True
                    except TransientStepError as exc:
                        attempt += 1
                        step_record.retry_count = attempt
                        state.retry_count += 1
                        self.store.record_step(step_record)
                        self.store.save_workflow(state)
                        self._emit_audit(
                            "workflow.step_retry",
                            state,
                            payload={"step_name": step_name.value, "attempt": attempt, "error": str(exc)},
                        )
                        if attempt > self.max_retries:
                            step_record.status = StepStatus.FAILED
                            step_record.error_message = f"Max retries exceeded: {exc}"
                            step_record.completed_at = _utc_now()
                            self.store.record_step(step_record)

                            state.status = WorkflowStatus.FAILED
                            state.errors.append(str(exc))
                            state.completed_at = _utc_now()
                            self.store.save_workflow(state)
                            self._emit_audit(
                                "workflow.step_failed",
                                state,
                                payload={"step_name": step_name.value, "error": str(exc)},
                            )
                            raise StepExecutionError(f"Step '{step_name.value}' failed after {attempt} retries: {exc}") from exc
                    except (PolicyValidationError, StepExecutionError, Exception) as exc:
                        # Non-transient or policy denial: fail step and workflow immediately without blind retry
                        step_record.status = StepStatus.FAILED
                        step_record.error_message = str(exc)
                        step_record.completed_at = _utc_now()
                        self.store.record_step(step_record)

                        state.status = WorkflowStatus.FAILED
                        state.errors.append(str(exc))
                        state.completed_at = _utc_now()
                        self.store.save_workflow(state)
                        self._emit_audit(
                            "workflow.step_failed",
                            state,
                            payload={"step_name": step_name.value, "error": str(exc)},
                        )
                        raise

                # Check if step resulted in pause (Approval Gate or Review)
                if state.status == WorkflowStatus.AWAITING_APPROVAL:
                    step_record.status = StepStatus.RUNNING
                    step_record.output_summary = f"Awaiting approval request: {state.approval_request_id}"
                    self.store.record_step(step_record)
                    self.store.save_workflow(state)
                    self._emit_audit(
                        "workflow.approval_requested",
                        state,
                        payload={"approval_request_id": state.approval_request_id},
                    )
                    return state

                if state.status == WorkflowStatus.NEEDS_REVIEW:
                    step_record.status = StepStatus.RUNNING
                    step_record.output_summary = f"Paused for manual review: {', '.join(state.errors)}"
                    self.store.record_step(step_record)
                    self.store.save_workflow(state)
                    self._emit_audit(
                        "workflow.needs_review",
                        state,
                        payload={"reasons": state.errors},
                    )
                    return state

                # Step completed successfully
                step_record.status = StepStatus.COMPLETED
                step_record.completed_at = _utc_now()
                step_record.output_summary = f"Completed step '{step_name.value}'"
                self.store.record_step(step_record)
                self.store.save_workflow(state)
                self._emit_audit("workflow.step_completed", state, payload={"step_name": step_name.value})

            # All steps completed
            state.status = WorkflowStatus.COMPLETED
            state.completed_at = _utc_now()
            self.store.save_workflow(state)
            self._emit_audit("workflow.completed", state)
            return state

        finally:
            self.store.release_claim(workflow_id, self.worker_id)

    def resume_workflow(
        self,
        property_id: str,
        workflow_id: str,
        resume_request: WorkflowResumeRequest | None = None,
        actor_id: str | None = None,
    ) -> WorkflowState:
        """Resume a paused workflow (e.g. after human approval or review resolution)."""
        state = self.get_workflow(property_id, workflow_id)

        if not state.status.is_resumable:
            raise WorkflowStateError(
                f"Cannot resume workflow '{workflow_id}' in terminal state '{state.status.value}'."
            )

        self._emit_audit(
            "workflow.resumed",
            state,
            payload={"reason": resume_request.reason if resume_request else None},
            actor_id=actor_id,
        )

        return self.run_workflow(property_id, workflow_id)

    def cancel_workflow(
        self,
        property_id: str,
        workflow_id: str,
        cancel_request: WorkflowCancelRequest,
        actor_id: str | None = None,
    ) -> WorkflowState:
        """Safely cancel a workflow if it has not already reached a terminal state."""
        state = self.get_workflow(property_id, workflow_id)

        if state.status.is_terminal:
            raise WorkflowStateError(
                f"Cannot cancel workflow '{workflow_id}' in terminal state '{state.status.value}'."
            )

        state.status = WorkflowStatus.CANCELLED
        state.completed_at = _utc_now()
        state.errors.append(f"Cancelled by user: {cancel_request.reason}")
        saved = self.store.save_workflow(state)

        self._emit_audit(
            "workflow.cancelled",
            saved,
            payload={"reason": cancel_request.reason},
            actor_id=actor_id,
        )
        return saved
