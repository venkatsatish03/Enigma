
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
import uuid

from fastapi import Body, Cookie, Depends, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse

from agents.document_agent import DocumentIntelligenceAgent, DocumentAgentResult


from pydantic import BaseModel, Field, field_validator

from schemas.approvals import (
    ApprovalDecision,
    ApprovalDecisionResult,
    ApprovalRequest,
)
from schemas.audit import (
    AuditEvent,
    AuditVerificationResult,
)
from schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    Permission,
    ROLE_PERMISSIONS,
    TokenResponse,
    User,
    UserMembershipInfo,
    UserRole,
)
from repository import (
    CrossPropertyReferenceError,
    DomainRepository,
    EntityNotFoundError,
    IntegrityConflictError,
    InvalidOperationError,
)
from repository.owner_listing_repository import OwnerListingRepository
from schemas.booking_request import (
    BookingRequestCreate,
    BookingRequestDecision,
    BookingRequestRecord,
    OwnerListingAvailabilityUpdate,
)
from schemas.domain import (
    BankStatementRecord,
    BankTransactionMatchStatus,
    BankTransactionRecord,
    ComplianceAssignmentRequest,
    ComplianceCategory,
    ComplianceFindingRecord,
    ComplianceResolutionRequest,
    ComplianceSeverity,
    ComplianceStatus,
    DocumentMetadataCreate,
    DocumentMetadataRecord,
    ExpenseApprovalDecision,
    ExpenseApprovalStatus,
    ExpenseCategory,
    ExpenseCategorySummary,
    ExpenseCreate,
    ExpenseRecord,
    ExpenseStatus,
    ExpenseUpdate,
    FinancialSummaryReport,
    InvoiceCreate,
    InvoiceRecord,
    InvoiceStatus,
    InvoiceType,
    InvoiceUpdate,
    LeaseCreate,
    LeaseRecord,
    LeaseStatus,
    LeaseUpdate,
    MatchDecisionRequest,
    NotificationDeliveryMode,
    NotificationPreviewRequest,
    NotificationPreviewResponse,
    NotificationRecord,
    NotificationSendRequest,
    NotificationStatus,
    NotificationType,
    OwnerStatementReport,
    PaymentCreate,
    PaymentMethod,
    PaymentRecord,
    PaymentStatus,
    PaymentUpdate,
    PropertyRecord,
    PropertyUpdate,
    ResidentCreate,
    ResidentRecord,
    ResidentStatus,
    ResidentUpdate,
    UnitCreate,
    UnitRecord,
    UnitStatus,
    UnitUpdate,
    VendorCategory,
    VendorCreate,
    VendorRecord,
    VendorUpdate,
    WorkOrderCreate,
    WorkOrderPriority,
    WorkOrderRecord,
    WorkOrderStatus,
    WorkOrderUpdate,
    MaintenanceRequestCreate,
    MaintenanceRequestRecord,
    MaintenanceRequestStatus,
    MaintenanceRequestUpdate,
    SlaEscalationCreate,
    SlaEscalationRecord,
    SlaEscalationStatus,
    SlaEscalationType,
    SlaEscalationUpdate,
    SlaMonitoringResult,
)
from orchestration import (
    ConcurrentExecutionError,
    MasterOrchestrator,
    PolicyValidationError,
    SqliteCheckpointStore,
    StepExecutionError,
    StepName,
    WorkflowCancelRequest,
    WorkflowCreateRequest,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowResumeRequest,
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
    WorkflowStepRecord,
    WorkflowSummary,
    WorkflowType,
)
from tools.approval_workflow import (
    ApprovalAuthorizationError,
    ApprovalNotFoundError,
    ApprovalStateError,
    ApprovalWorkflow,
)
from tools.audit_trail import AuditTrail
from tools.auth_config import AuthConfig, get_auth_config
from tools.auth_crypto import create_access_token, hash_password, verify_password
from tools.auth_email import AuthEmailConfigurationError, send_account_link
from tools.auth_dependencies import (
    PropertyContext,
    get_auth_repository,
    get_checkpoint_store,
    get_current_user,
    get_domain_repository,
    require_property_permission,
)
from tools.auth_repository import AuthRepository
from tools.google_oauth import (
    GoogleOAuthError,
    build_google_authorization_url,
    create_pkce_pair,
    exchange_google_code,
    verify_google_id_token,
)
from schemas.owner_listing import (
    ListingMediaRecord,
    OwnerListingDraft,
    OwnerListingRecord,
    PropertyStatus,
    PublicOwnerListingRecord,
)
from tools.financial_operations_orchestrator import (
    run_financial_operations,
)

from agents.resident_lease_agent import ResidentLeaseAgent
from agents.maintenance_agent import MaintenanceAgent
from agents.sla_monitoring_agent import SLAMonitoringAgent
from schemas.audit import AuditEventCreate
from schemas.resident_lease_agent import (
    ResidentLeaseAnalysisRequest,
    ResidentLeaseAnalysisResult,
)
from schemas.maintenance_agent import (
    AssetCreate,
    AssetRecord,
    AutoDispatchWorkOrderRequest,
    AutoDispatchWorkOrderResult,
    InspectionAnalysisResult,
    InspectionReportCreate,
    MaintenanceCostAnalysisResult,
    MaintenanceOperationsAnalysisRequest,
    MaintenanceOperationsAnalysisResult,
    MaintenanceTriageRequest,
    MaintenanceTriageResult,
    PreventiveTaskCreate,
    PreventiveTaskRecord,
)


from schemas.payment_allocation import PaymentAllocationInstruction
from schemas.financial_operations import FinancialOperationsResult

from services.reconciliation_service import (
    ReconciliationService,
    StatementValidationError,
)
from services.compliance_service import ComplianceService
from services.notification_service import NotificationService
from services.financial_reporting_service import FinancialReportingService

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_AUDIT_DB = DATA_DIR / "audit.sqlite3"
DEFAULT_APPROVALS_DB = DATA_DIR / "approvals.sqlite3"
DEFAULT_DOMAIN_DB = DATA_DIR / "domain.sqlite3"
DEFAULT_WORKFLOWS_DB = DATA_DIR / "workflows.sqlite3"
logger = logging.getLogger("uvicorn.error")


def get_audit_trail() -> AuditTrail:
    return AuditTrail(DEFAULT_AUDIT_DB)


