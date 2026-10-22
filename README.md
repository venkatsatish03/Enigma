# ENIGMA — Document Intelligence, Financial Operations, and Master Orchestrator

ENIGMA is an enterprise property-management AI orchestration platform featuring:
- **Intake & Document Intelligence**: Dual-engine text extraction (PyMuPDF & PaddleOCR) with line-item arithmetic validation and cross-document reconciliation.
- **Financial Operations**: Deterministic double-entry ledger matching, payment allocation, and discrepancy detection.
- **Maintenance & Property Operations**: Autonomous request triage, emergency safety fast-tracking, vendor matching & auto-dispatch, inspection deficiency work order generation, preventive maintenance cycles, equipment asset tracking, and budget cost variance reporting.
- **Resident & Lease Intelligence**: Automated duplicate identification, data integrity checking, and lease expiration risk scanning.
- **Master Orchestrator**: Durable SQLite workflow engine with step-level state checkpointing, worker locks, and bounded retries.
- **Human-in-the-Loop Approvals**: Property-scoped action approval workflow with separation-of-duties enforcement.
- **Audit & Cryptographic Security**: SHA-256 hash-chained tamper-evident audit logging, Argon2id password hashing, and role-based access control (RBAC).

---

## Table of Contents
1. [Prerequisites & Environment Setup](#prerequisites--environment-setup)
2. [Database Bootstrap & Password Management](#database-bootstrap--password-management)
3. [Running the Application](#running-the-application)
4. [Running the Test Suites](#running-the-test-suites)
5. [Complete API Reference & Execution Commands](#complete-api-reference--execution-commands)
   - [Authentication & User Profile](#1-authentication--profile)
   - [Document Intelligence Agent](#2-document-intelligence-agent)
   - [Financial Operations](#3-financial-operations)
   - [Maintenance & Property Operations Agent](#4-maintenance--property-operations-agent)
   - [Resident & Lease Intelligence Agent](#5-resident--lease-intelligence-agent)
   - [Master Orchestrator Workflows](#6-master-orchestrator-workflows)
   - [Human Approval Workflow](#7-human-approval-workflow)
   - [Audit Trail & Cryptographic Verification](#8-audit-trail--cryptographic-verification)
   - [Domain Repository Entities](#9-domain-repository-crud)
6. [End-to-End Workflow Walkthrough](#end-to-end-workflow-walkthrough)

---

## Prerequisites & Environment Setup

### 1. Virtual Environment Activation
On Windows (PowerShell):
```powershell
.\.venv\Scripts\Activate.ps1
```

On Linux / macOS:
```bash
source .venv/bin/activate
```

### 2. Install Dependencies
```powershell
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and verify your configuration:
```powershell
cp .env.example .env
```

Key environment variables in `.env`:
```env
# Cryptographically secure secret key (min 32 characters)
ENIGMA_JWT_SECRET_KEY=********
ENIGMA_JWT_ALGORITHM=HS256
ENIGMA_JWT_EXPIRE_MINUTES=60
ENIGMA_JWT_ISSUER=enigma-api

# Optional custom database locations (default to data/*.sqlite3)
ENIGMA_AUTH_DB=data/auth.sqlite3
ENIGMA_DOMAIN_DB=data/domain.sqlite3
ENIGMA_WORKFLOWS_DB=data/workflows.sqlite3

# Required for Google verified sign-up and sign-in; register the callback URL below.
ENIGMA_GOOGLE_CLIENT_ID=your-google-oauth-client-id
ENIGMA_GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
ENIGMA_GOOGLE_REDIRECT_URI=http://localhost:3000/api/backend/api/v1/auth/google/callback
ENIGMA_FRONTEND_BASE_URL=http://localhost:3000

# Required for forgot-password email delivery.
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your-smtp-user
SMTP_PASSWORD=your-smtp-password
SMTP_FROM_EMAIL=accounts@example.com
SMTP_USE_TLS=true
```

The OAuth redirect URI must exactly match the URL registered for the Google OAuth client. For local development, register `http://localhost:3000/api/backend/api/v1/auth/google/callback`. Google sign-in is completed by the backend, which validates the signed identity token and verified email before creating or linking an account. New Google accounts set an ENIGMA password immediately after verification. Password reset links are one-use and expire after 30 minutes; configure SMTP for their delivery.

### Tenant and owner portals

Newly registered and resident-only accounts open the tenant portal by default at `/tenant`. Tenant records are linked to a verified Google email only when it matches one unambiguous resident record for a property. Tenants can view linked leases, invoices, payments, and maintenance requests, and submit a maintenance request for an active lease unit.

The tenant portal includes an **Owner portal** switch. A first-time owner starts with a property listing wizard at `/owner/new`, saves a draft, uploads property photographs, floor plans, or a walkthrough video, then submits the listing for review. Submitted listings are kept in a separate owner listing database and their media is stored in `data/listing_media`; configure `ENIGMA_OWNER_LISTINGS_DB` and `ENIGMA_LISTING_MEDIA_DIR` to change those paths. The existing role-scoped admin workspace remains available at `/dashboard`.

---

## Database Bootstrap & Password Management

### Bootstrap Initial Administrator & Property
Run the CLI bootstrap tool to create an initial property manager or superuser:
```powershell
.\.venv\Scripts\python.exe -m tools.auth_bootstrap `
  --email admin@example.com `
  --password "AdminSecurePassword123!" `
  --property-id prop-highland `
  --property-name "Highland Park" `
  --role admin `
  --superuser
```

### Reset or Update Password
If you forget the password or need to update an existing user's credentials:
```powershell
.\.venv\Scripts\python.exe -m tools.auth_bootstrap `
  --email admin@example.com `
  --password "NewSecurePassword123!" `
  --reset-password
```

---

## Running the Application

### Start the FastAPI Server
```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

- **Interactive API Documentation (Swagger UI)**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Alternative ReDoc UI**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
- **OpenAPI Schema**: [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json)

---

## Running the Test Suites

### 1. Full Test Suite (332 Passing Tests)
```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```
*(Verbose mode: `.\.venv\Scripts\python.exe -m pytest tests -v`)*

### 2. Financial Verification & Regression Test Suite
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_verification_suite.py -v
```

### 3. Finance & Compliance Backend Test Suite
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_finance_compliance_suite.py -v
```

### 4. Maintenance & Property Operations Agent Tests
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_maintenance_agent.py -v
```

### 5. Resident & Lease Intelligence Agent Tests
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_resident_lease_agent.py -v
```

### 6. Master Orchestrator Tests (Phase 3 Engine)
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_master_orchestrator.py -v
```

### 7. Authentication & RBAC Tests
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_and_property_authorization.py -v
```

### 8. Comprehensive Audit Regression Suite (Scenarios A through G)
```powershell
.\.venv\Scripts\python.exe -m tests.test_comprehensive_audit_regression
```

### 9. Domain Repository Layer Tests
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_domain_repository.py -v
```

---

## Complete API Reference & Execution Commands

### 1. Authentication & Profile

> **Note for Windows PowerShell users:** In standard Windows PowerShell, `curl` is an alias for `Invoke-WebRequest`. Always use **`curl.exe`** or **`Invoke-RestMethod`** to avoid parameter binding errors.

#### Login & Acquire JWT Bearer Token

**Using `curl.exe`:**
```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/auth/login" `
  -H "Content-Type: application/json" `
  -d "{\"email\": \"admin@example.com\", \"password\": \"AdminSecurePassword123!\"}"
```

**Using PowerShell `Invoke-RestMethod` (Recommended for PowerShell):**
```powershell
$response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/auth/login" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"email": "admin@example.com", "password": "AdminSecurePassword123!"}'

$TOKEN = $response.access_token
```

**Response:**
```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

#### Get Current User Profile & Property Memberships
```powershell
curl.exe -X GET "http://127.0.0.1:8000/api/v1/auth/me" `
  -H "Authorization: Bearer $TOKEN"
```
Or in PowerShell:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/auth/me" `
  -Headers @{ Authorization = "Bearer $TOKEN" }
```

---

### 2. Document Intelligence Agent

#### Analyze Document (PDF / Scanned Image)
Analyzes searchable text, runs PaddleOCR when needed, extracts structured fields, and audits arithmetic:
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/documents/analyze" `
  -H "Authorization: Bearer <TOKEN>" `
  -F "file=@data/input/sample_invoice.pdf"
```

---

### 3. Financial Operations & Compliance

#### 3.1 Document Reconciliation (Double-Entry Engine)
Executes line-item arithmetic verification and cross-document payment matching:
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/financial-operations" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{
    "document_agent_output": {
      "status": "completed",
      "source_file": "invoice.pdf",
      "documents": [
        {
          "document_id": "doc-01",
          "file_name": "invoice.pdf",
          "source_file": "invoice.pdf",
          "document_type": "invoice",
          "total_amount": 750.00,
          "currency": "USD",
          "document_date": "2026-04-10",
          "vendor_name": "Apex HVAC"
        }
      ]
    }
  }'
```

#### 3.2 Invoices & Payment Allocation
- **Create Invoice**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/invoices" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{
      "invoice_number": "INV-2026-001",
      "invoice_type": "rent",
      "amount": "2400.00",
      "currency": "USD",
      "issue_date": "2026-05-01",
      "due_date": "2026-05-05",
      "description": "May 2026 Rent"
    }'
  ```
- **Allocate Payment Against Invoice (Atomic / Concurrency Safe)**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/invoices/<INVOICE_ID>/payments" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{"amount": "2400.00"}'
  ```

#### 3.3 Bank Reconciliation
- **Import Bank Statement (CSV)**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/bank-statements/upload" `
    -H "Authorization: Bearer <TOKEN>" `
    -F "file=@statement_may2026.csv" `
    -F "account_number=123456789" `
    -F "closing_balance=45200.00"
  ```
- **Run Auto-Reconciliation**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/bank-statements/<STATEMENT_ID>/reconcile" `
    -H "Authorization: Bearer <TOKEN>"
  ```
- **Reconciliation Match Decision (Approve / Reject / Correct)**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/bank-statements/transactions/<TX_ID>/decision" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{"decision": "approve"}'
  ```

#### 3.4 Expense Approval Workflow
- **Submit Expense for Approval**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/expenses/<EXPENSE_ID>/submit" `
    -H "Authorization: Bearer <TOKEN>"
  ```
- **Approve or Reject Expense**:
  Property Managers can approve expenses up to the configurable threshold (`ENIGMA_MANAGER_EXPENSE_LIMIT`, default `$1,000.00`). Expenses above this limit require an `admin` or `owner`:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/expenses/<EXPENSE_ID>/decide" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{"decision": "approve", "reason": "Authorized emergency repair."}'
  ```

#### 3.5 Compliance Monitoring & SLA Escalations
- **Run Compliance Audit**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/compliance/audit" `
    -H "Authorization: Bearer <TOKEN>"
  ```
- **Resolve Finding**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/compliance/findings/<FINDING_ID>/resolve" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{"notes": "Smoke detector batteries replaced and log updated."}'
  ```

#### 3.6 Multi-Channel Notifications
- **Preview Notification (Simulation)**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/notifications/preview" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{
      "template_id": "rent_due",
      "recipient": "resident@example.com",
      "context": {"first_name": "Alice", "amount_due": "2400.00", "due_date": "2026-05-05"}
    }'
  ```

#### 3.7 Financial Performance Reporting & Owner Statements
All calculations implement audited business rules:
- **Payment Realization Rule**: Only `SETTLED` and `RECONCILED` payments are counted in `total_rent_collected` and `cash_inflows`. Unsettled payments (`PENDING`) are tracked as pipeline metrics (`pending_rent_amount`, `pending_payments_count`). `FAILED` and `REFUNDED` payments are excluded from realized inflows.
- **Collection Rate Formula**: $(\text{rent\_collected} / \text{rent\_billed}) \times 100$. When `total_rent_billed == 0`, returns `null` (`None`) and prints `N/A` in CSV exports rather than an inaccurate 100%.
- **Outstanding Rent Obligations**: Calculated directly from actual invoice obligations minus allocations ($\sum \text{inv.outstanding\_amount}$ for rent invoices issued in the period), avoiding period mismatch and double counting.
- **NOI & Owner Distributions**: Prevents double-deducting management fees if an expense with `category == ExpenseCategory.MANAGEMENT` is already present in operating expenses. Management fee rate is configurable per property, per request, via `ENIGMA_MANAGEMENT_FEE_RATE`, or default `0.08` (8%).
- **Multi-Currency Isolation**: Each report isolates records by matching currency (default `USD`).

Endpoints:
- **Get Financial Summary (JSON)**:
  ```powershell
  curl -X GET "http://127.0.0.1:8000/api/v1/properties/prop-highland/reports/financial-summary?start_date=2026-05-01&end_date=2026-05-31" `
    -H "Authorization: Bearer <TOKEN>"
  ```
- **Export Financial Summary (CSV)**:
  ```powershell
  curl -X GET "http://127.0.0.1:8000/api/v1/properties/prop-highland/reports/financial-summary/csv" `
    -H "Authorization: Bearer <TOKEN>" -o financial_summary.csv
  ```
- **Get Owner Statement (JSON)**:
  ```powershell
  curl -X GET "http://127.0.0.1:8000/api/v1/properties/prop-highland/reports/owner-statement?management_fee_rate=0.08" `
    -H "Authorization: Bearer <TOKEN>"
  ```
- **Export Owner Statement (CSV)**:
  ```powershell
  curl -X GET "http://127.0.0.1:8000/api/v1/properties/prop-highland/reports/owner-statement/csv" `
    -H "Authorization: Bearer <TOKEN>" -o owner_statement.csv
  ```

---

### 4. Maintenance & Property Operations Agent

#### Triage Maintenance Request
Autonomous natural language request triage with emergency detection and containment safety instructions:
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/maintenance/triage" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{"description": "Strong gas smell coming from boiler room near unit 101!"}'
```

#### Auto-Dispatch Work Order to Qualified Contractor
Triages issue, scores candidate contractors by specialty category and current workload, and dispatches:
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/maintenance/auto-dispatch" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{
    "unit_id": "<UNIT_ID>",
    "title": "Air conditioning unit blowing hot air",
    "description": "AC compressor rattling and blowing hot air in summer heat",
    "auto_assign": true,
    "budget_limit": "350.00"
  }'
```

#### Record Inspection Checklist & Auto-Generate Deficiency Work Orders
Records move-in, move-out, quarterly, or annual checklists and automatically creates follow-up work orders for failed items:
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/maintenance/inspections" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{
    "unit_id": "<UNIT_ID>",
    "inspection_type": "move_out",
    "inspector_name": "Dave Miller",
    "items": [
      {"item_name": "Kitchen smoke detector", "category": "electrical", "status": "pass"},
      {"item_name": "Under-sink pipe", "category": "plumbing", "status": "fail", "notes": "Active drip at P-trap", "severity_if_failed": "medium"}
    ]
  }'
```

#### Analyze Active Operations & SLA Overdue Tasks
Scans active work orders, SLA overdues, upcoming/overdue preventive schedules, and expiring warranties:
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/maintenance/operations-analysis" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{"lookback_days": 90, "overdue_sla_hours_threshold": 48}'
```

#### Maintenance Cost Variance & Overrun Report
Aggregates estimated vs actual spending, category breakdown, and top-spending units and contractors:
```powershell
curl -X GET "http://127.0.0.1:8000/api/v1/properties/prop-highland/maintenance/cost-analysis" `
  -H "Authorization: Bearer <TOKEN>"
```

---

### 5. Resident & Lease Agent

#### Analyze Resident Records and Lease Risks
Deterministic, read-only analysis of duplicate residents, missing identity fields, expired active leases, approaching expirations, and overlapping unit/resident leases:
```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/resident-lease-analysis" `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"expiration_window_days": 60}'
```
Or in PowerShell:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/properties/prop-highland/resident-lease-analysis" `
  -Method Post `
  -Headers @{ Authorization = "Bearer $TOKEN" } `
  -ContentType "application/json" `
  -Body '{"expiration_window_days": 60}'
```

---

### 6. Master Orchestrator Workflows

#### Create and Start a Workflow
Workflow types: `invoice_processing`, `rent_reconciliation`, `financial_audit`, `maintenance_lifecycle`.
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/workflows" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{
    "workflow_type": "invoice_processing",
    "input_reference": "data/input/sample_invoice.pdf"
  }'
```

#### Run an Automated Maintenance Lifecycle Workflow
Triage $\rightarrow$ Vendor Dispatch $\rightarrow$ Policy Gate $\rightarrow$ Approval Gate $\rightarrow$ Execution $\rightarrow$ Outcome Verification:
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/workflows" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{
    "workflow_type": "maintenance_lifecycle",
    "input_reference": "Main water supply line leaking rapidly under kitchen sink",
    "metadata": {
      "unit_id": "unit-101",
      "title": "Kitchen Main Water Line Leak",
      "estimated_cost": 450.00
    }
  }'
```

#### List Workflows for a Property
```powershell
curl -X GET "http://127.0.0.1:8000/api/v1/properties/prop-highland/workflows?limit=10" `
  -H "Authorization: Bearer <TOKEN>"
```

#### Get Workflow Details & State
```powershell
curl -X GET "http://127.0.0.1:8000/api/v1/properties/prop-highland/workflows/{workflow_id}" `
  -H "Authorization: Bearer <TOKEN>"
```

#### Get Chronological Step History
```powershell
curl -X GET "http://127.0.0.1:8000/api/v1/properties/prop-highland/workflows/{workflow_id}/steps" `
  -H "Authorization: Bearer <TOKEN>"
```

#### Resume a Paused Workflow (After Approval)
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/workflows/{workflow_id}/resume" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{"notes": "Authorized after reviewing invoice details."}'
```

#### Cancel a Workflow
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/workflows/{workflow_id}/cancel" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{"reason": "Duplicate invoice submission."}'
```

---

### 7. Human Approval Workflow

#### List Approval Requests
```powershell
curl -X GET "http://127.0.0.1:8000/api/v1/approvals?property_id=prop-highland" `
  -H "Authorization: Bearer <TOKEN>"
```

#### Get Specific Approval Request
```powershell
curl -X GET "http://127.0.0.1:8000/api/v1/approvals/{approval_id}" `
  -H "Authorization: Bearer <TOKEN>"
```

#### Submit Approval Decision (Approve or Reject)
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/approvals/{approval_id}/decision" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "Content-Type: application/json" `
  -d '{
    "approval_id": "{approval_id}",
    "decision": "approve",
    "reason": "Verified contractor line items and amounts match budget."
  }'
```

---

### 8. Audit Trail & Cryptographic Verification

#### Query Property-Scoped Audit Events
```powershell
curl -X GET "http://127.0.0.1:8000/api/v1/audit/events?limit=25" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "X-Property-ID: prop-highland"
```

#### Verify Hash-Chain Integrity
Validates that historical SHA-256 blocks have not been tampered with:
```powershell
curl -X GET "http://127.0.0.1:8000/api/v1/audit/verify" `
  -H "Authorization: Bearer <TOKEN>" `
  -H "X-Property-ID: prop-highland"
```
**Response:**
```json
{
  "valid": true,
  "events_verified": 42,
  "tampered_event_id": null
}
```

---

### 9. Domain Repository CRUD

All domain endpoints are scoped by `property_id` and enforce property-level permissions:

#### Units
- **Create Unit**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/units" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{"unit_number": "101", "floor": 1, "bedrooms": 2, "bathrooms": 2.0, "status": "vacant"}'
  ```
- **List Units**:
  ```powershell
  curl -X GET "http://127.0.0.1:8000/api/v1/properties/prop-highland/units" `
    -H "Authorization: Bearer <TOKEN>"
  ```

#### Residents
- **Create Resident**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/residents" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{"first_name": "Jane", "last_name": "Doe", "email": "jane@example.com", "phone": "555-0100"}'
  ```

#### Leases
- **Create Lease**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/leases" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{
      "unit_id": "<UNIT_ID>",
      "resident_id": "<RESIDENT_ID>",
      "start_date": "2026-01-01",
      "end_date": "2026-12-31",
      "rent_amount": "1800.00",
      "deposit_amount": "1800.00"
    }'
  ```

#### Expenses
- **Create Expense**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/expenses" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{
      "category": "repairs",
      "amount": "450.00",
      "currency": "USD",
      "expense_date": "2026-04-12",
      "description": "Emergency plumbing repair"
    }'
  ```

#### Payments
- **Record Rent Payment**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/payments" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{
      "amount": "1800.00",
      "currency": "USD",
      "payment_date": "2026-04-01",
      "payment_method": "ach",
      "resident_id": "<RESIDENT_ID>",
      "reference_number": "ACH-2026-04"
    }'
  ```

#### Vendors
- **Create Vendor**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/vendors" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{"name": "Quick Fix Plumbing", "category": "plumbing", "email": "service@quickfix.example"}'
  ```

#### Work Orders
- **Create Work Order**:
  ```powershell
  curl -X POST "http://127.0.0.1:8000/api/v1/properties/prop-highland/work-orders" `
    -H "Authorization: Bearer <TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{
      "title": "Kitchen faucet leak",
      "priority": "medium",
      "unit_id": "<UNIT_ID>",
      "description": "Dripping tap in unit 101"
    }'
  ```

---

## End-to-End Workflow Walkthrough

Here is a typical complete invoice lifecycle execution:

1. **Start the API Server**:
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn main:app --reload
   ```

2. **Authenticate**:
   ```powershell
   $TOKEN = (Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/auth/login" -Method Post -ContentType "application/json" -Body '{"email":"admin@example.com","password":"AdminSecurePassword123!"}').access_token
   $HEADERS = @{"Authorization" = "Bearer $TOKEN"}
   ```

3. **Start an Invoice Workflow**:
   ```powershell
   $WF = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/properties/prop-highland/workflows" -Method Post -Headers $HEADERS -ContentType "application/json" -Body '{"workflow_type":"invoice_processing","input_reference":"data/input/sample_invoice.pdf"}'
   Write-Host "Workflow created:" $WF.workflow_id "Status:" $WF.status
   ```
   *The workflow executes Document Analysis $\rightarrow$ Financial Ops $\rightarrow$ Policy Validation and pauses with `status: awaiting_approval`.*

4. **Approve the Proposed Expense**:
   ```powershell
   $APP_ID = $WF.approval_request_id
   Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/approvals/$APP_ID/decision" -Method Post -Headers $HEADERS -ContentType "application/json" -Body "{`"approval_id`":`"$APP_ID`",`"decision`":`"approve`",`"reason`":`"Verified invoice total.`"}"
   ```

5. **Resume Workflow Execution**:
   ```powershell
   $WF_RESUMED = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/properties/prop-highland/workflows/$($WF.workflow_id)/resume" -Method Post -Headers $HEADERS -ContentType "application/json" -Body '{"notes":"Proceed with payment recording."}'
   Write-Host "Final Workflow Status:" $WF_RESUMED.status
   ```

6. **Verify Tamper-Evident Audit Chain**:
   ```powershell
   $AUDIT = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/audit/verify" -Headers ($HEADERS + @{"X-Property-ID"="prop-highland"})
   Write-Host "Audit Chain Valid:" $AUDIT.valid
   ```
