"""Executable workflow step nodes with deterministic idempotency and policy gates."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from agents.document_agent import DocumentIntelligenceAgent
from agents.maintenance_agent import MaintenanceAgent
from orchestration.exceptions import (
    PolicyValidationError,
    StepExecutionError,
    TransientStepError,
)
from orchestration.schemas import (
    StepName,
    StepStatus,
    WorkflowStatus,
    WorkflowType,
)
from orchestration.state import WorkflowState
from repository.domain_repository import DomainRepository
from schemas.approvals import ApprovalRequestCreate
from schemas.domain import (
    ExpenseCategory,
    ExpenseCreate,
    ExpenseStatus,
    PaymentCreate,
    PaymentMethod,
    PaymentStatus,
    ResidentCreate,
    UnitCreate,
    VendorCategory,
    VendorCreate,
    WorkOrderPriority,
    WorkOrderStatus,
    WorkOrderUpdate,
)
from schemas.maintenance_agent import (
    AutoDispatchWorkOrderRequest,
    MaintenanceCategory,
    MaintenanceTriageRequest,
)
from schemas.payment_allocation import PaymentAllocationInstruction
from tools.action_approval_service import submit_action_for_approval
from tools.action_policy_validator import PolicyStatus, validate_proposed_action
from tools.approval_workflow import ApprovalWorkflow
from tools.financial_operations_orchestrator import run_financial_operations


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentAnalysisNode:
    """Intake and parse documents using DocumentIntelligenceAgent."""

    def __init__(self, agent: DocumentIntelligenceAgent | None = None) -> None:
        self.agent = agent or DocumentIntelligenceAgent()

    def execute(self, state: WorkflowState) -> WorkflowState:
        if state.document_result is not None:
            return state

        path = Path(state.input_reference)
        if not path.exists():
            raise StepExecutionError(f"Input document reference '{state.input_reference}' does not exist on disk.")

        if path.suffix.lower() != ".pdf":
            raise StepExecutionError(f"Input document '{state.input_reference}' must be a PDF file.")

        try:
            result = self.agent.process(path)
            state.document_result = result.model_dump(mode="json")
            return state
        except Exception as exc:
            raise StepExecutionError(f"Document analysis failed: {exc}") from exc


class FinancialOperationsNode:
    """Perform reconciliation, duplicate detection, and payment allocation."""

    def execute(self, state: WorkflowState) -> WorkflowState:
        if state.financial_result is not None:
            return state

        if not state.document_result:
            raise StepExecutionError("Document analysis result is missing.")

        allocation_instructions = None
        if state.allocation_instructions:
            allocation_instructions = [
                PaymentAllocationInstruction(**item) if isinstance(item, dict) else item
                for item in state.allocation_instructions
            ]

        try:
            fin_ops_result = run_financial_operations(
                document_agent_output=state.document_result,
                allocation_instructions=allocation_instructions,
            )
            state.financial_result = fin_ops_result.model_dump(mode="json")
            return state
        except Exception as exc:
            raise StepExecutionError(f"Financial operations analysis failed: {exc}") from exc


class MaintenanceTriageNode:
    """Triage maintenance request, assign priority, SLA, and detect safety hazards."""

    def __init__(self, agent: MaintenanceAgent) -> None:
        self.agent = agent

    def execute(self, state: WorkflowState) -> WorkflowState:
        if state.maintenance_result and "triage" in state.maintenance_result:
            return state

        desc = state.metadata.get("description")
        if not desc:
            ref_path = Path(state.input_reference)
            if ref_path.exists() and ref_path.is_file():
                try:
                    desc = ref_path.read_text(encoding="utf-8")
                except Exception:
                    desc = state.input_reference
            else:
                desc = state.input_reference

        unit_id = state.metadata.get("unit_id")
        triage_req = MaintenanceTriageRequest(description=desc, unit_id=unit_id)
        result = self.agent.triage_request(triage_req)

        if state.maintenance_result is None:
            state.maintenance_result = {}
        state.maintenance_result["triage"] = result.model_dump(mode="json")
        return state


class MaintenanceDispatchNode:
    """Automatically create and dispatch work order to optimal vendor based on triage."""

    def __init__(self, agent: MaintenanceAgent, domain_repo: DomainRepository) -> None:
        self.agent = agent
        self.domain_repo = domain_repo

    def execute(self, state: WorkflowState) -> WorkflowState:
        if state.maintenance_result and "dispatch" in state.maintenance_result:
            return state

        triage_data = (state.maintenance_result or {}).get("triage", {})
        title = state.metadata.get("title") or f"{triage_data.get('category', 'General')} Maintenance"
        desc = state.metadata.get("description") or state.input_reference
        unit_id = state.metadata.get("unit_id")
        raw_cost = state.metadata.get("estimated_cost")
        estimated_cost = Decimal(str(raw_cost)) if raw_cost is not None else None

        # Ensure property exists in domain DB
        self.domain_repo.ensure_property(property_id=state.property_id, name=f"Property {state.property_id}")

        # Ensure unit exists in property
        if not unit_id:
            units = self.domain_repo.list_units(state.property_id, limit=1)
            if units:
                unit_id = units[0].unit_id
            else:
                new_unit = self.domain_repo.create_unit(state.property_id, UnitCreate(unit_number="MAIN-BLDG"))
                unit_id = new_unit.unit_id
        else:
            unit = self.domain_repo.get_unit(state.property_id, unit_id)
            if not unit:
                new_unit = self.domain_repo.create_unit(state.property_id, UnitCreate(unit_number=unit_id, unit_id=unit_id))
                unit_id = new_unit.unit_id

        dispatch_req = AutoDispatchWorkOrderRequest(
            title=title,
            description=desc,
            unit_id=unit_id,
            category=triage_data.get("category", MaintenanceCategory.GENERAL),
            priority=triage_data.get("priority", WorkOrderPriority.MEDIUM),
            estimated_cost=estimated_cost,
        )

        dispatch_res = self.agent.auto_dispatch(state.property_id, dispatch_req)

        if state.maintenance_result is None:
            state.maintenance_result = {}
        state.maintenance_result["dispatch"] = dispatch_res.model_dump(mode="json")
        return state


class PolicyValidationNode:
    """Validate proposed actions against policy, discrepancies, and duplicate risks."""

    def execute(self, state: WorkflowState) -> WorkflowState:
        if state.validation_result is not None:
            return state

        if state.workflow_type == WorkflowType.MAINTENANCE_LIFECYCLE:
            if not state.maintenance_result:
                raise StepExecutionError("Maintenance analysis result is missing for policy validation.")

            dispatch = state.maintenance_result.get("dispatch", {})
            triage = state.maintenance_result.get("triage", {})
            work_order_id = dispatch.get("work_order_id") or f"wo-{state.workflow_id}"

            raw_cost = state.metadata.get("estimated_cost", "0.00")
            is_emergency = bool(triage.get("is_emergency", False) or dispatch.get("priority") == "emergency")
            cost_decimal = Decimal(str(raw_cost)) if raw_cost is not None else Decimal("0.00")

            approval_threshold = Decimal(str(state.metadata.get("approval_threshold", "1000.00")))
            requires_approval = (cost_decimal > approval_threshold) or is_emergency

            action_payload = {
                "action_id": f"act-{state.workflow_id}",
                "action_type": "authorize_work_order",
                "status": "proposed",
                "amount": str(cost_decimal),
                "currency": "USD",
                "entity_id": work_order_id,
                "requires_approval": requires_approval,
                "evidence": [work_order_id],
                "unresolved_issues": [triage.get("emergency_rationale")] if is_emergency and triage.get("emergency_rationale") else [],
            }

            state.validation_result = {
                "status": "needs_review" if requires_approval else "approved",
                "reasons": [
                    f"Work order requires manager approval: estimated cost ${cost_decimal} exceeds ${approval_threshold} threshold or is emergency."
                ] if requires_approval else ["Pre-approved within standard operational maintenance budget limits."],
                "eligible_for_approval": True,
                "action_payload": action_payload,
            }
            return state

        if not state.financial_result:
            raise StepExecutionError("Financial analysis result is missing for policy validation.")

        fin_data = state.financial_result
        records = fin_data.get("records", [])
        findings = fin_data.get("findings", [])
        matches = fin_data.get("matches", [])
        allocations = fin_data.get("payment_allocations", [])

        # Extract amount and currency from parsed records or default to first record
        amount = None
        currency = "USD"
        evidence_ids = []

        if records:
            first_rec = records[0]
            amount = first_rec.get("amount")
            currency = first_rec.get("currency") or "USD"
            evidence_ids = [first_rec.get("document_id")] if first_rec.get("document_id") else []

        action_type = "propose_expense_payment" if state.workflow_type == WorkflowType.INVOICE_PROCESSING else "propose_payment"
        action_payload = {
            "action_id": f"act-{state.workflow_id}",
            "action_type": action_type,
            "status": "proposed",
            "amount": amount,
            "currency": currency,
            "evidence": evidence_ids,
            "unresolved_issues": [f.get("description") for f in findings if f.get("severity") in ("high", "critical")],
        }

        policy_res = validate_proposed_action(
            action_payload,
            findings=findings,
            records=records,
            matches=matches,
            allocations=allocations,
        )

        val_summary = {
            "status": policy_res.status.value,
            "reasons": policy_res.reasons,
            "eligible_for_approval": policy_res.eligible_for_approval,
            "action_payload": action_payload,
        }
        state.validation_result = val_summary

        if policy_res.status == PolicyStatus.BLOCKED:
            state.status = WorkflowStatus.FAILED
            state.errors.extend(policy_res.reasons)
            raise PolicyValidationError(f"Action blocked by policy: {', '.join(policy_res.reasons)}")

        if policy_res.status == PolicyStatus.MANUAL_REVIEW:
            state.status = WorkflowStatus.NEEDS_REVIEW
            state.errors.extend(policy_res.reasons)
            return state

        return state


class ApprovalGateNode:
    """Manage human approval pause and authorized decision resume."""

    def __init__(self, approval_workflow: ApprovalWorkflow) -> None:
        self.approval_workflow = approval_workflow

    def execute(self, state: WorkflowState) -> WorkflowState:
        # If approval request hasn't been created yet, create it and pause
        if not state.approval_request_id:
            val = state.validation_result or {}
            action_payload = val.get("action_payload", {})

            # For maintenance lifecycle, skip approval if within pre-approved thresholds
            if state.workflow_type == WorkflowType.MAINTENANCE_LIFECYCLE and not action_payload.get("requires_approval", True):
                state.status = WorkflowStatus.RUNNING
                return state

            amount_str = str(action_payload.get("amount")) if action_payload.get("amount") is not None else None
            currency_str = action_payload.get("currency") or "USD"

            request_create = ApprovalRequestCreate(
                action_id=action_payload.get("action_id", f"act-{state.workflow_id}"),
                action_type=action_payload.get("action_type", "propose_action"),
                requested_by="orchestrator",
                approver_ids=[state.initiated_by],  # Authorized manager or caller
                rationale=f"Automated workflow review for {state.workflow_type.value} ({state.workflow_id})",
                amount=amount_str,
                currency=currency_str,
                evidence_ids=action_payload.get("evidence", []),
                expires_at=_utc_now() + timedelta(days=7),
                correlation_id=state.correlation_id,
                property_id=state.property_id,
            )

            approval_req = self.approval_workflow.create_request(request_create)
            state.approval_request_id = approval_req.approval_id
            state.status = WorkflowStatus.AWAITING_APPROVAL
            return state

        # If approval request already exists, evaluate decision
        approval = self.approval_workflow.get_request(state.approval_request_id)
        if approval.status == "pending":
            state.status = WorkflowStatus.AWAITING_APPROVAL
            return state

        if approval.status == "rejected":
            state.status = WorkflowStatus.FAILED
            state.errors.append(f"Approval rejected: {approval.decision_reason or 'No reason specified'}")
            raise StepExecutionError(f"Approval was rejected: {approval.decision_reason}")

        if approval.status == "expired":
            state.status = WorkflowStatus.FAILED
            state.errors.append("Approval request expired before decision was rendered.")
            raise StepExecutionError("Approval request expired.")

        if approval.status == "approved":
            state.status = WorkflowStatus.RUNNING
            return state

        return state


class ActionExecutionNode:
    """Execute supported ledger mutations upon verified policy and authorized approval."""

    def __init__(self, domain_repo: DomainRepository) -> None:
        self.domain_repo = domain_repo

    def execute(self, state: WorkflowState) -> WorkflowState:
        if state.action_result is not None:
            return state

        val = state.validation_result or {}
        if not val.get("eligible_for_approval"):
            raise StepExecutionError("Cannot execute action: Action is not eligible.")

        action_payload = val.get("action_payload", {})
        raw_amt = action_payload.get("amount")
        amt = Decimal(str(raw_amt)) if raw_amt is not None else Decimal("0.00")
        currency = action_payload.get("currency") or "USD"

        # Ensure property exists in domain DB
        self.domain_repo.ensure_property(property_id=state.property_id, name=f"Property {state.property_id}")

        if state.workflow_type == WorkflowType.INVOICE_PROCESSING:
            # Ensure a default vendor exists for invoice expense
            vendors = self.domain_repo.list_vendors(state.property_id, limit=1)
            if vendors:
                vendor_id = vendors[0].vendor_id
            else:
                new_v = self.domain_repo.create_vendor(
                    state.property_id,
                    VendorCreate(
                        name="Workflow Invoice Payee",
                        category=VendorCategory.GENERAL,
                    ),
                )
                vendor_id = new_v.vendor_id

            expense = self.domain_repo.create_expense(
                state.property_id,
                ExpenseCreate(
                    category=ExpenseCategory.MANAGEMENT,
                    amount=amt,
                    currency=currency,
                    expense_date=date.today(),
                    status=ExpenseStatus.APPROVED,
                    description=f"Automated invoice execution for workflow {state.workflow_id}",
                    vendor_id=vendor_id,
                ),
            )

            state.action_result = {
                "status": "executed",
                "action_type": "record_expense",
                "entity_type": "expense",
                "entity_id": expense.expense_id,
                "amount": str(expense.amount),
                "executed_at": _utc_now().isoformat(),
            }

        elif state.workflow_type == WorkflowType.RENT_RECONCILIATION:
            # Ensure a resident exists for rent payment record
            residents = self.domain_repo.list_residents(state.property_id, limit=1)
            if residents:
                resident_id = residents[0].resident_id
            else:
                new_r = self.domain_repo.create_resident(
                    state.property_id,
                    ResidentCreate(
                        first_name="Reconciled",
                        last_name="Resident",
                        email=f"resident_{state.workflow_id[:8]}@example.com",
                    ),
                )
                resident_id = new_r.resident_id

            pmt = self.domain_repo.create_payment(
                state.property_id,
                PaymentCreate(
                    amount=amt,
                    currency=currency,
                    payment_date=date.today(),
                    payment_method=PaymentMethod.ACH,
                    status=PaymentStatus.SETTLED,
                    resident_id=resident_id,
                    reference_number=f"WF-{state.workflow_id[:12]}",
                ),
            )

            state.action_result = {
                "status": "executed",
                "action_type": "record_payment",
                "entity_type": "payment",
                "entity_id": pmt.payment_id,
                "amount": str(pmt.amount),
                "executed_at": _utc_now().isoformat(),
            }

        elif state.workflow_type == WorkflowType.MAINTENANCE_LIFECYCLE:
            dispatch = (state.maintenance_result or {}).get("dispatch", {})
            work_order_id = dispatch.get("work_order_id") or action_payload.get("entity_id")
            assigned_v = dispatch.get("assigned_vendor")
            vendor_id = assigned_v.get("vendor_id") if isinstance(assigned_v, dict) else None

            if work_order_id:
                try:
                    self.domain_repo.update_work_order(
                        state.property_id,
                        work_order_id,
                        WorkOrderUpdate(status=WorkOrderStatus.IN_PROGRESS),
                    )
                except Exception:
                    pass

            expense_id = None
            if amt > Decimal("0.00"):
                if not vendor_id:
                    vendors = self.domain_repo.list_vendors(state.property_id, limit=1)
                    vendor_id = vendors[0].vendor_id if vendors else None

                expense = self.domain_repo.create_expense(
                    state.property_id,
                    ExpenseCreate(
                        category=ExpenseCategory.MAINTENANCE,
                        amount=amt,
                        currency=currency,
                        expense_date=date.today(),
                        status=ExpenseStatus.APPROVED,
                        description=f"Authorized work order {work_order_id} execution for workflow {state.workflow_id}",
                        vendor_id=vendor_id,
                    ),
                )
                expense_id = expense.expense_id

            state.action_result = {
                "status": "executed",
                "action_type": "authorize_work_order",
                "entity_type": "work_order",
                "entity_id": work_order_id,
                "expense_id": expense_id,
                "amount": str(amt),
                "executed_at": _utc_now().isoformat(),
            }

        else:
            state.action_result = {
                "status": "executed",
                "action_type": "audit_analysis_only",
                "entity_type": "audit",
                "entity_id": state.workflow_id,
                "executed_at": _utc_now().isoformat(),
            }

        return state


class OutcomeVerificationNode:
    """Verify execution outcome against actual database evidence and amounts."""

    def __init__(self, domain_repo: DomainRepository) -> None:
        self.domain_repo = domain_repo

    def execute(self, state: WorkflowState) -> WorkflowState:
        if state.verification_result is not None:
            return state

        evidence_details: list[str] = []

        if state.workflow_type == WorkflowType.FINANCIAL_AUDIT:
            if not state.financial_result:
                raise StepExecutionError("Financial analysis result is missing for audit verification.")
            summary = state.financial_result.get("summary", {})
            evidence_details.append(
                f"Verified financial audit results: {summary.get('reconciled_count', 0)} reconciled, "
                f"{summary.get('discrepancy_count', 0)} discrepancies."
            )
        else:
            if not state.action_result:
                raise StepExecutionError("Action result is missing for verification.")

            act = state.action_result
            entity_type = act.get("entity_type")
            entity_id = act.get("entity_id")
            expected_amt = act.get("amount")

            if entity_type == "expense":
                exp = self.domain_repo.get_expense(state.property_id, entity_id)
                if not exp:
                    raise StepExecutionError(f"Verification failed: Expense '{entity_id}' not found in database.")
                if str(exp.amount) != expected_amt:
                    raise StepExecutionError(f"Verification failed: Expense amount '{exp.amount}' != expected '{expected_amt}'.")
                evidence_details.append(f"Verified expense {entity_id} with amount {exp.amount} {exp.currency} in domain database.")

            elif entity_type == "payment":
                pmt = self.domain_repo.get_payment(state.property_id, entity_id)
                if not pmt:
                    raise StepExecutionError(f"Verification failed: Payment '{entity_id}' not found in database.")
                if str(pmt.amount) != expected_amt:
                    raise StepExecutionError(f"Verification failed: Payment amount '{pmt.amount}' != expected '{expected_amt}'.")
                evidence_details.append(f"Verified payment {entity_id} with amount {pmt.amount} {pmt.currency} in domain database.")

            elif entity_type == "work_order":
                wo = self.domain_repo.get_work_order(state.property_id, entity_id)
                if not wo:
                    raise StepExecutionError(f"Verification failed: Work order '{entity_id}' not found in database.")
                evidence_details.append(f"Verified work order {entity_id} active with status {wo.status.value}.")
                expense_id = act.get("expense_id")
                if expense_id:
                    exp = self.domain_repo.get_expense(state.property_id, expense_id)
                    if not exp:
                        raise StepExecutionError(f"Verification failed: Linked expense '{expense_id}' not found in database.")
                    evidence_details.append(f"Verified linked maintenance expense {expense_id} for amount {exp.amount} {exp.currency}.")

            else:
                evidence_details.append(f"Verified audit execution for workflow {state.workflow_id}.")

        state.verification_result = {
            "verified": True,
            "evidence_details": evidence_details,
            "verified_at": _utc_now().isoformat(),
        }
        state.status = WorkflowStatus.COMPLETED
        state.completed_at = _utc_now()
        return state