def get_approval_workflow(
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> ApprovalWorkflow:
    return ApprovalWorkflow(DEFAULT_APPROVALS_DB, audit_trail)


def get_master_orchestrator(
    checkpoint_store: SqliteCheckpointStore = Depends(get_checkpoint_store),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    approval_workflow: ApprovalWorkflow = Depends(get_approval_workflow),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> MasterOrchestrator:
    return MasterOrchestrator(
        checkpoint_store=checkpoint_store,
        domain_repo=domain_repo,
        approval_workflow=approval_workflow,
        audit_trail=audit_trail,
    )


def get_reconciliation_service(
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> ReconciliationService:
    return ReconciliationService(domain_repo)


def get_compliance_service(
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> ComplianceService:
    return ComplianceService(domain_repo)


def get_notification_service(
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> NotificationService:
    return NotificationService(domain_repo)


def get_financial_reporting_service(
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> FinancialReportingService:
    return FinancialReportingService(domain_repo)


def get_owner_listing_repository(
    config: AuthConfig = Depends(get_auth_config),
) -> OwnerListingRepository:
    return OwnerListingRepository(config.owner_listings_db_path)



app = FastAPI(
    title="Enigma API",
    description="Document intelligence and financial operations API with property-level authorization",
    version="0.2.0",
)

document_agent = DocumentIntelligenceAgent()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "enigma-api"}


class GoogleExchangeRequest(BaseModel):
    code: str = Field(min_length=24, max_length=256)


class AccountActionRequest(BaseModel):
    token: str = Field(min_length=24, max_length=256)
    password: str = Field(min_length=12, max_length=256)


class EmailAddressRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid email address.")
        return value


class TenantMaintenanceRequestPayload(BaseModel):
    property_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    description: str = Field(min_length=3, max_length=5000)


def _issue_token_for_user(user: User, config: AuthConfig) -> TokenResponse:
    token, expires_in = create_access_token(
        user_id=user.user_id,
        email=user.email,
        secret_key=config.secret_key,
        expires_delta=timedelta(minutes=config.access_token_expire_minutes),
        algorithm=config.algorithm,
        issuer=config.issuer,
        audience=config.audience,
    )
    return TokenResponse(access_token=token, expires_in=expires_in)


def _link_verified_residents(user: User, email: str, auth_repo: AuthRepository, domain_repo: DomainRepository) -> None:
    """Link only an unambiguous exact-email resident record in each property."""
    matches_by_property: dict[str, list[str]] = {}
    for property_id, resident_id in domain_repo.find_residents_by_email(email):
        matches_by_property.setdefault(property_id, []).append(resident_id)
    for property_id, resident_ids in matches_by_property.items():
        if len(resident_ids) == 1:
            auth_repo.link_resident(user_id=user.user_id, property_id=property_id, resident_id=resident_ids[0])


def _google_callback_redirect(config: AuthConfig, path: str) -> RedirectResponse:
    response = RedirectResponse(f"{config.frontend_base_url}{path}", status_code=303)
    response.delete_cookie(
        "enigma_oauth_state",
        path="/api/backend/api/v1/auth/google/callback",
        httponly=True,
        secure=config.frontend_base_url.startswith("https://"),
        samesite="lax",
    )
    return response


@app.get("/api/v1/auth/google/start")
def start_google_auth(
    intent: Literal["login", "signup"] = Query("login"),
    config: AuthConfig = Depends(get_auth_config),
    auth_repo: AuthRepository = Depends(get_auth_repository),
) -> RedirectResponse:
    """Start a Google OIDC authorization-code flow with state, nonce, and PKCE."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = create_pkce_pair()
    try:
        authorization_url = build_google_authorization_url(
            config, state=state, nonce=nonce, code_challenge=challenge, intent=intent
        )
    except GoogleOAuthError:
        return RedirectResponse(f"{config.frontend_base_url}/login?auth_error=google_not_configured", status_code=303)
    auth_repo.create_oauth_state(state=state, nonce=nonce, code_verifier=verifier, intent=intent)
    response = RedirectResponse(authorization_url, status_code=302)
    response.set_cookie(
        "enigma_oauth_state",
        state,
        max_age=600,
        httponly=True,
        secure=config.frontend_base_url.startswith("https://"),
        samesite="lax",
        path="/api/backend/api/v1/auth/google/callback",
    )
    return response


@app.get("/api/v1/auth/google/callback")
def google_auth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    oauth_state_cookie: str | None = Cookie(None, alias="enigma_oauth_state"),
    config: AuthConfig = Depends(get_auth_config),
    auth_repo: AuthRepository = Depends(get_auth_repository),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> RedirectResponse:
    """Verify Google's response and return a short-lived, one-use app handoff."""
    if error or not code or not state:
        return _google_callback_redirect(config, "/login?auth_error=google_cancelled")
    if not oauth_state_cookie or not secrets.compare_digest(state, oauth_state_cookie):
        return _google_callback_redirect(config, "/login?auth_error=google_state")
    state_data = auth_repo.consume_oauth_state(state)
    if not state_data:
        return _google_callback_redirect(config, "/login?auth_error=google_state")

    try:
        id_token = exchange_google_code(config, code=code, code_verifier=state_data["code_verifier"])
        claims = verify_google_id_token(config, id_token=id_token, expected_nonce=state_data["nonce"])
        email = str(claims["email"]).strip().lower()
        google_sub = str(claims["sub"])

        user = auth_repo.get_user_by_google_sub(google_sub)
        if user is None:
            existing = auth_repo.get_user_by_email(email)
            if existing:
                user = existing[0]
                if not auth_repo.link_google_identity(user_id=user.user_id, google_sub=google_sub):
                    raise GoogleOAuthError("This Google account is already linked to a different ENIGMA account.")
                user = auth_repo.get_user_by_id(user.user_id) or user
            else:
                # The random hash is never disclosed; a verified Google user must set their own password.
                user = auth_repo.create_google_user(
                    email=email,
                    google_sub=google_sub,
                    password_hash=hash_password(secrets.token_urlsafe(48)),
                )
        if not user.is_active:
            return _google_callback_redirect(config, "/login?auth_error=account_disabled")
        _link_verified_residents(user, email, auth_repo, domain_repo)
        if user.password_setup_required:
            setup_token = auth_repo.create_action_token(
                user_id=user.user_id, purpose="password_setup", lifetime=timedelta(minutes=30)
            )
            return _google_callback_redirect(config, f"/set-password#token={setup_token}")

        handoff = auth_repo.create_exchange_code(user_id=user.user_id)
        return _google_callback_redirect(config, f"/auth/google/callback#code={handoff}")
    except GoogleOAuthError as exc:
        cause = exc.__cause__
        if cause is not None:
            logger.warning(
                "Google sign-in rejected: %s (cause: %s: %s)",
                str(exc),
                type(cause).__name__,
                str(cause),
            )
        else:
            logger.warning("Google sign-in rejected: %s", str(exc))
        return _google_callback_redirect(config, "/login?auth_error=google_verification")
    except Exception:
        logger.exception("Google sign-in callback failed")
        return _google_callback_redirect(config, "/login?auth_error=google_failed")


@app.post("/api/v1/auth/google/exchange", response_model=TokenResponse)
def exchange_google_handoff(
    payload: GoogleExchangeRequest,
    config: AuthConfig = Depends(get_auth_config),
    auth_repo: AuthRepository = Depends(get_auth_repository),
) -> TokenResponse:
    user_id = auth_repo.consume_exchange_code(payload.code)
    user = auth_repo.get_user_by_id(user_id) if user_id else None
    if not user or not user.is_active or user.password_setup_required:
        raise HTTPException(status_code=401, detail="Google sign-in could not be completed. Please try again.")
    return _issue_token_for_user(user, config)


@app.post("/api/v1/auth/password/setup", response_model=TokenResponse)
def set_initial_password(
    payload: AccountActionRequest,
    config: AuthConfig = Depends(get_auth_config),
    auth_repo: AuthRepository = Depends(get_auth_repository),
) -> TokenResponse:
    user_id = auth_repo.consume_action_token(raw_token=payload.token, purpose="password_setup")
    user = auth_repo.get_user_by_id(user_id) if user_id else None
    if not user or not user.is_active or not user.password_setup_required:
        raise HTTPException(status_code=400, detail="This password setup link is invalid or expired. Sign in with Google again.")
    auth_repo.update_user_password(user.user_id, hash_password(payload.password))
    updated = auth_repo.get_user_by_id(user.user_id) or user
    return _issue_token_for_user(updated, config)


@app.post("/api/v1/auth/password/forgot", status_code=202)
def request_password_reset(
    payload: EmailAddressRequest,
    config: AuthConfig = Depends(get_auth_config),
    auth_repo: AuthRepository = Depends(get_auth_repository),
) -> dict[str, str]:
    user_record = auth_repo.get_user_by_email(payload.email)
    if user_record and user_record[0].is_active:
        user = user_record[0]
        raw_token = auth_repo.create_action_token(
            user_id=user.user_id, purpose="password_reset", lifetime=timedelta(minutes=30)
        )
        link = f"{config.frontend_base_url}/reset-password#token={raw_token}"
        try:
            send_account_link(
                recipient=user.email,
                subject="Reset your ENIGMA password",
                heading="Password reset",
                link=link,
                action="Reset password",
            )
        except AuthEmailConfigurationError as exc:
            logger.warning("Password reset email is unavailable because SMTP is not configured: %s", exc)
        except Exception:
            logger.exception("Password reset email delivery failed")
    return {"message": "If an active account matches that email, password reset instructions will be sent."}


@app.post("/api/v1/auth/password/reset", status_code=204)
def reset_password(
    payload: AccountActionRequest,
    auth_repo: AuthRepository = Depends(get_auth_repository),
) -> Response:
    user_id = auth_repo.consume_action_token(raw_token=payload.token, purpose="password_reset")
    if not user_id:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or expired.")
    auth_repo.update_user_password(user_id, hash_password(payload.password))
    return Response(status_code=204)


@app.post(
    "/api/v1/auth/login",
    response_model=TokenResponse,
)
def login(
    credentials: LoginRequest,
    config: AuthConfig = Depends(get_auth_config),
    auth_repo: AuthRepository = Depends(get_auth_repository),
) -> TokenResponse:
    """Issue signed JWT access token upon successful credential verification."""
    user_res = auth_repo.get_user_by_email(credentials.email)
    if not user_res:
        # Generic error message to prevent account enumeration
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user, pwd_hash = user_res
    if user.password_setup_required:
        raise HTTPException(
            status_code=401,
            detail="Create your ENIGMA password from the setup link sent after Google signup.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not verify_password(credentials.password, pwd_hash):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="User account is disabled.",
        )

    token, expires_in = create_access_token(
        user_id=user.user_id,
        email=user.email,
        secret_key=config.secret_key,
        expires_delta=timedelta(minutes=config.access_token_expire_minutes),
        algorithm=config.algorithm,
        issuer=config.issuer,
        audience=config.audience,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
    )


@app.get(
    "/api/v1/auth/me",
    response_model=CurrentUserResponse,
)
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    auth_repo: AuthRepository = Depends(get_auth_repository),
    owner_listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> CurrentUserResponse:
    """Return authenticated caller identity and authorized property roles."""
    memberships = auth_repo.list_memberships_for_user(current_user.user_id)
    membership_infos: list[UserMembershipInfo] = []

    for m in memberships:
        prop = auth_repo.get_property(m.property_id)
        perms = [p.value for p in ROLE_PERMISSIONS.get(m.role, frozenset())]
        membership_infos.append(
            UserMembershipInfo(
                property_id=m.property_id,
                property_name=prop.name if prop else None,
                role=m.role.value,
                permissions=perms,
            )
        )

    return CurrentUserResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        is_active=current_user.is_active,
        is_superuser=current_user.is_superuser,
        memberships=membership_infos,
        portal_roles=["tenant", "owner"] if owner_listings.list_for_owner(current_user.user_id) else ["tenant"],
    )


@app.get("/api/v1/portal/tenant/dashboard")
def get_tenant_dashboard(
    current_user: User = Depends(get_current_user),
    auth_repo: AuthRepository = Depends(get_auth_repository),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> dict[str, object]:
    """Return only leases, charges, payments, and requests linked to this account."""
    properties: list[dict[str, object]] = []
    request_ids: set[str] = set()
    links = auth_repo.list_resident_links(current_user.user_id)
    for property_id, resident_id in links:
        resident = domain_repo.get_resident(property_id, resident_id)
        if not resident:
            continue
        property_record = domain_repo.get_property(property_id)
        leases = domain_repo.list_leases(property_id, resident_id=resident_id, limit=1000)
        invoices = domain_repo.list_invoices(property_id, resident_id=resident_id, limit=1000)
        payments = domain_repo.list_payments(property_id, resident_id=resident_id, limit=1000)
        units = []
        maintenance_requests = []
        for lease in leases:
            unit = domain_repo.get_unit(property_id, lease.unit_id)
            if unit and all(existing.unit_id != unit.unit_id for existing in units):
                units.append(unit)
            if unit:
                for request in domain_repo.list_maintenance_requests(property_id, unit_id=unit.unit_id, limit=1000):
                    if request.resident_id == resident_id and request.request_id not in request_ids:
                        request_ids.add(request.request_id)
                        maintenance_requests.append(request)
        properties.append(
            {
                "property_id": property_id,
                "property_name": property_record.name if property_record else property_id,
                "resident": resident,
                "leases": leases,
                "units": units,
                "invoices": invoices,
                "payments": payments,
                "maintenance_requests": maintenance_requests,
            }
        )
    return {"properties": properties, "has_linked_resident": bool(properties)}


@app.post("/api/v1/portal/tenant/maintenance-requests", status_code=201)
def create_tenant_maintenance_request(
    payload: TenantMaintenanceRequestPayload,
    current_user: User = Depends(get_current_user),
    auth_repo: AuthRepository = Depends(get_auth_repository),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> object:
    """A tenant may submit a request only for a unit in their active lease."""
    resident_ids = [
        resident_id
        for property_id, resident_id in auth_repo.list_resident_links(current_user.user_id)
        if property_id == payload.property_id
    ]
    if not resident_ids:
        raise HTTPException(status_code=403, detail="Your account is not linked to a resident record at this property.")
    for resident_id in resident_ids:
        active_leases = domain_repo.list_leases(
            payload.property_id,
            status=LeaseStatus.ACTIVE,
            resident_id=resident_id,
            limit=1000,
        )
        if any(lease.unit_id == payload.unit_id for lease in active_leases):
            try:
                return domain_repo.create_maintenance_request(
                    payload.property_id,
                    MaintenanceRequestCreate(
                        unit_id=payload.unit_id,
                        resident_id=resident_id,
                        description=payload.description,
                    ),
                )
            except (EntityNotFoundError, CrossPropertyReferenceError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=403, detail="The selected unit is not part of your active lease.")


@app.get("/api/v1/portal/tenant/listings", response_model=list[PublicOwnerListingRecord])
def list_available_tenant_listings(
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> list[PublicOwnerListingRecord]:
    """List approved properties that are currently available to authenticated tenants."""
    return [
        PublicOwnerListingRecord(
            listing_id=listing.listing_id,
            status=listing.status,
            data=listing.data,
            media=listing.media,
            created_at=listing.created_at,
            updated_at=listing.updated_at,
        )
        for listing in listings.list_published()
    ]


@app.get("/api/v1/portal/tenant/listings/{listing_id}/media/{media_id}")
def get_tenant_listing_media(
    listing_id: str,
    media_id: str,
    current_user: User = Depends(get_current_user),
    config: AuthConfig = Depends(get_auth_config),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> FileResponse:
    item = listings.get_published_media(listing_id, media_id)
    if not item:
        raise HTTPException(status_code=404, detail="Listing media is not available.")
    root = config.listing_media_path.resolve()
    file_path = (root / str(item["storage_name"])).resolve()
    if not file_path.is_relative_to(root) or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Listing media file is not available.")
    return FileResponse(file_path, media_type=str(item["mime_type"]), filename=str(item["original_filename"]))


@app.get("/api/v1/portal/tenant/booking-requests", response_model=list[BookingRequestRecord])
def list_tenant_booking_requests(
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> list[BookingRequestRecord]:
    return listings.list_booking_requests_for_tenant(current_user.user_id)


@app.post("/api/v1/portal/tenant/booking-requests", response_model=BookingRequestRecord, status_code=201)
def create_tenant_booking_request(
    payload: BookingRequestCreate,
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> BookingRequestRecord:
    request, error = listings.create_booking_request(
        tenant_user_id=current_user.user_id,
        tenant_email=current_user.email,
        listing_id=payload.listing_id,
        desired_move_in_date=payload.desired_move_in_date,
        message=payload.message,
    )
    if error == "listing_unavailable":
        raise HTTPException(status_code=409, detail="This property is no longer available for booking requests.")
    if error == "request_already_pending":
        raise HTTPException(status_code=409, detail="You already have a pending request for this property.")
    if error == "cannot_request_own_listing":
        raise HTTPException(status_code=403, detail="You cannot submit a booking request for your own property.")
    if request is None:
        raise HTTPException(status_code=400, detail="The booking request could not be created.")
    return request


@app.get("/api/v1/portal/owner/booking-requests", response_model=list[BookingRequestRecord])
def list_owner_booking_requests(
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> list[BookingRequestRecord]:
    return listings.list_booking_requests_for_owner(current_user.user_id)


@app.patch("/api/v1/portal/owner/booking-requests/{request_id}", response_model=BookingRequestRecord)
def decide_owner_booking_request(
    request_id: str,
    payload: BookingRequestDecision,
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> BookingRequestRecord:
    request, error = listings.decide_booking_request(
        owner_user_id=current_user.user_id,
        request_id=request_id,
        status=payload.status,
        decision_note=payload.decision_note,
    )
    if error == "not_found" or request is None:
        raise HTTPException(status_code=404, detail="Booking request not found for your properties.")
    if error == "already_decided":
        raise HTTPException(status_code=409, detail="This booking request has already been decided.")
    if error == "listing_unavailable":
        raise HTTPException(status_code=409, detail="This property is no longer available.")
    return request


@app.get("/api/v1/portal/owner/listings", response_model=list[OwnerListingRecord])
def list_owner_listings(
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> list[OwnerListingRecord]:
    return listings.list_for_owner(current_user.user_id)


@app.patch("/api/v1/portal/owner/listings/{listing_id}/availability", response_model=OwnerListingRecord)
def update_owner_listing_availability(
    listing_id: str,
    payload: OwnerListingAvailabilityUpdate,
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> OwnerListingRecord:
    listing = listings.update_property_status(
        current_user.user_id,
        listing_id,
        PropertyStatus(payload.property_status),
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Published property not found for your account.")
    return listing


@app.post("/api/v1/portal/owner/listings", response_model=OwnerListingRecord, status_code=201)
def create_owner_listing(
    payload: OwnerListingDraft,
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> OwnerListingRecord:
    return listings.create(current_user.user_id, payload)


@app.get("/api/v1/portal/owner/listings/{listing_id}", response_model=OwnerListingRecord)
def get_owner_listing(
    listing_id: str,
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> OwnerListingRecord:
    listing = listings.get(current_user.user_id, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found.")
    return listing


@app.put("/api/v1/portal/owner/listings/{listing_id}", response_model=OwnerListingRecord)
def update_owner_listing(
    listing_id: str,
    payload: OwnerListingDraft,
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> OwnerListingRecord:
    listing = listings.update(current_user.user_id, listing_id, payload)
    if not listing:
        raise HTTPException(status_code=404, detail="Draft listing not found or it is no longer editable.")
    return listing


@app.post("/api/v1/portal/owner/listings/{listing_id}/submit", response_model=OwnerListingRecord)
def submit_owner_listing(
    listing_id: str,
    current_user: User = Depends(get_current_user),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> OwnerListingRecord:
    listing, errors = listings.submit(current_user.user_id, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found.")
    if errors:
        raise HTTPException(status_code=422, detail={"message": "Complete the required listing details before submitting.", "errors": errors})
    return listing


@app.post("/api/v1/portal/owner/listings/{listing_id}/media", response_model=ListingMediaRecord, status_code=201)
async def upload_owner_listing_media(
    listing_id: str,
    file: UploadFile = File(...),
    media_kind: str = Query("front_exterior"),
    current_user: User = Depends(get_current_user),
    config: AuthConfig = Depends(get_auth_config),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> ListingMediaRecord:
    listing = listings.get(current_user.user_id, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found.")
    allowed_kinds = {
        "front_exterior", "entrance", "living_room", "common_area", "kitchen", "bathroom",
        "bedroom", "garden", "balcony", "parking", "shared_amenity", "floor_plan", "video_walkthrough",
    }
    if media_kind not in allowed_kinds:
        raise HTTPException(status_code=422, detail="Unsupported media category.")
    mime = (file.content_type or "").lower()
    if media_kind == "video_walkthrough":
        allowed_mimes = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}
        maximum_bytes = 50 * 1024 * 1024
    elif media_kind == "floor_plan":
        allowed_mimes = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}
        maximum_bytes = 15 * 1024 * 1024
    else:
        allowed_mimes = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
        maximum_bytes = 15 * 1024 * 1024
    if mime not in allowed_mimes:
        raise HTTPException(status_code=415, detail="Upload a supported image, floor plan PDF, or video file.")
    content = await file.read(maximum_bytes + 1)
    if len(content) > maximum_bytes:
        raise HTTPException(status_code=413, detail="The uploaded file exceeds the size limit for this media type.")

    media_id = str(uuid.uuid4())
    storage_name = f"{media_id}{allowed_mimes[mime]}"
    directory = (config.listing_media_path / listing_id).resolve()
    root = config.listing_media_path.resolve()
    if not directory.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Invalid media destination.")
    directory.mkdir(parents=True, exist_ok=True)
    stored_path = directory / storage_name
    stored_path.write_bytes(content)
    record = listings.add_media(
        owner_user_id=current_user.user_id,
        listing_id=listing_id,
        original_filename=Path(file.filename or "upload").name[:255],
        storage_name=f"{listing_id}/{storage_name}",
        media_kind=media_kind,
        mime_type=mime,
        size_bytes=len(content),
    )
    if not record:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="Listing not found.")
    return record


@app.get("/api/v1/portal/owner/listings/{listing_id}/media/{media_id}")
def get_owner_listing_media(
    listing_id: str,
    media_id: str,
    current_user: User = Depends(get_current_user),
    config: AuthConfig = Depends(get_auth_config),
    listings: OwnerListingRepository = Depends(get_owner_listing_repository),
) -> FileResponse:
    item = listings.get_media(current_user.user_id, listing_id, media_id)
    if not item:
        raise HTTPException(status_code=404, detail="Listing media not found.")
    root = config.listing_media_path.resolve()
    file_path = (root / str(item["storage_name"])).resolve()
    if not file_path.is_relative_to(root) or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Listing media file is not available.")
    return FileResponse(file_path, media_type=str(item["mime_type"]), filename=str(item["original_filename"]))


@app.post(
    "/api/v1/documents/analyze",
    response_model=DocumentAgentResult,
)
async def analyze_document(
    file: UploadFile = File(...),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.DOCUMENTS_ANALYZE)
    ),
) -> DocumentAgentResult:
    filename = file.filename or ""

    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=415,
            detail="Only PDF files are supported.",
        )

    # Stage the uploaded file in a temporary directory.
    # The existing document agent continues to receive a filesystem path.
    with TemporaryDirectory(prefix="enigma_upload_") as temp_dir:
        staged_path = Path(temp_dir) / "upload.pdf"
        total_bytes = 0

        try:
            with staged_path.open("wb") as destination:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break

                    total_bytes += len(chunk)
                    if total_bytes > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="PDF exceeds the 20 MB upload limit.",
                        )

                    destination.write(chunk)

            if total_bytes == 0:
                raise HTTPException(
                    status_code=400,
                    detail="The uploaded file is empty.",
                )

            # A .pdf filename alone does not prove the content is a PDF.
            with staged_path.open("rb") as source:
                if source.read(5) != b"%PDF-":
                    raise HTTPException(
                        status_code=400,
                        detail="The uploaded file does not have a valid PDF header.",
                    )

            result = await run_in_threadpool(
                document_agent.process,
                staged_path,
            )
            return result

        except HTTPException:
            raise
        except Exception as exc:
            # Keep internal paths and stack traces out of API responses.
            raise HTTPException(
                status_code=500,
                detail="Document processing failed.",
            ) from exc
        finally:
            await file.close()


class FinancialOperationsRequest(BaseModel):
    document_result: dict = Field(
        ...,
        description="Serialized DocumentAgentResult returned by document analysis.",
    )
    allocation_instructions: list[PaymentAllocationInstruction] | None = None
    property_id: str | None = None


@app.post(
    "/api/v1/financial-operations",
    response_model=FinancialOperationsResult,
)
def financial_operations(
    request: FinancialOperationsRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
) -> FinancialOperationsResult:
    try:
        return run_financial_operations(
            document_agent_output=request.document_result,
            allocation_instructions=request.allocation_instructions,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Financial operations could not process the supplied document result.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Financial operations failed.",
        ) from exc


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1)
    approver_id: str | None = None
    approval_id: str | None = None


@app.get(
    "/api/v1/approvals",
    response_model=list[ApprovalRequest],
)
def list_approvals(
    status: str | None = Query(None, description="Filter approvals by status"),
    action_id: str | None = Query(None, description="Filter approvals by action ID"),
    limit: int = Query(default=100, ge=1, le=10000, description="Maximum items to return"),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.APPROVALS_READ)
    ),
    workflow: ApprovalWorkflow = Depends(get_approval_workflow),
) -> list[ApprovalRequest]:
    try:
        return workflow.list_requests(
            status=status,
            action_id=action_id,
            property_id=property_ctx.property.property_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to list approvals.",
        ) from exc


@app.get(
    "/api/v1/approvals/{approval_id}",
    response_model=ApprovalRequest,
)
def get_approval(
    approval_id: str,
    current_user: User = Depends(get_current_user),
    auth_repo: AuthRepository = Depends(get_auth_repository),
    workflow: ApprovalWorkflow = Depends(get_approval_workflow),
) -> ApprovalRequest:
    try:
        approval = workflow.get_request(approval_id)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve approval request.",
        ) from exc

    if approval.property_id:
        if not current_user.is_superuser:
            membership = auth_repo.get_membership(approval.property_id, current_user.user_id)
            if not membership:
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied: Not authorized for property '{approval.property_id}'.",
                )
            perms = ROLE_PERMISSIONS.get(membership.role, frozenset())
            if Permission.APPROVALS_READ not in perms:
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied: Insufficient permission to view approvals for property '{approval.property_id}'.",
                )
    else:
        # Legacy records without an established property association
        # "Records without an established property association must not become visible to every authenticated user."
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=403,
                detail="Access denied: Unassigned legacy record is only accessible by administrators.",
            )

    return approval


@app.post(
    "/api/v1/approvals/{approval_id}/decision",
    response_model=ApprovalDecisionResult,
)
def decide_approval(
    approval_id: str,
    request: ApprovalDecisionRequest,
    current_user: User = Depends(get_current_user),
    auth_repo: AuthRepository = Depends(get_auth_repository),
    workflow: ApprovalWorkflow = Depends(get_approval_workflow),
) -> ApprovalDecisionResult:
    if request.approval_id and request.approval_id != approval_id:
        raise HTTPException(
            status_code=422,
            detail="approval_id in request body does not match path parameter",
        )

    try:
        approval = workflow.get_request(approval_id)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Property authorization check
    if approval.property_id:
        if not current_user.is_superuser:
            membership = auth_repo.get_membership(approval.property_id, current_user.user_id)
            if not membership:
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied: Not authorized for property '{approval.property_id}'.",
                )
            perms = ROLE_PERMISSIONS.get(membership.role, frozenset())
            if Permission.APPROVALS_DECIDE not in perms:
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied: Insufficient permission to decide approvals for property '{approval.property_id}'.",
                )
    else:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=403,
                detail="Access denied: Unassigned legacy record can only be decided by administrators.",
            )

    # For approval decisions, use the authenticated principal as the approver identity.
    # Reject conflicting caller-supplied identity to prevent impersonation.
    if request.approver_id and request.approver_id != current_user.user_id:
        raise HTTPException(
            status_code=403,
            detail=f"Caller identity '{current_user.user_id}' does not match requested approver_id '{request.approver_id}'.",
        )

    effective_approver_id = current_user.user_id

    try:
        decision = ApprovalDecision(
            approval_id=approval_id,
            approver_id=effective_approver_id,
            decision=request.decision,
            reason=request.reason,
        )
        return workflow.decide(decision)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ApprovalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to process approval decision.",
        ) from exc


@app.get(
    "/api/v1/audit/events",
    response_model=list[AuditEvent],
)
def list_audit_events(
    entity_type: str | None = Query(None, description="Filter by entity type"),
    entity_id: str | None = Query(None, description="Filter by entity ID"),
    correlation_id: str | None = Query(None, description="Filter by correlation ID"),
    limit: int = Query(default=100, ge=1, le=10000, description="Maximum events to return"),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.AUDIT_READ)
    ),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> list[AuditEvent]:
    try:
        return audit_trail.list_events(
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            property_id=property_ctx.property.property_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to list audit events.",
        ) from exc


@app.get(
    "/api/v1/audit/verify",
    response_model=AuditVerificationResult,
)
def verify_audit_trail(
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.AUDIT_VERIFY)
    ),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> AuditVerificationResult:
    try:
        return audit_trail.verify_chain()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to verify audit trail.",
        ) from exc


# =============================================================================
# Phase 2: Domain Entity Endpoints (Property-Scoped CRUD)
# =============================================================================

# -----------------------------------------------------------------------------
# Properties
# -----------------------------------------------------------------------------

@app.get(
    "/api/v1/properties/{property_id}",
    response_model=PropertyRecord,
)
def get_property_details(
    property_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> PropertyRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    prop = domain_repo.get_property(property_id)
    if not prop:
        raise HTTPException(status_code=404, detail=f"Property '{property_id}' not found.")
    return prop


@app.patch(
    "/api/v1/properties/{property_id}",
    response_model=PropertyRecord,
)
def update_property_details(
    property_id: str,
    payload: PropertyUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> PropertyRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.update_property(property_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# -----------------------------------------------------------------------------
# Units
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/units",
    response_model=UnitRecord,
    status_code=201,
)
def create_unit(
    property_id: str,
    payload: UnitCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> UnitRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.create_unit(property_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/units",
    response_model=list[UnitRecord],
)
def list_units(
    property_id: str,
    status: UnitStatus | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[UnitRecord]:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    return domain_repo.list_units(
        property_id=property_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/units/{unit_id}",
    response_model=UnitRecord,
)
def get_unit(
    property_id: str,
    unit_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> UnitRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    unit = domain_repo.get_unit(property_id, unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail=f"Unit '{unit_id}' not found.")
    return unit


@app.patch(
    "/api/v1/properties/{property_id}/units/{unit_id}",
    response_model=UnitRecord,
)
def update_unit(
    property_id: str,
    unit_id: str,
    payload: UnitUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> UnitRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.update_unit(property_id, unit_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# -----------------------------------------------------------------------------
# Residents
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/residents",
    response_model=ResidentRecord,
    status_code=201,
)
def create_resident(
    property_id: str,
    payload: ResidentCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> ResidentRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.create_resident(property_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/residents",
    response_model=list[ResidentRecord],
)
def list_residents(
    property_id: str,
    status: ResidentStatus | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.RESIDENT_READ)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[ResidentRecord]:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    return domain_repo.list_residents(
        property_id=property_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/residents/{resident_id}",
    response_model=ResidentRecord,
)
def get_resident(
    property_id: str,
    resident_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.RESIDENT_READ)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> ResidentRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    res = domain_repo.get_resident(property_id, resident_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Resident '{resident_id}' not found.")
    return res


@app.patch(
    "/api/v1/properties/{property_id}/residents/{resident_id}",
    response_model=ResidentRecord,
)
def update_resident(
    property_id: str,
    resident_id: str,
    payload: ResidentUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> ResidentRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.update_resident(property_id, resident_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# -----------------------------------------------------------------------------
# Leases
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/leases",
    response_model=LeaseRecord,
    status_code=201,
)
def create_lease(
    property_id: str,
    payload: LeaseCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> LeaseRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.create_lease(property_id, payload)
    except CrossPropertyReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/leases",
    response_model=list[LeaseRecord],
)
def list_leases(
    property_id: str,
    status: LeaseStatus | None = Query(None),
    resident_id: str | None = Query(None),
    unit_id: str | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[LeaseRecord]:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    return domain_repo.list_leases(
        property_id=property_id,
        status=status,
        resident_id=resident_id,
        unit_id=unit_id,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/leases/{lease_id}",
    response_model=LeaseRecord,
)
def get_lease(
    property_id: str,
    lease_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> LeaseRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    lease = domain_repo.get_lease(property_id, lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail=f"Lease '{lease_id}' not found.")
    return lease


@app.patch(
    "/api/v1/properties/{property_id}/leases/{lease_id}",
    response_model=LeaseRecord,
)
def update_lease(
    property_id: str,
    lease_id: str,
    payload: LeaseUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> LeaseRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.update_lease(property_id, lease_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# -----------------------------------------------------------------------------
# Vendors
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/vendors",
    response_model=VendorRecord,
    status_code=201,
)
def create_vendor(
    property_id: str,
    payload: VendorCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> VendorRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.create_vendor(property_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/vendors",
    response_model=list[VendorRecord],
)
def list_vendors(
    property_id: str,
    category: VendorCategory | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[VendorRecord]:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    return domain_repo.list_vendors(
        property_id=property_id,
        category=category,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/vendors/{vendor_id}",
    response_model=VendorRecord,
)
def get_vendor(
    property_id: str,
    vendor_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> VendorRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    vendor = domain_repo.get_vendor(property_id, vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail=f"Vendor '{vendor_id}' not found.")
    return vendor


@app.patch(
    "/api/v1/properties/{property_id}/vendors/{vendor_id}",
    response_model=VendorRecord,
)
def update_vendor(
    property_id: str,
    vendor_id: str,
    payload: VendorUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> VendorRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.update_vendor(property_id, vendor_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# -----------------------------------------------------------------------------
# Payments
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/payments",
    response_model=PaymentRecord,
    status_code=201,
)
def record_payment(
    property_id: str,
    payload: PaymentCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> PaymentRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.create_payment(property_id, payload)
    except CrossPropertyReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/payments",
    response_model=list[PaymentRecord],
)
def list_payments(
    property_id: str,
    status: PaymentStatus | None = Query(None),
    resident_id: str | None = Query(None),
    lease_id: str | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[PaymentRecord]:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    return domain_repo.list_payments(
        property_id=property_id,
        status=status,
        resident_id=resident_id,
        lease_id=lease_id,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/payments/{payment_id}",
    response_model=PaymentRecord,
)
def get_payment(
    property_id: str,
    payment_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> PaymentRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    payment = domain_repo.get_payment(property_id, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail=f"Payment '{payment_id}' not found.")
    return payment


@app.patch(
    "/api/v1/properties/{property_id}/payments/{payment_id}",
    response_model=PaymentRecord,
)
def update_payment(
    property_id: str,
    payment_id: str,
    payload: PaymentUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> PaymentRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.update_payment(property_id, payment_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# -----------------------------------------------------------------------------
# Expenses
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/expenses",
    response_model=ExpenseRecord,
    status_code=201,
)
def create_expense(
    property_id: str,
    payload: ExpenseCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> ExpenseRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.create_expense(property_id, payload)
    except CrossPropertyReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/expenses",
    response_model=list[ExpenseRecord],
)
def list_expenses(
    property_id: str,
    status: ExpenseStatus | None = Query(None),
    vendor_id: str | None = Query(None),
    unit_id: str | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[ExpenseRecord]:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    return domain_repo.list_expenses(
        property_id=property_id,
        status=status,
        vendor_id=vendor_id,
        unit_id=unit_id,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/expenses/{expense_id}",
    response_model=ExpenseRecord,
)
def get_expense(
    property_id: str,
    expense_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> ExpenseRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    expense = domain_repo.get_expense(property_id, expense_id)
    if not expense:
        raise HTTPException(status_code=404, detail=f"Expense '{expense_id}' not found.")
    return expense


@app.patch(
    "/api/v1/properties/{property_id}/expenses/{expense_id}",
    response_model=ExpenseRecord,
)
def update_expense(
    property_id: str,
    expense_id: str,
    payload: ExpenseUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> ExpenseRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.update_expense(property_id, expense_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# -----------------------------------------------------------------------------
# Maintenance Work Orders
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/work-orders",
    response_model=WorkOrderRecord,
    status_code=201,
)
def create_work_order(
    property_id: str,
    payload: WorkOrderCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> WorkOrderRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.create_work_order(property_id, payload)
    except CrossPropertyReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/work-orders",
    response_model=list[WorkOrderRecord],
)
def list_work_orders(
    property_id: str,
    status: WorkOrderStatus | None = Query(None),
    priority: WorkOrderPriority | None = Query(None),
    unit_id: str | None = Query(None),
    vendor_id: str | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[WorkOrderRecord]:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    return domain_repo.list_work_orders(
        property_id=property_id,
        status=status,
        priority=priority,
        unit_id=unit_id,
        vendor_id=vendor_id,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/work-orders/{work_order_id}",
    response_model=WorkOrderRecord,
)
def get_work_order(
    property_id: str,
    work_order_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> WorkOrderRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    wo = domain_repo.get_work_order(property_id, work_order_id)
    if not wo:
        raise HTTPException(status_code=404, detail=f"Work order '{work_order_id}' not found.")
    return wo


@app.patch(
    "/api/v1/properties/{property_id}/work-orders/{work_order_id}",
    response_model=WorkOrderRecord,
)
def update_work_order(
    property_id: str,
    work_order_id: str,
    payload: WorkOrderUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> WorkOrderRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.update_work_order(property_id, work_order_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))



# -----------------------------------------------------------------------------
# Persistent Maintenance Requests
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/maintenance-requests",
    response_model=MaintenanceRequestRecord,
    status_code=201,
)
def create_maintenance_request(
    property_id: str,
    payload: MaintenanceRequestCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> MaintenanceRequestRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )

    try:
        return domain_repo.create_maintenance_request(
            property_id, payload
        )
    except CrossPropertyReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/maintenance-requests",
    response_model=list[MaintenanceRequestRecord],
)
def list_maintenance_requests(
    property_id: str,
    status: MaintenanceRequestStatus | None = Query(None),
    unit_id: str | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[MaintenanceRequestRecord]:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )

    return domain_repo.list_maintenance_requests(
        property_id=property_id,
        status=status,
        unit_id=unit_id,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/maintenance-requests/{request_id}",
    response_model=MaintenanceRequestRecord,
)
def get_maintenance_request(
    property_id: str,
    request_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> MaintenanceRequestRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )

    result = domain_repo.get_maintenance_request(
        property_id, request_id
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Maintenance request '{request_id}' not found.",
        )
    return result


@app.patch(
    "/api/v1/properties/{property_id}/maintenance-requests/{request_id}",
    response_model=MaintenanceRequestRecord,
)
def update_maintenance_request(
    property_id: str,
    request_id: str,
    payload: MaintenanceRequestUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> MaintenanceRequestRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )

    try:
        return domain_repo.update_maintenance_request(
            property_id, request_id, payload
        )
    except CrossPropertyReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))





@app.post(
    "/api/v1/properties/{property_id}/maintenance-requests/{request_id}/triage",
    response_model=MaintenanceRequestRecord,
    tags=["Maintenance & Operations Agent"],
)
def triage_persistent_maintenance_request(
    property_id: str,
    request_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> MaintenanceRequestRecord:
    """Triage a saved maintenance request and persist the classification."""
    authorized_property_id = property_ctx.property.property_id

    if property_id != authorized_property_id:
        raise HTTPException(
            status_code=403,
            detail="Property context mismatch.",
        )

    domain_repo.ensure_property(
        property_id=authorized_property_id,
        name=property_ctx.property.name,
    )

    request = domain_repo.get_maintenance_request(
        authorized_property_id,
        request_id,
    )

    if request is None:
        raise HTTPException(
            status_code=404,
            detail=f"Maintenance request '{request_id}' not found.",
        )

    if request.status != MaintenanceRequestStatus.SUBMITTED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Only submitted maintenance requests can be triaged. "
                f"Current status: {request.status.value}."
            ),
        )

    agent = MaintenanceAgent(domain_repo)

    try:
        result = agent.triage_request(
            MaintenanceTriageRequest(
                unit_id=request.unit_id,
                description=request.description,
            )
        )

        sla_deadline_at = (
            datetime.now(timezone.utc)
            + timedelta(hours=result.estimated_sla_hours)
        )

        updated_request = domain_repo.update_maintenance_request(
            authorized_property_id,
            request_id,
            MaintenanceRequestUpdate(
                status=MaintenanceRequestStatus.TRIAGED,
                priority=result.priority,
                category=result.category.value,
                is_emergency=result.is_emergency,
                estimated_sla_hours=result.estimated_sla_hours,
                sla_deadline_at=sla_deadline_at,
            ),
        )

    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, InvalidOperationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="maintenance.request_triaged",
            entity_type="maintenance_request",
            entity_id=request_id,
            property_id=authorized_property_id,
            payload={
                "request_id": request_id,
                "unit_id": request.unit_id,
                "priority": result.priority.value,
                "category": result.category.value,
                "is_emergency": result.is_emergency,
                "estimated_sla_hours": result.estimated_sla_hours,
                "suggested_vendor_category": (
                    result.suggested_vendor_category.value
                ),
            },
        )
    )

    return updated_request





@app.post(
    "/api/v1/properties/{property_id}/maintenance-requests/{request_id}/convert",
    response_model=WorkOrderRecord,
    tags=["Maintenance & Operations Agent"],
)
def convert_maintenance_request(
    property_id: str,
    request_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> WorkOrderRecord:
    """Convert a triaged maintenance request into a work order."""
    authorized_property_id = property_ctx.property.property_id

    if property_id != authorized_property_id:
        raise HTTPException(
            status_code=403,
            detail="Property context mismatch.",
        )

    domain_repo.ensure_property(
        property_id=authorized_property_id,
        name=property_ctx.property.name,
    )

    try:
        work_order = domain_repo.convert_maintenance_request_to_work_order(
            property_id=authorized_property_id,
            request_id=request_id,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CrossPropertyReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="maintenance.request_converted_to_work_order",
            entity_type="work_order",
            entity_id=work_order.work_order_id,
            property_id=authorized_property_id,
            payload={
                "request_id": request_id,
                "work_order_id": work_order.work_order_id,
                "unit_id": work_order.unit_id,
                "priority": work_order.priority.value,
                "status": work_order.status.value,
            },
        )
    )

    return work_order



@app.post(
    "/api/v1/properties/{property_id}/maintenance-requests/{request_id}/dispatch",
    response_model=AutoDispatchWorkOrderResult,
    tags=["Maintenance & Operations Agent"],
)
def dispatch_maintenance_request(
    property_id: str,
    request_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> AutoDispatchWorkOrderResult:
    """Dispatch the work order linked to a converted maintenance request."""
    authorized_property_id = property_ctx.property.property_id

    if property_id != authorized_property_id:
        raise HTTPException(
            status_code=403,
            detail="Property context mismatch.",
        )

    domain_repo.ensure_property(
        property_id=authorized_property_id,
        name=property_ctx.property.name,
    )

    request = domain_repo.get_maintenance_request(
        authorized_property_id,
        request_id,
    )

    if request is None:
        raise HTTPException(
            status_code=404,
            detail=f"Maintenance request '{request_id}' not found.",
        )

    if (
        request.status != MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER
        or not request.work_order_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Convert the triaged maintenance request before dispatching it.",
        )

    agent = MaintenanceAgent(domain_repo)

    try:
        result = agent.auto_dispatch(
            property_id=authorized_property_id,
            request=AutoDispatchWorkOrderRequest(
                work_order_id=request.work_order_id,
                auto_assign=True,
            ),
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, InvalidOperationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CrossPropertyReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="maintenance.request_dispatched",
            entity_type="maintenance_request",
            entity_id=request_id,
            property_id=authorized_property_id,
            payload={
                "request_id": request_id,
                "work_order_id": result.work_order_id,
                "assigned_vendor_id": (
                    result.assigned_vendor.vendor_id
                    if result.assigned_vendor
                    else None
                ),
                "work_order_status": result.status.value,
            },
        )
    )

    return result




# -----------------------------------------------------------------------------
# SLA Monitoring and Escalation
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/sla/monitor",
    response_model=SlaMonitoringResult,
    tags=["SLA Monitoring & Operations"],
)
def run_sla_monitoring_cycle(
    property_id: str,
    warning_window_hours: float = Query(
        default=2.0,
        ge=0.1,
        le=168.0,
        description="Warning window in hours before SLA deadline",
    ),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> SlaMonitoringResult:
    """Run an SLA monitoring cycle for the property, evaluating approaching and overdue requests."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(
            status_code=403,
            detail="Property context mismatch.",
        )

    domain_repo.ensure_property(
        property_id=authorized_property_id,
        name=property_ctx.property.name,
    )

    agent = SLAMonitoringAgent(domain_repo)
    result = agent.monitor_property_sla(
        property_id=authorized_property_id,
        warning_window_hours=warning_window_hours,
    )

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="sla.monitoring_run",
            entity_type="property",
            entity_id=authorized_property_id,
            property_id=authorized_property_id,
            payload={
                "evaluated_count": result.evaluated_count,
                "approaching_deadline_count": result.approaching_deadline_count,
                "overdue_count": result.overdue_count,
                "new_escalations_count": result.new_escalations_count,
                "existing_escalations_count": result.existing_escalations_count,
                "warning_window_hours": warning_window_hours,
            },
        )
    )

    return result


@app.get(
    "/api/v1/properties/{property_id}/sla/escalations",
    response_model=list[SlaEscalationRecord],
    tags=["SLA Monitoring & Operations"],
)
def list_sla_escalations(
    property_id: str,
    escalation_type: SlaEscalationType | None = Query(None),
    status: SlaEscalationStatus | None = Query(None),
    request_id: str | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[SlaEscalationRecord]:
    """List SLA escalations with filtering and pagination."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(
            status_code=403,
            detail="Property context mismatch.",
        )

    domain_repo.ensure_property(
        property_id=authorized_property_id,
        name=property_ctx.property.name,
    )

    return domain_repo.list_sla_escalations(
        property_id=authorized_property_id,
        escalation_type=escalation_type,
        status=status,
        request_id=request_id,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/sla/escalations/{escalation_id}",
    response_model=SlaEscalationRecord,
    tags=["SLA Monitoring & Operations"],
)
def get_sla_escalation(
    property_id: str,
    escalation_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> SlaEscalationRecord:
    """Retrieve an individual SLA escalation record."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(
            status_code=403,
            detail="Property context mismatch.",
        )

    domain_repo.ensure_property(
        property_id=authorized_property_id,
        name=property_ctx.property.name,
    )

    result = domain_repo.get_sla_escalation(authorized_property_id, escalation_id)
    if result is None:
        other_prop = domain_repo._find_property_for_entity(
            "sla_escalations", "escalation_id", escalation_id
        )
        if other_prop:
            raise HTTPException(
                status_code=403,
                detail="Escalation belongs to another property.",
            )
        raise HTTPException(
            status_code=404,
            detail=f"SLA escalation '{escalation_id}' not found.",
        )
    return result


@app.patch(
    "/api/v1/properties/{property_id}/sla/escalations/{escalation_id}",
    response_model=SlaEscalationRecord,
    tags=["SLA Monitoring & Operations"],
)
def update_sla_escalation(
    property_id: str,
    escalation_id: str,
    payload: SlaEscalationUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> SlaEscalationRecord:
    """Acknowledge or resolve an SLA escalation record."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(
            status_code=403,
            detail="Property context mismatch.",
        )

    domain_repo.ensure_property(
        property_id=authorized_property_id,
        name=property_ctx.property.name,
    )

    current = domain_repo.get_sla_escalation(authorized_property_id, escalation_id)
    if current is None:
        other_prop = domain_repo._find_property_for_entity(
            "sla_escalations", "escalation_id", escalation_id
        )
        if other_prop:
            raise HTTPException(
                status_code=403,
                detail="Escalation belongs to another property.",
            )
        raise HTTPException(
            status_code=404,
            detail=f"SLA escalation '{escalation_id}' not found.",
        )

    try:
        updated = domain_repo.update_sla_escalation_status(
            property_id=authorized_property_id,
            escalation_id=escalation_id,
            update=payload,
        )
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CrossPropertyReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="sla.escalation_status_updated",
            entity_type="sla_escalation",
            entity_id=escalation_id,
            property_id=authorized_property_id,
            payload={
                "escalation_id": escalation_id,
                "request_id": updated.request_id,
                "escalation_type": updated.escalation_type.value,
                "previous_status": current.status.value,
                "new_status": updated.status.value,
                "notes": updated.notes,
            },
        )
    )

    return updated




# -----------------------------------------------------------------------------
# Document Metadata
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/documents",
    response_model=DocumentMetadataRecord,
    status_code=201,
)
def record_document_metadata(
    property_id: str,
    payload: DocumentMetadataCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.DOCUMENTS_ANALYZE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> DocumentMetadataRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    try:
        return domain_repo.create_document_metadata(property_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/documents",
    response_model=list[DocumentMetadataRecord],
)
def list_documents_metadata(
    property_id: str,
    associated_entity_type: str | None = Query(None),
    associated_entity_id: str | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.DOCUMENTS_ANALYZE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[DocumentMetadataRecord]:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    return domain_repo.list_document_metadata(
        property_id=property_id,
        associated_entity_type=associated_entity_type,
        associated_entity_id=associated_entity_id,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/documents/{document_id}",
    response_model=DocumentMetadataRecord,
)
def get_document_metadata(
    property_id: str,
    document_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.DOCUMENTS_ANALYZE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> DocumentMetadataRecord:
    domain_repo.ensure_property(
        property_id=property_ctx.property.property_id,
        name=property_ctx.property.name,
    )
    doc = domain_repo.get_document_metadata(property_id, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found.")
    return doc


# =============================================================================
# Phase 3: Master Orchestrator Workflow Endpoints
# =============================================================================

@app.post(
    "/api/v1/properties/{property_id}/workflows",
    response_model=WorkflowSummary,
    status_code=201,
)
def create_and_start_workflow(
    property_id: str,
    payload: WorkflowCreateRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    orchestrator: MasterOrchestrator = Depends(get_master_orchestrator),
) -> WorkflowSummary:
    try:
        state = orchestrator.create_workflow(
            property_id=property_ctx.property.property_id,
            initiated_by=property_ctx.user.user_id,
            request=payload,
        )
        state = orchestrator.run_workflow(
            property_id=property_ctx.property.property_id,
            workflow_id=state.workflow_id,
        )
        return state.to_summary()
    except PolicyValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except StepExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except WorkflowError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/workflows",
    response_model=list[WorkflowSummary],
)
def list_property_workflows(
    property_id: str,
    status: WorkflowStatus | None = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    orchestrator: MasterOrchestrator = Depends(get_master_orchestrator),
) -> list[WorkflowSummary]:
    return orchestrator.list_workflows(
        property_id=property_ctx.property.property_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/workflows/{workflow_id}",
    response_model=WorkflowSummary,
)
def get_workflow_status(
    property_id: str,
    workflow_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    orchestrator: MasterOrchestrator = Depends(get_master_orchestrator),
) -> WorkflowSummary:
    try:
        state = orchestrator.get_workflow(
            property_id=property_ctx.property.property_id,
            workflow_id=workflow_id,
        )
        return state.to_summary()
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post(
    "/api/v1/properties/{property_id}/workflows/{workflow_id}/resume",
    response_model=WorkflowSummary,
)
def resume_workflow(
    property_id: str,
    workflow_id: str,
    payload: WorkflowResumeRequest | None = None,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    orchestrator: MasterOrchestrator = Depends(get_master_orchestrator),
) -> WorkflowSummary:
    try:
        state = orchestrator.resume_workflow(
            property_id=property_ctx.property.property_id,
            workflow_id=workflow_id,
            resume_request=payload,
            actor_id=property_ctx.user.user_id,
        )
        return state.to_summary()
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (WorkflowStateError, ConcurrentExecutionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PolicyValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except StepExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post(
    "/api/v1/properties/{property_id}/workflows/{workflow_id}/cancel",
    response_model=WorkflowSummary,
)
def cancel_workflow(
    property_id: str,
    workflow_id: str,
    payload: WorkflowCancelRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    orchestrator: MasterOrchestrator = Depends(get_master_orchestrator),
) -> WorkflowSummary:
    try:
        state = orchestrator.cancel_workflow(
            property_id=property_ctx.property.property_id,
            workflow_id=workflow_id,
            cancel_request=payload,
            actor_id=property_ctx.user.user_id,
        )
        return state.to_summary()
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except WorkflowStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get(
    "/api/v1/properties/{property_id}/workflows/{workflow_id}/steps",
    response_model=list[WorkflowStepRecord],
)
def get_workflow_step_history(
    property_id: str,
    workflow_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    orchestrator: MasterOrchestrator = Depends(get_master_orchestrator),
) -> list[WorkflowStepRecord]:
    try:
        return orchestrator.list_steps(
            property_id=property_ctx.property.property_id,
            workflow_id=workflow_id,
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))




@app.post(
    "/api/v1/properties/{property_id}/resident-lease-analysis",
    response_model=ResidentLeaseAnalysisResult,
    tags=["Resident & Lease Agent"],
)
def analyze_resident_leases(
    property_id: str,
    payload: ResidentLeaseAnalysisRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.RESIDENT_READ)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> ResidentLeaseAnalysisResult:
    """Analyze resident and lease records without modifying them."""

    # Use the property validated by the authorization dependency.
    authorized_property_id = property_ctx.property.property_id

    if property_id != authorized_property_id:
        raise HTTPException(
            status_code=403,
            detail="Property context does not match the requested property.",
        )

    domain_repo.ensure_property(
        property_id=authorized_property_id,
        name=property_ctx.property.name,
    )

    agent = ResidentLeaseAgent(domain_repo)

    try:
        result = agent.analyze(
            property_id=authorized_property_id,
            request=payload,
        )
    except (ValueError, EntityNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="resident_lease.analysis_completed",
            entity_type="property",
            entity_id=authorized_property_id,
            property_id=authorized_property_id,
            payload={
                "analysis_date": result.analysis_date.isoformat(),
                "residents_analyzed": result.residents_analyzed,
                "leases_analyzed": result.leases_analyzed,
                "finding_count": len(result.findings),
                "finding_types": sorted(
                    {finding.finding_type.value for finding in result.findings}
                ),
            },
        )
    )

    return result


# -----------------------------------------------------------------------------
# Maintenance & Property Operations Agent
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/properties/{property_id}/maintenance/triage",
    response_model=MaintenanceTriageResult,
    tags=["Maintenance & Operations Agent"],
)
def triage_maintenance_request(
    property_id: str,
    payload: MaintenanceTriageRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> MaintenanceTriageResult:
    """Triage maintenance request text, detect emergencies, classify category, and assign SLAs."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    agent = MaintenanceAgent(domain_repo)
    return agent.triage_request(payload)


@app.post(
    "/api/v1/properties/{property_id}/maintenance/auto-dispatch",
    response_model=AutoDispatchWorkOrderResult,
    tags=["Maintenance & Operations Agent"],
)
def auto_dispatch_work_order(
    property_id: str,
    payload: AutoDispatchWorkOrderRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> AutoDispatchWorkOrderResult:
    """Auto-triage and dispatch a work order to the most qualified available vendor."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    domain_repo.ensure_property(property_id=authorized_property_id, name=property_ctx.property.name)
    agent = MaintenanceAgent(domain_repo)

    try:
        result = agent.auto_dispatch(property_id=authorized_property_id, request=payload)
    except (ValueError, EntityNotFoundError, CrossPropertyReferenceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="maintenance.work_order_dispatched",
            entity_type="work_order",
            entity_id=result.work_order_id,
            property_id=authorized_property_id,
            payload={
                "work_order_id": result.work_order_id,
                "category": result.category.value,
                "priority": result.priority.value,
                "assigned_vendor_id": result.assigned_vendor.vendor_id if result.assigned_vendor else None,
            },
        )
    )
    return result


@app.post(
    "/api/v1/properties/{property_id}/maintenance/inspections",
    response_model=InspectionAnalysisResult,
    tags=["Maintenance & Operations Agent"],
)
def record_property_inspection(
    property_id: str,
    payload: InspectionReportCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> InspectionAnalysisResult:
    """Record property inspection checklist, log deficiencies, and generate follow-up work orders."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    domain_repo.ensure_property(property_id=authorized_property_id, name=property_ctx.property.name)
    agent = MaintenanceAgent(domain_repo)

    try:
        result = agent.record_inspection(property_id=authorized_property_id, report=payload)
    except (ValueError, EntityNotFoundError, CrossPropertyReferenceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="maintenance.inspection_recorded",
            entity_type="inspection",
            entity_id=result.inspection_id,
            property_id=authorized_property_id,
            payload={
                "unit_id": result.unit_id,
                "inspection_type": result.inspection_type.value,
                "items_inspected": result.items_inspected,
                "items_failed": result.items_failed,
                "generated_work_orders_count": len(result.generated_work_orders),
            },
        )
    )
    return result


@app.post(
    "/api/v1/properties/{property_id}/maintenance/operations-analysis",
    response_model=MaintenanceOperationsAnalysisResult,
    tags=["Maintenance & Operations Agent"],
)
def analyze_maintenance_operations(
    property_id: str,
    payload: MaintenanceOperationsAnalysisRequest | None = None,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> MaintenanceOperationsAnalysisResult:
    """Analyze active work orders, SLA overdues, preventive maintenance tasks, and asset health."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    domain_repo.ensure_property(property_id=authorized_property_id, name=property_ctx.property.name)
    agent = MaintenanceAgent(domain_repo)

    result = agent.analyze_operations(property_id=authorized_property_id, request=payload)

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="maintenance.operations_analysis_completed",
            entity_type="property",
            entity_id=authorized_property_id,
            property_id=authorized_property_id,
            payload={
                "analysis_date": result.analysis_date.isoformat(),
                "open_work_orders": result.open_work_orders_count,
                "emergency_count": result.emergency_count,
                "overdue_count": result.overdue_work_orders_count,
                "preventive_due": result.preventive_tasks_due_count,
                "findings_count": len(result.findings),
            },
        )
    )
    return result


@app.get(
    "/api/v1/properties/{property_id}/maintenance/cost-analysis",
    response_model=MaintenanceCostAnalysisResult,
    tags=["Maintenance & Operations Agent"],
)
def analyze_maintenance_costs(
    property_id: str,
    as_of_date: date | None = None,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> MaintenanceCostAnalysisResult:
    """Analyze maintenance costs, budget variances, overruns, and category breakdown."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    domain_repo.ensure_property(property_id=authorized_property_id, name=property_ctx.property.name)
    agent = MaintenanceAgent(domain_repo)
    return agent.analyze_costs(property_id=authorized_property_id, as_of_date=as_of_date)


@app.post(
    "/api/v1/properties/{property_id}/maintenance/assets",
    response_model=AssetRecord,
    status_code=201,
    tags=["Maintenance & Operations Agent"],
)
def create_property_asset(
    property_id: str,
    payload: AssetCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> AssetRecord:
    """Register property or unit equipment asset for warranty and health tracking."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    domain_repo.ensure_property(property_id=authorized_property_id, name=property_ctx.property.name)
    return domain_repo.create_asset(authorized_property_id, payload)


@app.get(
    "/api/v1/properties/{property_id}/maintenance/assets",
    response_model=list[AssetRecord],
    tags=["Maintenance & Operations Agent"],
)
def list_property_assets(
    property_id: str,
    unit_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[AssetRecord]:
    """List tracked equipment assets for a property or specific unit."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return domain_repo.list_assets(authorized_property_id, unit_id=unit_id, limit=limit, offset=offset)


@app.post(
    "/api/v1/properties/{property_id}/maintenance/preventive-tasks",
    response_model=PreventiveTaskRecord,
    status_code=201,
    tags=["Maintenance & Operations Agent"],
)
def create_preventive_maintenance_task(
    property_id: str,
    payload: PreventiveTaskCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> PreventiveTaskRecord:
    """Schedule recurring preventive maintenance cycles."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    domain_repo.ensure_property(property_id=authorized_property_id, name=property_ctx.property.name)
    return domain_repo.create_preventive_schedule(authorized_property_id, payload)


@app.get(
    "/api/v1/properties/{property_id}/maintenance/preventive-tasks",
    response_model=list[PreventiveTaskRecord],
    tags=["Maintenance & Operations Agent"],
)
def list_preventive_maintenance_tasks(
    property_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[PreventiveTaskRecord]:
    """List scheduled preventive maintenance tasks."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return domain_repo.list_preventive_schedules(authorized_property_id, limit=limit, offset=offset)


# =============================================================================
# FINANCIAL OPERATIONS: Invoices & Payment Allocation
# =============================================================================

@app.post(
    "/api/v1/properties/{property_id}/invoices",
    response_model=InvoiceRecord,
    status_code=201,
    tags=["Financial Operations"],
)
def create_invoice(
    property_id: str,
    payload: InvoiceCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> InvoiceRecord:
    """Create a new rent or vendor invoice with validation and persistence."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    domain_repo.ensure_property(property_id=authorized_property_id, name=property_ctx.property.name)
    try:
        record = domain_repo.create_invoice(authorized_property_id, payload)
    except (IntegrityConflictError, CrossPropertyReferenceError, InvalidOperationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="financial.invoice_created",
            entity_type="invoice",
            entity_id=record.invoice_id,
            property_id=authorized_property_id,
            payload={
                "invoice_id": record.invoice_id,
                "invoice_number": record.invoice_number,
                "amount": str(record.amount),
                "invoice_type": record.invoice_type.value,
            },
        )
    )
    return record


@app.get(
    "/api/v1/properties/{property_id}/invoices",
    response_model=list[InvoiceRecord],
    tags=["Financial Operations"],
)
def list_invoices(
    property_id: str,
    status: InvoiceStatus | None = Query(None),
    invoice_type: InvoiceType | None = Query(None),
    resident_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[InvoiceRecord]:
    """List and filter invoices for a property."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return domain_repo.list_invoices(
        authorized_property_id,
        status=status,
        invoice_type=invoice_type,
        resident_id=resident_id,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/invoices/{invoice_id}",
    response_model=InvoiceRecord,
    tags=["Financial Operations"],
)
def get_invoice(
    property_id: str,
    invoice_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> InvoiceRecord:
    """Retrieve an invoice by its unique identifier."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    inv = domain_repo.get_invoice(authorized_property_id, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found.")
    return inv


@app.patch(
    "/api/v1/properties/{property_id}/invoices/{invoice_id}",
    response_model=InvoiceRecord,
    tags=["Financial Operations"],
)
def update_invoice(
    property_id: str,
    invoice_id: str,
    payload: InvoiceUpdate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> InvoiceRecord:
    """Update invoice metadata or status."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    try:
        updated = domain_repo.update_invoice(authorized_property_id, invoice_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="financial.invoice_updated",
            entity_type="invoice",
            entity_id=invoice_id,
            property_id=authorized_property_id,
            payload={"invoice_id": invoice_id, "status": updated.status.value},
        )
    )
    return updated


@app.post(
    "/api/v1/properties/{property_id}/invoices/{invoice_id}/payments",
    response_model=InvoiceRecord,
    tags=["Financial Operations"],
)
def allocate_payment_to_invoice(
    property_id: str,
    invoice_id: str,
    amount: Decimal = Body(..., embed=True),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> InvoiceRecord:
    """Allocate a full or partial payment towards an outstanding invoice."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    try:
        updated = domain_repo.record_invoice_payment(authorized_property_id, invoice_id, amount)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="financial.invoice_payment_allocated",
            entity_type="invoice",
            entity_id=invoice_id,
            property_id=authorized_property_id,
            payload={
                "invoice_id": invoice_id,
                "allocated_amount": str(amount),
                "outstanding_amount": str(updated.outstanding_amount),
                "new_status": updated.status.value,
            },
        )
    )
    return updated


# =============================================================================
# BANK RECONCILIATION & STATEMENT IMPORT
# =============================================================================

@app.post(
    "/api/v1/properties/{property_id}/bank-statements/import",
    response_model=dict,
    status_code=201,
    tags=["Bank Reconciliation"],
)
async def import_bank_statement(
    property_id: str,
    file: UploadFile = File(...),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    recon_service: ReconciliationService = Depends(get_reconciliation_service),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> dict:
    """Upload and reconcile a bank statement CSV against property records."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    domain_repo.ensure_property(property_id=authorized_property_id, name=property_ctx.property.name)

    content = await file.read()
    try:
        statement, transactions = recon_service.import_bank_statement(
            property_id=authorized_property_id,
            csv_content=content,
            filename=file.filename or "statement.csv",
            imported_by=property_ctx.user.user_id,
        )
    except StatementValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid statement CSV: {str(exc)}")
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Statement import failed: {str(exc)}")

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="financial.bank_statement_imported",
            entity_type="bank_statement",
            entity_id=statement.statement_id,
            property_id=authorized_property_id,
            payload={
                "statement_id": statement.statement_id,
                "filename": statement.filename,
                "transaction_count": statement.total_transactions,
            },
        )
    )

    return {
        "statement": statement,
        "transaction_count": len(transactions),
        "transactions": transactions,
    }


@app.get(
    "/api/v1/properties/{property_id}/bank-statements",
    response_model=list[BankStatementRecord],
    tags=["Bank Reconciliation"],
)
def list_bank_statements(
    property_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[BankStatementRecord]:
    """List imported bank statements."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return domain_repo.list_bank_statements(authorized_property_id, limit=limit, offset=offset)


@app.get(
    "/api/v1/properties/{property_id}/bank-statements/transactions",
    response_model=list[BankTransactionRecord],
    tags=["Bank Reconciliation"],
)
def list_bank_transactions(
    property_id: str,
    statement_id: str | None = Query(None),
    match_status: BankTransactionMatchStatus | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[BankTransactionRecord]:
    """List bank statement transactions with reconciliation status."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return domain_repo.list_bank_transactions(
        authorized_property_id,
        statement_id=statement_id,
        match_status=match_status,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/bank-statements/{statement_id}",
    response_model=BankStatementRecord,
    tags=["Bank Reconciliation"],
)
def get_bank_statement(
    property_id: str,
    statement_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> BankStatementRecord:
    """Retrieve details of an imported bank statement."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    stmt = domain_repo.get_bank_statement(authorized_property_id, statement_id)
    if not stmt:
        raise HTTPException(status_code=404, detail=f"Bank statement '{statement_id}' not found.")
    return stmt


@app.post(
    "/api/v1/properties/{property_id}/bank-statements/transactions/{transaction_id}/decide",
    response_model=BankTransactionRecord,
    tags=["Bank Reconciliation"],
)
def decide_reconciliation_match(
    property_id: str,
    transaction_id: str,
    payload: MatchDecisionRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    recon_service: ReconciliationService = Depends(get_reconciliation_service),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> BankTransactionRecord:
    """Approve, reject, or manually correct a proposed reconciliation match."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    try:
        updated = recon_service.decide_match(
            authorized_property_id,
            transaction_id,
            payload,
            decided_by=property_ctx.user.user_id,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (ValueError, IntegrityConflictError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="financial.reconciliation_match_decided",
            entity_type="bank_transaction",
            entity_id=transaction_id,
            property_id=authorized_property_id,
            payload={
                "transaction_id": transaction_id,
                "decision": payload.decision,
                "match_status": updated.match_status.value,
                "matched_entity_type": updated.matched_entity_type,
                "matched_entity_id": updated.matched_entity_id,
            },
        )
    )
    return updated


# =============================================================================
# EXPENSE MANAGEMENT & APPROVAL WORKFLOW
# =============================================================================

@app.post(
    "/api/v1/properties/{property_id}/expenses/{expense_id}/submit",
    response_model=ExpenseRecord,
    tags=["Expense Management"],
)
def submit_expense_for_approval(
    property_id: str,
    expense_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> ExpenseRecord:
    """Submit a draft expense record for formal review and approval."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    try:
        updated = domain_repo.submit_expense_for_approval(authorized_property_id, expense_id)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="financial.expense_submitted_for_approval",
            entity_type="expense",
            entity_id=expense_id,
            property_id=authorized_property_id,
            payload={"expense_id": expense_id, "amount": str(updated.amount)},
        )
    )
    return updated


@app.post(
    "/api/v1/properties/{property_id}/expenses/{expense_id}/decide",
    response_model=ExpenseRecord,
    tags=["Expense Management"],
)
def decide_expense_approval(
    property_id: str,
    expense_id: str,
    payload: ExpenseApprovalDecision,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> ExpenseRecord:
    """Approve, reject, or return an expense record with role and threshold enforcement."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    try:
        updated = domain_repo.decide_expense(
            authorized_property_id,
            expense_id,
            decision=payload.decision,
            approver_id=property_ctx.user.user_id,
            approver_role=property_ctx.role,
            reason=payload.reason,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (PermissionError, InvalidOperationError) as exc:
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc))

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="financial.expense_approval_decided",
            entity_type="expense",
            entity_id=expense_id,
            property_id=authorized_property_id,
            payload={
                "expense_id": expense_id,
                "approval_status": updated.approval_status if isinstance(updated.approval_status, str) else getattr(updated.approval_status, "value", "unknown"),
                "approved_by": property_ctx.user.user_id,
                "decision_reason": payload.reason,
            },
        )
    )
    return updated


@app.post(
    "/api/v1/properties/{property_id}/expenses/check-duplicate",
    response_model=dict,
    tags=["Expense Management"],
)
def check_duplicate_expense(
    property_id: str,
    payload: ExpenseCreate,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> dict:
    """Check if an expense payload matches a previously recorded expense."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    dup = domain_repo.detect_duplicate_expense(
        authorized_property_id,
        category=payload.category,
        amount=payload.amount,
        expense_date=payload.expense_date,
        vendor_id=payload.vendor_id,
    )
    return {
        "is_duplicate": dup is not None,
        "duplicate_expense_id": dup.expense_id if dup else None,
        "existing_expense": dup,
    }


# =============================================================================
# COMPLIANCE MONITORING
# =============================================================================

@app.post(
    "/api/v1/properties/{property_id}/compliance/audit",
    response_model=list[ComplianceFindingRecord],
    tags=["Compliance Monitoring"],
)
def run_compliance_audit(
    property_id: str,
    as_of_date: date | None = Query(None),
    grace_days: int = Query(5, ge=0, le=30),
    auto_escalate: bool = Query(True),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    compliance_service: ComplianceService = Depends(get_compliance_service),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> list[ComplianceFindingRecord]:
    """Execute rule-based compliance checks and auto-escalate severe issues."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    findings = compliance_service.run_compliance_audit(
        authorized_property_id,
        as_of_date=as_of_date,
        grace_days=grace_days,
        auto_escalate_severe=auto_escalate,
    )

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="compliance.audit_executed",
            entity_type="compliance",
            entity_id=f"audit-{authorized_property_id}-{as_of_date or date.today()}",
            property_id=authorized_property_id,
            payload={"findings_detected": len(findings)},
        )
    )
    return findings


@app.get(
    "/api/v1/properties/{property_id}/compliance/findings",
    response_model=list[ComplianceFindingRecord],
    tags=["Compliance Monitoring"],
)
def list_compliance_findings(
    property_id: str,
    category: ComplianceCategory | None = Query(None),
    severity: ComplianceSeverity | None = Query(None),
    status: ComplianceStatus | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[ComplianceFindingRecord]:
    """List operational compliance findings with filtering."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return domain_repo.list_compliance_findings(
        authorized_property_id,
        category=category,
        severity=severity,
        status=status,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/compliance/findings/{finding_id}",
    response_model=ComplianceFindingRecord,
    tags=["Compliance Monitoring"],
)
def get_compliance_finding(
    property_id: str,
    finding_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> ComplianceFindingRecord:
    """Retrieve an individual compliance finding."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    f = domain_repo.get_compliance_finding(authorized_property_id, finding_id)
    if not f:
        raise HTTPException(status_code=404, detail=f"Compliance finding '{finding_id}' not found.")
    return f


@app.post(
    "/api/v1/properties/{property_id}/compliance/findings/{finding_id}/assign",
    response_model=ComplianceFindingRecord,
    tags=["Compliance Monitoring"],
)
def assign_compliance_finding(
    property_id: str,
    finding_id: str,
    payload: ComplianceAssignmentRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    compliance_service: ComplianceService = Depends(get_compliance_service),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> ComplianceFindingRecord:
    """Assign an open finding to an investigator or manager."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    try:
        updated = compliance_service.assign_finding(authorized_property_id, finding_id, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="compliance.finding_assigned",
            entity_type="compliance_finding",
            entity_id=finding_id,
            property_id=authorized_property_id,
            payload={"finding_id": finding_id, "assigned_to": payload.assigned_to},
        )
    )
    return updated


@app.post(
    "/api/v1/properties/{property_id}/compliance/findings/{finding_id}/resolve",
    response_model=ComplianceFindingRecord,
    tags=["Compliance Monitoring"],
)
def resolve_compliance_finding(
    property_id: str,
    finding_id: str,
    payload: ComplianceResolutionRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.PROPERTY_MANAGE)
    ),
    compliance_service: ComplianceService = Depends(get_compliance_service),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> ComplianceFindingRecord:
    """Resolve or dismiss a compliance finding with a documented reason."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    try:
        updated = compliance_service.resolve_finding(
            authorized_property_id,
            finding_id,
            payload,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="compliance.finding_resolved",
            entity_type="compliance_finding",
            entity_id=finding_id,
            property_id=authorized_property_id,
            payload={"finding_id": finding_id, "status": updated.status.value, "notes": payload.reason},
        )
    )
    return updated


# =============================================================================
# EMAIL NOTIFICATIONS & PREVIEWS
# =============================================================================

@app.post(
    "/api/v1/properties/{property_id}/notifications/preview",
    response_model=NotificationPreviewResponse,
    tags=["Email Notifications"],
)
def preview_notification(
    property_id: str,
    payload: NotificationPreviewRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    notification_service: NotificationService = Depends(get_notification_service),
) -> NotificationPreviewResponse:
    """Preview rendered email text and HTML without dispatching."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return notification_service.preview_notification(authorized_property_id, payload)


@app.post(
    "/api/v1/properties/{property_id}/notifications/send",
    response_model=NotificationRecord,
    status_code=201,
    tags=["Email Notifications"],
)
def send_notification(
    property_id: str,
    payload: NotificationSendRequest,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    notification_service: NotificationService = Depends(get_notification_service),
    audit_trail: AuditTrail = Depends(get_audit_trail),
) -> NotificationRecord:
    """Send or simulate sending an email notification."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    record = notification_service.send_notification(
        authorized_property_id,
        payload,
        created_by=property_ctx.user.user_id,
    )

    audit_trail.append(
        AuditEventCreate(
            actor_id=property_ctx.user.user_id,
            actor_type="user",
            event_type="notification.dispatched",
            entity_type="notification",
            entity_id=record.notification_id,
            property_id=authorized_property_id,
            payload={
                "notification_id": record.notification_id,
                "recipient": record.recipient_email,
                "type": record.notification_type.value,
                "status": record.status.value,
                "delivery_mode": record.delivery_mode.value,
            },
        )
    )
    return record


@app.get(
    "/api/v1/properties/{property_id}/notifications",
    response_model=list[NotificationRecord],
    tags=["Email Notifications"],
)
def list_notifications(
    property_id: str,
    status: NotificationStatus | None = Query(None),
    notification_type: NotificationType | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> list[NotificationRecord]:
    """List notification history for a property."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return domain_repo.list_notifications(
        authorized_property_id,
        status=status,
        notification_type=notification_type,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/properties/{property_id}/notifications/{notification_id}",
    response_model=NotificationRecord,
    tags=["Email Notifications"],
)
def get_notification(
    property_id: str,
    notification_id: str,
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    domain_repo: DomainRepository = Depends(get_domain_repository),
) -> NotificationRecord:
    """Retrieve details of a sent or simulated notification."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    n = domain_repo.get_notification(authorized_property_id, notification_id)
    if not n:
        raise HTTPException(status_code=404, detail=f"Notification '{notification_id}' not found.")
    return n


# =============================================================================
# FINANCIAL REPORTING & EXPORTS
# =============================================================================

@app.get(
    "/api/v1/properties/{property_id}/reports/financial-summary",
    response_model=FinancialSummaryReport,
    tags=["Financial Reporting"],
)
def get_financial_summary(
    property_id: str,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    currency: str = Query("USD"),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    reporting_service: FinancialReportingService = Depends(get_financial_reporting_service),
) -> FinancialSummaryReport:
    """Calculate and return key financial performance metrics."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return reporting_service.generate_financial_summary(
        authorized_property_id,
        start_date=start_date,
        end_date=end_date,
        currency=currency,
    )


@app.get(
    "/api/v1/properties/{property_id}/reports/financial-summary/csv",
    tags=["Financial Reporting"],
)
def export_financial_summary_csv(
    property_id: str,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    currency: str = Query("USD"),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    reporting_service: FinancialReportingService = Depends(get_financial_reporting_service),
) -> Response:
    """Export property financial performance report as a CSV spreadsheet."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    report = reporting_service.generate_financial_summary(
        authorized_property_id,
        start_date=start_date,
        end_date=end_date,
        currency=currency,
    )
    csv_text = reporting_service.export_summary_csv(report)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=financial_summary_{property_id}.csv"},
    )


@app.get(
    "/api/v1/properties/{property_id}/reports/owner-statement",
    response_model=OwnerStatementReport,
    tags=["Financial Reporting"],
)
def get_owner_statement(
    property_id: str,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    management_fee_rate: Decimal | None = Query(None),
    currency: str = Query("USD"),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    reporting_service: FinancialReportingService = Depends(get_financial_reporting_service),
) -> OwnerStatementReport:
    """Generate owner financial statement showing collections, expenses, and distributable income."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    return reporting_service.generate_owner_statement(
        authorized_property_id,
        start_date=start_date,
        end_date=end_date,
        management_fee_rate=management_fee_rate,
        currency=currency,
    )


@app.get(
    "/api/v1/properties/{property_id}/reports/owner-statement/csv",
    tags=["Financial Reporting"],
)
def export_owner_statement_csv(
    property_id: str,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    management_fee_rate: Decimal | None = Query(None),
    currency: str = Query("USD"),
    property_ctx: PropertyContext = Depends(
        require_property_permission(Permission.FINANCIAL_OPERATIONS)
    ),
    reporting_service: FinancialReportingService = Depends(get_financial_reporting_service),
) -> Response:
    """Export owner financial statement with itemized expenses as a CSV spreadsheet."""
    authorized_property_id = property_ctx.property.property_id
    if property_id != authorized_property_id:
        raise HTTPException(status_code=403, detail="Property context mismatch.")

    stmt = reporting_service.generate_owner_statement(
        authorized_property_id,
        start_date=start_date,
        end_date=end_date,
        management_fee_rate=management_fee_rate,
        currency=currency,
    )
    csv_text = reporting_service.export_owner_statement_csv(stmt)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=owner_statement_{property_id}.csv"},
    )


