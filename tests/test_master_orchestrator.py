"""Comprehensive tests for ENIGMA Phase 3 Master Orchestrator and durable workflows.

Covers:
1. Workflow creation, validation, and property-scoping.
2. End-to-end execution of Invoice Processing workflow (Analysis -> FinOps -> Policy -> Approval -> Action -> Verification).
3. End-to-end Rent Reconciliation workflow (Payment recording & verification).
4. Financial Audit workflow (analysis-only pipeline without action mutation).
5. Persistence and recovery across process restarts (reopening SQLite databases).
6. Idempotent resumption without repeating completed steps.
7. Approval rejection and policy denial handling.
8. Cross-property isolation and security boundaries.
9. Role/permission enforcement on workflow API endpoints.
10. Concurrent worker lock protection.
11. Bounded retry for transient failures.
12. Safe cancellation and terminal state validation.
13. Unbroken cryptographic audit hash-chain verification.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pymupdf
import pytest
from fastapi.testclient import TestClient

import main
from orchestration import (
    ConcurrentExecutionError,
    MasterOrchestrator,
    PolicyValidationError,
    SqliteCheckpointStore,
    StepExecutionError,
    StepName,
    StepStatus,
    TransientStepError,
    WorkflowCancelRequest,
    WorkflowCreateRequest,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowResumeRequest,
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
    WorkflowSummary,
    WorkflowType,
)
from repository.domain_repository import DomainRepository
from schemas.approvals import ApprovalDecision
from schemas.auth import Permission, UserRole
from schemas.domain import ExpenseStatus, PaymentStatus
from tools.approval_workflow import ApprovalWorkflow
from tools.audit_trail import AuditTrail
from tools.auth_config import AuthConfig
from tools.auth_crypto import create_access_token, hash_password
from tools.auth_dependencies import (
    get_auth_config,
    get_auth_repository,
    get_checkpoint_store,
    get_domain_repository,
)
from tools.auth_repository import AuthRepository

TEST_SECRET = "phase3-orchestrator-secret-key-at-least-32-chars-long"


def create_sample_invoice_pdf(path: Path, amount_str: str = "450.00") -> Path:
    """Generate a clean synthetic invoice PDF for end-to-end processing."""
    doc = pymupdf.open()
    page = doc.new_page()
    text = (
        f"INVOICE\n"
        f"Invoice Number: INV-2026-9901\n"
        f"Date: 2026-03-01\n"
        f"Vendor: Summit Maintenance Services\n"
        f"Description: Emergency HVAC fan replacement\n"
        f"Line Item 1: HVAC Fan Unit - ${amount_str}\n"
        f"Total Amount: ${amount_str}\n"
    )
    page.insert_text((50, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def create_sample_rent_pdf(path: Path, amount_str: str = "1200.00") -> Path:
    """Generate a clean synthetic rent receipt PDF."""
    doc = pymupdf.open()
    page = doc.new_page()
    text = (
        f"RENT RECEIPT\n"
        f"Reference: REC-2026-0042\n"
        f"Date: 2026-03-05\n"
        f"Tenant: Alex Morgan\n"
        f"Description: Monthly Rent Unit 204\n"
        f"Amount Received: ${amount_str}\n"
        f"Total Due: ${amount_str}\n"
    )
    page.insert_text((50, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def orchestrator_env(tmp_path: Path):
    auth_db = tmp_path / "test_auth.sqlite3"
    audit_db = tmp_path / "test_audit.sqlite3"
    approvals_db = tmp_path / "test_approvals.sqlite3"
    domain_db = tmp_path / "test_domain.sqlite3"
    workflows_db = tmp_path / "test_workflows.sqlite3"

    auth_repo = AuthRepository(auth_db)
    audit = AuditTrail(audit_db)
    workflow = ApprovalWorkflow(approvals_db, audit)
    domain_repo = DomainRepository(domain_db)
    store = SqliteCheckpointStore(workflows_db)

    orchestrator = MasterOrchestrator(
        checkpoint_store=store,
        domain_repo=domain_repo,
        approval_workflow=workflow,
        audit_trail=audit,
    )

    config = AuthConfig(
        secret_key=TEST_SECRET,
        algorithm="HS256",
        access_token_expire_minutes=60,
        auth_db_path=auth_db,
        domain_db_path=domain_db,
        workflows_db_path=workflows_db,
    )

    # Properties
    prop_a = auth_repo.create_property(name="Highland Park", property_id="prop-highland")
    prop_b = auth_repo.create_property(name="Lowland Meadows", property_id="prop-lowland")
    domain_repo.ensure_property(property_id=prop_a.property_id, name=prop_a.name)
    domain_repo.ensure_property(property_id=prop_b.property_id, name=prop_b.name)

    # Users
    pwd_hash = hash_password("ValidPassword123!")
    manager = auth_repo.create_user(email="manager@example.com", password_hash=pwd_hash)
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=manager.user_id,
        role=UserRole.PROPERTY_MANAGER,
    )

    accountant = auth_repo.create_user(email="accountant@example.com", password_hash=pwd_hash)
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=accountant.user_id,
        role=UserRole.ACCOUNTANT,
    )

    resident_user = auth_repo.create_user(email="resident@example.com", password_hash=pwd_hash)
    auth_repo.create_membership(
        property_id=prop_a.property_id,
        user_id=resident_user.user_id,
        role=UserRole.RESIDENT,
    )

    # Dependency overrides for API testing
    main.app.dependency_overrides[get_auth_config] = lambda: config
    main.app.dependency_overrides[get_auth_repository] = lambda: auth_repo
    main.app.dependency_overrides[get_domain_repository] = lambda: domain_repo
    main.app.dependency_overrides[get_checkpoint_store] = lambda: store
    main.app.dependency_overrides[main.get_audit_trail] = lambda: audit
    main.app.dependency_overrides[main.get_approval_workflow] = lambda: workflow
    main.app.dependency_overrides[main.get_master_orchestrator] = lambda: orchestrator

    client = TestClient(main.app)

    try:
        yield {
            "tmp_path": tmp_path,
            "auth_repo": auth_repo,
            "audit": audit,
            "workflow": workflow,
            "domain_repo": domain_repo,
            "store": store,
            "orchestrator": orchestrator,
            "client": client,
            "prop_a": prop_a,
            "prop_b": prop_b,
            "manager": manager,
            "accountant": accountant,
            "resident_user": resident_user,
            "db_paths": {
                "auth": auth_db,
                "audit": audit_db,
                "approvals": approvals_db,
                "domain": domain_db,
                "workflows": workflows_db,
            },
        }
    finally:
        main.app.dependency_overrides.clear()


def make_token(user_id: str, email: str) -> str:
    token, _ = create_access_token(
        user_id=user_id,
        email=email,
        secret_key=TEST_SECRET,
        expires_delta=timedelta(minutes=60),
    )
    return token


def make_auth_headers(user_id: str, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id, email)}"}


# =============================================================================
# 1. Workflow Creation and Validation
# =============================================================================


def test_workflow_creation_and_validation(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    audit = orchestrator_env["audit"]

    req = WorkflowCreateRequest(
        workflow_type=WorkflowType.INVOICE_PROCESSING,
        input_reference="data/input/sample.pdf",
    )

    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=req,
    )

    assert state.workflow_id.startswith("wf-")
    assert state.property_id == prop_a.property_id
    assert state.initiated_by == manager.user_id
    assert state.status == WorkflowStatus.CREATED
    assert state.correlation_id.startswith("corr-")

    # Verify audit event emitted
    events = audit.list_events(entity_id=state.workflow_id, property_id=prop_a.property_id)
    assert len(events) >= 1
    assert events[0].event_type == "workflow.created"


# =============================================================================
# 2. End-to-End Invoice Processing Workflow
# =============================================================================


def test_end_to_end_invoice_processing_workflow(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]
    workflow_engine = orchestrator_env["workflow"]
    domain_repo = orchestrator_env["domain_repo"]
    audit = orchestrator_env["audit"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "invoice_test.pdf", amount_str="750.00")

    # 1. Create and advance workflow
    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.INVOICE_PROCESSING,
            input_reference=str(pdf_path),
        ),
    )

    # First run: processes document analysis, financial ops, policy, and pauses at approval gate
    state = orch.run_workflow(prop_a.property_id, state.workflow_id)
    assert state.status == WorkflowStatus.AWAITING_APPROVAL
    assert state.approval_request_id is not None
    assert state.document_result is not None
    assert state.financial_result is not None
    assert state.validation_result["eligible_for_approval"] is True

    # 2. Render Human Approval Decision
    decision = ApprovalDecision(
        approval_id=state.approval_request_id,
        approver_id=manager.user_id,
        decision="approve",
        reason="Approved valid emergency HVAC repair invoice.",
    )
    workflow_engine.decide(decision)

    # 3. Resume workflow
    resumed_state = orch.resume_workflow(prop_a.property_id, state.workflow_id)
    assert resumed_state.status == WorkflowStatus.COMPLETED
    assert resumed_state.completed_at is not None
    assert resumed_state.action_result["status"] == "executed"
    assert resumed_state.verification_result["verified"] is True

    # 4. Verify domain entity was recorded
    expense_id = resumed_state.action_result["entity_id"]
    exp = domain_repo.get_expense(prop_a.property_id, expense_id)
    assert exp is not None
    assert exp.amount == Decimal("750.00")
    assert exp.status == ExpenseStatus.APPROVED

    # 5. Verify cryptographic audit chain remains 100% valid
    verification = audit.verify_chain()
    assert verification.valid is True


# =============================================================================
# 3. End-to-End Rent Reconciliation Workflow
# =============================================================================


def test_end_to_end_rent_reconciliation_workflow(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    accountant = orchestrator_env["accountant"]
    tmp_path = orchestrator_env["tmp_path"]
    workflow_engine = orchestrator_env["workflow"]
    domain_repo = orchestrator_env["domain_repo"]

    pdf_path = create_sample_rent_pdf(tmp_path / "rent_receipt.pdf", amount_str="1200.00")

    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=accountant.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.RENT_RECONCILIATION,
            input_reference=str(pdf_path),
        ),
    )

    state = orch.run_workflow(prop_a.property_id, state.workflow_id)
    assert state.status == WorkflowStatus.AWAITING_APPROVAL

    # Approve
    workflow_engine.decide(
        ApprovalDecision(
            approval_id=state.approval_request_id,
            approver_id=accountant.user_id,
            decision="approve",
            reason="Verified monthly rent payment receipt.",
        )
    )

    # Resume
    completed_state = orch.resume_workflow(prop_a.property_id, state.workflow_id)
    assert completed_state.status == WorkflowStatus.COMPLETED
    assert completed_state.action_result["entity_type"] == "payment"

    pmt_id = completed_state.action_result["entity_id"]
    payment = domain_repo.get_payment(prop_a.property_id, pmt_id)
    assert payment is not None
    assert payment.amount == Decimal("1200.00")
    assert payment.status == PaymentStatus.SETTLED


# =============================================================================
# 4. Financial Audit Workflow (Analysis-Only Pipeline)
# =============================================================================


def test_financial_audit_workflow_analysis_only(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "audit_doc.pdf", amount_str="300.00")

    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.FINANCIAL_AUDIT,
            input_reference=str(pdf_path),
        ),
    )

    # Financial audit workflow runs through Document Analysis -> Financial Ops -> Policy -> Outcome Verification without approval gate
    completed = orch.run_workflow(prop_a.property_id, state.workflow_id)
    assert completed.status == WorkflowStatus.COMPLETED
    assert completed.approval_request_id is None
    assert completed.verification_result["verified"] is True


# =============================================================================
# 5. Persistence and Recovery Across Process Restart
# =============================================================================


def test_persistence_and_recovery_across_process_restart(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]
    workflow_engine = orchestrator_env["workflow"]
    db_paths = orchestrator_env["db_paths"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "restart_invoice.pdf", amount_str="550.00")

    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.INVOICE_PROCESSING,
            input_reference=str(pdf_path),
        ),
    )
    state = orch.run_workflow(prop_a.property_id, state.workflow_id)

    # Simulate process termination by instantiating new orchestrator and stores from disk
    new_store = SqliteCheckpointStore(db_paths["workflows"])
    new_domain_repo = DomainRepository(db_paths["domain"])
    new_audit = AuditTrail(db_paths["audit"])
    new_workflow_engine = ApprovalWorkflow(db_paths["approvals"], new_audit)

    new_orch = MasterOrchestrator(
        checkpoint_store=new_store,
        domain_repo=new_domain_repo,
        approval_workflow=new_workflow_engine,
        audit_trail=new_audit,
    )

    # Check recovered state
    recovered = new_orch.get_workflow(prop_a.property_id, state.workflow_id)
    assert recovered.status == WorkflowStatus.AWAITING_APPROVAL
    assert recovered.document_result is not None

    # Render approval on fresh engine
    new_workflow_engine.decide(
        ApprovalDecision(
            approval_id=recovered.approval_request_id,
            approver_id=manager.user_id,
            decision="approve",
            reason="Approved after process recovery.",
        )
    )

    # Resume on new orchestrator
    final_state = new_orch.resume_workflow(prop_a.property_id, state.workflow_id)
    assert final_state.status == WorkflowStatus.COMPLETED
    assert final_state.action_result["status"] == "executed"


# =============================================================================
# 6. Idempotent Resumption Without Repeating Completed Steps
# =============================================================================


def test_idempotent_resumption_skips_completed_steps(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]
    workflow_engine = orchestrator_env["workflow"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "idempotent.pdf", amount_str="600.00")

    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.INVOICE_PROCESSING,
            input_reference=str(pdf_path),
        ),
    )
    state = orch.run_workflow(prop_a.property_id, state.workflow_id)

    steps_paused = orch.list_steps(prop_a.property_id, state.workflow_id)
    completed_steps_before = [s.step_name for s in steps_paused if s.status == StepStatus.COMPLETED]
    assert StepName.DOCUMENT_ANALYSIS in completed_steps_before
    assert StepName.FINANCIAL_OPERATIONS in completed_steps_before

    # Mock DocumentAnalysisNode to verify it is NOT called during resume
    orch.nodes[StepName.DOCUMENT_ANALYSIS].execute = MagicMock(
        side_effect=AssertionError("Document analysis should not be called again!")
    )

    workflow_engine.decide(
        ApprovalDecision(
            approval_id=state.approval_request_id,
            approver_id=manager.user_id,
            decision="approve",
            reason="Approval decision",
        )
    )

    resumed = orch.resume_workflow(prop_a.property_id, state.workflow_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    # Mock was never invoked because the completed step was skipped!
    orch.nodes[StepName.DOCUMENT_ANALYSIS].execute.assert_not_called()


# =============================================================================
# 7. Approval Rejection Halts Workflow
# =============================================================================


def test_approval_rejection_halts_workflow(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]
    workflow_engine = orchestrator_env["workflow"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "reject_invoice.pdf", amount_str="800.00")

    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.INVOICE_PROCESSING,
            input_reference=str(pdf_path),
        ),
    )
    state = orch.run_workflow(prop_a.property_id, state.workflow_id)

    # Reject approval
    workflow_engine.decide(
        ApprovalDecision(
            approval_id=state.approval_request_id,
            approver_id=manager.user_id,
            decision="reject",
            reason="Unverified contractor charges.",
        )
    )

    with pytest.raises(StepExecutionError) as exc_info:
        orch.resume_workflow(prop_a.property_id, state.workflow_id)

    assert "rejected" in str(exc_info.value).lower()
    final_state = orch.get_workflow(prop_a.property_id, state.workflow_id)
    assert final_state.status == WorkflowStatus.FAILED
    assert final_state.action_result is None


# =============================================================================
# 8. Policy Denial Blocks Workflow
# =============================================================================


def test_policy_denial_blocks_workflow(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]

    # Negative amount invoice
    pdf_path = create_sample_invoice_pdf(tmp_path / "negative_invoice.pdf", amount_str="-50.00")

    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.INVOICE_PROCESSING,
            input_reference=str(pdf_path),
        ),
    )

    with pytest.raises(PolicyValidationError):
        orch.run_workflow(prop_a.property_id, state.workflow_id)

    failed_state = orch.get_workflow(prop_a.property_id, state.workflow_id)
    assert failed_state.status == WorkflowStatus.FAILED
    assert failed_state.approval_request_id is None


# =============================================================================
# 9. Cross-Property Isolation & Security Boundaries
# =============================================================================


def test_cross_property_isolation(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    prop_b = orchestrator_env["prop_b"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "isolation.pdf")

    state_a = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.INVOICE_PROCESSING,
            input_reference=str(pdf_path),
        ),
    )

    # Attempting to access Property A's workflow under Property B's context must fail
    with pytest.raises(WorkflowNotFoundError):
        orch.get_workflow(prop_b.property_id, state_a.workflow_id)

    with pytest.raises(WorkflowNotFoundError):
        orch.resume_workflow(prop_b.property_id, state_a.workflow_id)

    with pytest.raises(WorkflowNotFoundError):
        orch.cancel_workflow(
            prop_b.property_id,
            state_a.workflow_id,
            WorkflowCancelRequest(reason="Cross property cancel"),
        )

    # Listing workflows on Property B does not leak Property A's workflow
    list_b = orch.list_workflows(prop_b.property_id)
    assert not any(w.workflow_id == state_a.workflow_id for w in list_b)


# =============================================================================
# 10. Concurrent Worker Lock Protection
# =============================================================================


def test_concurrent_worker_lock_protection(orchestrator_env):
    store = orchestrator_env["store"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]
    domain_repo = orchestrator_env["domain_repo"]
    workflow = orchestrator_env["workflow"]
    audit = orchestrator_env["audit"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "concurrent.pdf")

    orch1 = MasterOrchestrator(store, domain_repo, workflow, audit, worker_id="worker-1")
    orch2 = MasterOrchestrator(store, domain_repo, workflow, audit, worker_id="worker-2")

    state = orch1.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.INVOICE_PROCESSING,
            input_reference=str(pdf_path),
        ),
    )

    # Worker 1 claims the lock
    lock_acquired = store.claim_step(state.workflow_id, "worker-1", lock_ttl_seconds=60)
    assert lock_acquired is True

    # Worker 2 attempts to process concurrently
    with pytest.raises(ConcurrentExecutionError):
        orch2.run_workflow(prop_a.property_id, state.workflow_id)

    # Worker 1 releases lock
    store.release_claim(state.workflow_id, "worker-1")

    # Worker 2 can now claim and run
    orch2.run_workflow(prop_a.property_id, state.workflow_id)


# =============================================================================
# 11. Bounded Retry for Transient Failures
# =============================================================================


def test_transient_failure_bounded_retry(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "transient.pdf")

    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.INVOICE_PROCESSING,
            input_reference=str(pdf_path),
        ),
    )

    call_count = 0

    def mock_transient_execute(st):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TransientStepError("Temporary lock or network timeout")
        # Succeeded on 3rd attempt with valid agent result structure
        st.document_result = {
            "status": "completed",
            "source_file": "transient.pdf",
            "documents": [
                {
                    "document_id": "doc-transient-1",
                    "file_name": "transient.pdf",
                    "source_file": "transient.pdf",
                    "document_type": "invoice",
                    "total_amount": 100.0,
                    "currency": "USD",
                    "document_date": "2026-05-01",
                    "vendor_name": "Apex Repair",
                    "confidence": 0.95,
                    "fields": {},
                    "line_items": [],
                    "anomalies": [],
                    "warnings": [],
                }
            ],
        }
        return st

    orch.nodes[StepName.DOCUMENT_ANALYSIS].execute = mock_transient_execute

    advanced = orch.run_workflow(prop_a.property_id, state.workflow_id)
    assert call_count == 3
    assert advanced.retry_count == 2


# =============================================================================
# 12. Safe Cancellation and Terminal State Validation
# =============================================================================


def test_safe_cancellation_and_terminal_validation(orchestrator_env):
    orch = orchestrator_env["orchestrator"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]
    tmp_path = orchestrator_env["tmp_path"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "cancel.pdf")

    state = orch.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=WorkflowCreateRequest(
            workflow_type=WorkflowType.INVOICE_PROCESSING,
            input_reference=str(pdf_path),
        ),
    )
    state = orch.run_workflow(prop_a.property_id, state.workflow_id)
    assert state.status == WorkflowStatus.AWAITING_APPROVAL

    # Cancel while awaiting approval
    cancelled = orch.cancel_workflow(
        prop_a.property_id,
        state.workflow_id,
        WorkflowCancelRequest(reason="Duplicate submission by analyst."),
    )
    assert cancelled.status == WorkflowStatus.CANCELLED
    assert cancelled.completed_at is not None

    # Cannot cancel an already cancelled workflow
    with pytest.raises(WorkflowStateError):
        orch.cancel_workflow(
            prop_a.property_id,
            state.workflow_id,
            WorkflowCancelRequest(reason="Cancel again"),
        )

    # Cannot resume a cancelled workflow
    with pytest.raises(WorkflowStateError):
        orch.resume_workflow(prop_a.property_id, state.workflow_id)


# =============================================================================
# 13. API Endpoints & Role Permission Enforcement
# =============================================================================


def test_api_workflow_endpoints_and_permissions(orchestrator_env):
    client = orchestrator_env["client"]
    prop_a = orchestrator_env["prop_a"]
    prop_b = orchestrator_env["prop_b"]
    manager = orchestrator_env["manager"]
    resident_user = orchestrator_env["resident_user"]
    tmp_path = orchestrator_env["tmp_path"]
    workflow_engine = orchestrator_env["workflow"]

    pdf_path = create_sample_invoice_pdf(tmp_path / "api_workflow.pdf", amount_str="350.00")

    mgr_headers = make_auth_headers(manager.user_id, manager.email)
    res_headers = make_auth_headers(resident_user.user_id, resident_user.email)

    # 1. Resident attempts to create workflow -> 403 Forbidden (lacks FINANCIAL_OPERATIONS)
    res_unauth = client.post(
        f"/api/v1/properties/{prop_a.property_id}/workflows",
        headers=res_headers,
        json={
            "workflow_type": "invoice_processing",
            "input_reference": str(pdf_path),
        },
    )
    assert res_unauth.status_code == 403

    # 2. Manager creates and advances workflow -> 201 Created (pauses at awaiting_approval)
    res_create = client.post(
        f"/api/v1/properties/{prop_a.property_id}/workflows",
        headers=mgr_headers,
        json={
            "workflow_type": "invoice_processing",
            "input_reference": str(pdf_path),
        },
    )
    assert res_create.status_code == 201
    data = res_create.json()
    wf_id = data["workflow_id"]
    assert data["status"] == "awaiting_approval"
    approval_id = data["approval_request_id"]
    assert approval_id is not None

    # 3. Retrieve workflow status -> 200 OK
    res_get = client.get(
        f"/api/v1/properties/{prop_a.property_id}/workflows/{wf_id}",
        headers=mgr_headers,
    )
    assert res_get.status_code == 200
    assert res_get.json()["workflow_id"] == wf_id

    # 4. Cross-property attempt on Property B -> 403 Forbidden
    res_cross = client.get(
        f"/api/v1/properties/{prop_b.property_id}/workflows/{wf_id}",
        headers=mgr_headers,
    )
    assert res_cross.status_code == 403

    # 5. List step history -> 200 OK
    res_steps = client.get(
        f"/api/v1/properties/{prop_a.property_id}/workflows/{wf_id}/steps",
        headers=mgr_headers,
    )
    assert res_steps.status_code == 200
    steps = res_steps.json()
    assert len(steps) >= 3

    # 6. Approve approval request via approval engine
    workflow_engine.decide(
        ApprovalDecision(
            approval_id=approval_id,
            approver_id=manager.user_id,
            decision="approve",
            reason="Approved invoice via API test.",
        )
    )

    # 7. Resume workflow -> 200 OK
    res_resume = client.post(
        f"/api/v1/properties/{prop_a.property_id}/workflows/{wf_id}/resume",
        headers=mgr_headers,
        json={"reason": "Approved and resuming."},
    )
    assert res_resume.status_code == 200
    assert res_resume.json()["status"] == "completed"
    assert res_resume.json()["action_executed"] is True
    assert res_resume.json()["outcome_verified"] is True


def test_maintenance_lifecycle_workflow_pre_approved(orchestrator_env):
    """Test standard low-cost maintenance request executes end-to-end without pausing for approval."""
    orchestrator = orchestrator_env["orchestrator"]
    domain_repo = orchestrator_env["domain_repo"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]

    # Seed vendor and unit
    vendor = domain_repo.create_vendor(
        prop_a.property_id,
        main.VendorCreate(name="Ace Plumbing Solutions", category=main.VendorCategory.PLUMBING),
    )

    req = WorkflowCreateRequest(
        workflow_type=WorkflowType.MAINTENANCE_LIFECYCLE,
        input_reference="Kitchen sink faucet dripping slowly in unit 101",
        metadata={
            "unit_id": "unit-101",
            "estimated_cost": 150.00,
            "title": "Kitchen Faucet Drip",
        },
    )

    created = orchestrator.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=req,
    )
    assert created.status == WorkflowStatus.CREATED

    # Run workflow
    final_state = orchestrator.run_workflow(prop_a.property_id, created.workflow_id)
    assert final_state.status == WorkflowStatus.COMPLETED
    assert final_state.maintenance_result is not None
    assert "triage" in final_state.maintenance_result
    assert "dispatch" in final_state.maintenance_result

    # Triage and dispatch validation
    triage = final_state.maintenance_result["triage"]
    assert triage["category"] == "plumbing"
    assert triage["priority"] == "low"

    dispatch = final_state.maintenance_result["dispatch"]
    wo_id = dispatch["work_order_id"]
    assigned_v = dispatch["assigned_vendor"]
    assert assigned_v["vendor_id"] == vendor.vendor_id

    # Verify action execution & outcome
    assert final_state.action_result is not None
    assert final_state.action_result["status"] == "executed"
    assert final_state.action_result["entity_type"] == "work_order"
    assert final_state.action_result["entity_id"] == wo_id
    assert final_state.verification_result["verified"] is True

    # Check work order status in domain DB
    updated_wo = domain_repo.get_work_order(prop_a.property_id, wo_id)
    assert updated_wo is not None
    assert updated_wo.status == main.WorkOrderStatus.IN_PROGRESS


def test_maintenance_lifecycle_workflow_emergency_approval_and_resumption(orchestrator_env):
    """Test emergency high-cost maintenance request pauses for manager approval, then completes on resume."""
    orchestrator = orchestrator_env["orchestrator"]
    domain_repo = orchestrator_env["domain_repo"]
    workflow_engine = orchestrator_env["workflow"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]

    # Seed plumbing vendor
    vendor = domain_repo.create_vendor(
        prop_a.property_id,
        main.VendorCreate(name="Emergency Rooter Pro", category=main.VendorCategory.PLUMBING),
    )

    req = WorkflowCreateRequest(
        workflow_type=WorkflowType.MAINTENANCE_LIFECYCLE,
        input_reference="Burst pipe under slab active flood pouring into basement",
        metadata={
            "unit_id": "unit-001",
            "estimated_cost": 4200.00,
            "title": "Severe Pipe Burst Emergency",
        },
    )

    created = orchestrator.create_workflow(
        property_id=prop_a.property_id,
        initiated_by=manager.user_id,
        request=req,
    )

    # Initial run should pause at approval gate due to emergency & high cost
    paused_state = orchestrator.run_workflow(prop_a.property_id, created.workflow_id)
    assert paused_state.status == WorkflowStatus.AWAITING_APPROVAL
    assert paused_state.approval_request_id is not None
    assert paused_state.maintenance_result["triage"]["is_emergency"] is True

    # Manager approves the emergency repair expense
    workflow_engine.decide(
        ApprovalDecision(
            approval_id=paused_state.approval_request_id,
            approver_id=manager.user_id,
            decision="approve",
            reason="Emergency slab leak approved for immediate dispatch.",
        )
    )

    # Resume workflow
    resumed_state = orchestrator.resume_workflow(
        property_id=prop_a.property_id,
        workflow_id=created.workflow_id,
        resume_request=WorkflowResumeRequest(reason="Manager approval granted."),
    )

    assert resumed_state.status == WorkflowStatus.COMPLETED
    assert resumed_state.action_result["status"] == "executed"
    assert resumed_state.verification_result["verified"] is True

    # Verify expense was created and linked
    expense_id = resumed_state.action_result["expense_id"]
    assert expense_id is not None
    expense = domain_repo.get_expense(prop_a.property_id, expense_id)
    assert expense is not None
    assert str(expense.amount) == "4200.00"
    assert expense.vendor_id == vendor.vendor_id


def test_maintenance_lifecycle_workflow_api_e2e(orchestrator_env):
    """Test maintenance lifecycle workflow execution via REST API."""
    client = orchestrator_env["client"]
    prop_a = orchestrator_env["prop_a"]
    manager = orchestrator_env["manager"]

    headers = make_auth_headers(manager.user_id, manager.email)

    res = client.post(
        f"/api/v1/properties/{prop_a.property_id}/workflows",
        headers=headers,
        json={
            "workflow_type": "maintenance_lifecycle",
            "input_reference": "Heater broken and no heat inside freezing winter weather",
            "metadata": {
                "unit_id": "unit-304",
                "estimated_cost": 300.00,
                "approval_threshold": 5000.00,
            },
        },
    )
    assert res.status_code == 201
    data = res.json()
    wf_id = data["workflow_id"]
    assert data["workflow_type"] == "maintenance_lifecycle"

    # Fetch steps
    steps_res = client.get(
        f"/api/v1/properties/{prop_a.property_id}/workflows/{wf_id}/steps",
        headers=headers,
    )
    assert steps_res.status_code == 200
    step_records = steps_res.json()
    step_names = [s["step_name"] for s in step_records]
    assert "maintenance_triage" in step_names
    assert "maintenance_dispatch" in step_names

