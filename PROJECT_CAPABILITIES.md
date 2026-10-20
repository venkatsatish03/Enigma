# ENIGMA Platform Architecture & Capabilities Reference

## 1. Executive Summary

**ENIGMA** is an enterprise-grade, autonomous Property Management Artificial Intelligence orchestration platform. It is engineered with strict separation of duties, deterministic double-entry accounting verification, cryptographic audit trails, role-based access control (RBAC), and persistent multi-tenant domain storage.

```mermaid
graph TD
    Client[Client / Web Browser / API Caller] -->|Bearer JWT + RBAC| FastAPI[FastAPI Gateway (main.py)]
    
    subgraph Autonomous Agents
        DocAgent[Document Intelligence Agent]
        ResAgent[Resident & Lease Intelligence Agent]
        MaintAgent[Maintenance & Property Operations Agent]
        OrchAgent[Master Orchestrator Workflow Engine]
    end

    subgraph Core Services
        FinRepSvc[Financial Reporting Service]
        CompSvc[Compliance & SLA Service]
        NotifSvc[Multi-Channel Notification Service]
    end

    subgraph Mathematical & Algorithmic Tools
        OCR[Dual-Engine OCR: PyMuPDF + PaddleOCR]
        FinExt[Financial Table & Line Item Extractor]
        PayAlloc[Payment Allocator Engine]
        TxMatch[Bank Transaction Matcher Engine]
        FinAdapt[Domain Financial Record Adapter]
        ApprovalEng[Approval Workflow Engine]
        AuditEng[SHA-256 Hash-Chained Audit Trail]
    end

    subgraph Data Stores
        DomainDB[(domain.sqlite3: Invoices, Payments, Expenses, Leases, Units, Bank Statements)]
        AuthDB[(auth.sqlite3: Users, RBAC Roles, Property Memberships)]
        AuditDB[(audit.sqlite3: Immutable Tamper-Evident Hash Chain)]
        WorkflowsDB[(workflows.sqlite3: Durable Checkpoints & Locks)]
    end

    FastAPI --> DocAgent
    FastAPI --> ResAgent
    FastAPI --> MaintAgent
    FastAPI --> OrchAgent
    FastAPI --> FinRepSvc
    FastAPI --> CompSvc
    FastAPI --> NotifSvc

    DocAgent --> OCR
    DocAgent --> FinExt
    FinRepSvc --> DomainDB
    CompSvc --> DomainDB
    NotifSvc --> DomainDB
    PayAlloc --> DomainDB
    TxMatch --> DomainDB
    FinAdapt --> DomainDB
    ApprovalEng --> DomainDB
    AuditEng --> AuditDB
    FastAPI --> AuthDB
```

---

## 2. Autonomous Agents

### 2.1 Document Intelligence Agent (`agents/document_intelligence_agent.py`)
- **Capabilities**:
  - Ingests PDFs, scanned images, and document uploads.
  - Dual extraction pipelines: high-speed digital text extraction via PyMuPDF with automatic fallback to PaddleOCR for scanned or low-DPI documents.
  - Document categorization (invoices, receipts, leases, bank statements).
  - Mathematical line-item table parsing: verifies that `quantity * unit_price = line_total`, and `sum(line_totals) + tax = grand_total`.
  - Discrepancy flagging for corrupted numbers, currency mismatches, and date conflicts.

### 2.2 Resident & Lease Intelligence Agent (`agents/resident_lease_agent.py`)
- **Capabilities**:
  - Deterministic resident record deduplication (cross-checking names, emails, phone numbers, and lease assignments).
  - Missing field auditing (detects residents missing active leases, phone numbers, or emergency contacts).
  - Expiration risk analysis: detects active leases that have already passed end-dates without renewals.
  - Approaching expiration forecast window (configurable lookahead: 30, 60, or 90 days).
  - Overlap detection: prevents overlapping active leases on the same physical unit.

### 2.3 Maintenance & Property Operations Agent (`agents/maintenance_agent.py`)
- **Capabilities**:
  - Autonomous natural-language triage of resident maintenance issues.
  - Emergency safety fast-tracking: identifies gas leaks, electrical fires, structural flooding, and returns immediate resident containment instructions while prioritizing work orders.
  - Contractor auto-dispatch: scores vendors by specialty trade category, hourly rates, and current active queue workloads.
  - Move-in / move-out and periodic inspection checklist processing: auto-creates deficiency work orders for failed items.
  - Preventive maintenance cycle planning and SLA deadline escalation monitoring.
  - Maintenance budget variance and cost overrun analysis.

### 2.4 Master Orchestrator Agent (`agents/master_orchestrator.py`)
- **Capabilities**:
  - Durable multi-step workflow engine backed by SQLite persistence.
  - Workflow types: `invoice_processing`, `rent_reconciliation`, `financial_audit`, `maintenance_lifecycle`.
  - Worker concurrency locks, execution heartbeat monitoring, and bounded exponential backoff retries.
  - Human-in-the-loop pause gates: automatically pauses workflows when human approvals are required (`awaiting_approval`) and resumes upon approval resolution.

---

## 3. Core Domain Services

### 3.1 Financial Reporting Service (`services/financial_reporting_service.py`)
Calculates property-level accounting metrics and generates formal owner statements:
- **Payment Status Realization**: Only `SETTLED` and `RECONCILED` payments are counted toward realized `total_rent_collected` and `cash_inflows`. Unsettled payments (`PENDING`) are tracked in pipeline metrics (`pending_rent_amount`, `pending_payments_count`). `FAILED` and `REFUNDED` payments are excluded from realized cash inflows.
- **Collection Rate**: Computed as $(\text{rent\_collected} / \text{rent\_billed}) \times 100$. When `total_rent_billed == 0`, explicitly returns `None` (`null`) and exports as `"N/A"` in CSV spreadsheets.
- **Outstanding Rent Obligations**: Evaluated directly from invoice obligations minus payment allocations ($\sum \text{inv.outstanding\_amount}$ for rent invoices issued in the period), avoiding period mismatch and double counting.
- **NOI & Owner Distributions**:
  - Prevents double-deducting management fees if an expense with `category == ExpenseCategory.MANAGEMENT` is already present in operating expenses.
  - Configurable fee rate resolution: (1) Per-call override $\rightarrow$ (2) Per-property configured rate $\rightarrow$ (3) `ENIGMA_MANAGEMENT_FEE_RATE` env variable $\rightarrow$ (4) Default `0.08` (8%).
- **Multi-Currency Isolation**: All summaries and owner statements isolate records by target currency (default `"USD"`), preventing aggregation of incompatible currencies.
- **Spreadsheet Exports**: Generates CSV exports for property financial summaries and itemized owner statements.

### 3.2 Compliance Service (`services/compliance_service.py`)
- **Capabilities**:
  - Monitors property regulatory compliance (life safety, fire alarms, boiler certificates, elevator inspections, habitability).
  - Evaluates SLA deadlines on open maintenance requests and flags overdue violations.
  - Records compliance findings with assigned severity levels (`low`, `medium`, `high`, `critical`).
  - Manages finding remediation lifecycles (`open` $\rightarrow$ `in_progress` $\rightarrow$ `resolved`).

### 3.3 Notification Service (`services/notification_service.py`)
- **Capabilities**:
  - Multi-channel notification dispatch engine (Email, SMS, In-App).
  - Templating system for rent reminders, overdue rent warnings, maintenance dispatches, SLA escalation notices, and expense approval alerts.
  - Dry-run / preview simulation endpoint: allows testing rendered templates and recipient payloads without sending real outbound messages.
  - Idempotent delivery tracking to prevent duplicate notifications.

---

## 4. Algorithmic Tools & Mathematical Engines

### 4.1 Payment Allocator (`tools/payment_allocator.py`)
- Conservative, rule-based payment-to-invoice allocation engine.
- Matches payments to invoices using invoice reference numbers, exact amounts, currency compatibility, and party identifiers.
- Supports both full payments and partial payment splits.
- Prevents over-allocation and negative balance states.

### 4.2 Bank Transaction Matcher (`tools/transaction_matcher.py`)
- Double-entry bank statement reconciliation engine.
- Matches imported bank statement line items with recorded payments, invoices, and expense disbursements.
- Uses multi-factor confidence scoring:
  - Exact reference matching
  - Amount parity
  - Date proximity window ($\pm 3$ days)
  - Counterparty fuzzy text matching
- Categorizes match status into `matched`, `partially_matched`, `unmatched`, `discrepancy`, and `manual_review`.

### 4.3 Financial Record Adapter (`tools/financial_record_adapter.py`)
- Bidirectional bridge connecting Document Intelligence outputs and bank reconciliation schemas with persistent Domain Repository records (`invoices`, `payments`, `expenses`, `bank_transactions`).

### 4.4 Cryptographic Audit Trail (`tools/audit_trail.py`)
- Tamper-evident immutable ledger.
- Every financial allocation, expense approval, bank match, and compliance decision records an `AuditEvent`.
- Each event stores: `event_id`, `event_type`, `property_id`, `actor_id`, `payload`, `timestamp`, and `previous_hash`.
- Cryptographic verification endpoint (`/api/v1/audit/verify`) traverses the entire SHA-256 chain from genesis to head, immediately catching any database tampering or record deletion.

### 4.5 Human-in-the-Loop Approval Workflow (`tools/approval_workflow.py`)
- Property-scoped approval request lifecycle.
- Enforces Separation of Duties: the user who submitted an expense or action cannot approve it.
- Configurable Property Manager approval limit (`ENIGMA_MANAGER_EXPENSE_LIMIT`, default: `$1,000.00`). Expenses exceeding this limit require `admin` or `owner` authorization.

### 4.6 Authentication, Password Security & RBAC (`tools/auth_*.py`)
- Password hashing via **Argon2id** (memory-hard, resistant to GPU/ASIC cracking).
- JWT (JSON Web Tokens) with cryptographically secure HS256 signatures, expirations, and audience validation.
- Role-based permissions: `admin`, `owner`, `property_manager`, `accountant`, `maintenance_tech`, `resident`.
- Fine-grained permission flags: `READ_PROPERTY`, `WRITE_PROPERTY`, `FINANCIAL_OPERATIONS`, `MAINTENANCE_OPERATIONS`, `APPROVE_ACTIONS`, `VIEW_AUDIT_LOGS`, `ADMIN_SETTINGS`.

---

## 5. Database Architecture & Persistence Layers

The backend utilizes four isolated SQLite databases located in `data/`:

| Database File | Class / Repository | Managed Entities & Tables |
| :--- | :--- | :--- |
| `data/domain.sqlite3` | `DomainRepository` | `properties`, `units`, `residents`, `leases`, `invoices`, `payments`, `expenses`, `vendors`, `work_orders`, `maintenance_requests`, `sla_escalations`, `bank_statements`, `bank_transactions`, `compliance_rules`, `compliance_findings`, `notifications` |
| `data/auth.sqlite3` | `AuthRepository` | `users`, `properties`, `property_memberships`, `revoked_tokens` |
| `data/audit.sqlite3` | `AuditTrail` | `audit_events` (tamper-evident SHA-256 hash-chained ledger) |
| `data/workflows.sqlite3` | `WorkflowRepository` | `workflows`, `workflow_steps`, `workflow_checkpoints`, `workflow_locks` |

### Database Concurrency & Race Condition Protection
- All financial allocations (`record_invoice_payment`) and reconciliation decisions (`update_bank_transaction_match`) execute within explicit atomic SQLite transaction contexts:
  ```python
  with self.transaction() as conn:
      # Atomic balance check and update
  ```
- Prevents concurrent requests from overpaying invoices or matching the same payment to multiple bank statement lines.

---

## 6. Complete API Surface Inventory

### Authentication & Profiles
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/auth/login` | Authenticate user & issue JWT bearer token | Public |
| `GET` | `/api/v1/auth/me` | Retrieve authenticated user profile | Authenticated |
| `POST` | `/api/v1/auth/logout` | Revoke active bearer token | Authenticated |

### Document Intelligence
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/documents/analyze` | OCR text extraction & line-item table parsing | `WRITE_PROPERTY` |

### Financial Operations & Invoices
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/financial-operations` | Cross-document double-entry ledger matching | `FINANCIAL_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/invoices` | Create new rent or vendor invoice | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/invoices` | List invoices with status/type filters | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/invoices/{id}` | Retrieve individual invoice details | `FINANCIAL_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/invoices/{id}/payments` | Atomically allocate payment to invoice | `FINANCIAL_OPERATIONS` |

### Payments & Expenses
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/properties/{property_id}/payments` | Record payment received | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/payments` | List payments with status filtering | `FINANCIAL_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/expenses` | Record operating expense | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/expenses` | List expenses with category/status filters | `FINANCIAL_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/expenses/{id}/submit` | Submit expense for manager/owner approval | `FINANCIAL_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/expenses/{id}/decide` | Approve or reject expense with threshold check | `APPROVE_ACTIONS` |
| `POST` | `/api/v1/properties/{property_id}/expenses/check-duplicate` | Scan for potential duplicate expenses | `FINANCIAL_OPERATIONS` |

### Bank Statement Import & Reconciliation
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/properties/{property_id}/bank-statements/upload` | Import bank statement CSV with parsed lines | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/bank-statements` | List imported statements | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/bank-statements/transactions` | Query all bank transactions with match status | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/bank-statements/{id}` | Retrieve single statement details | `FINANCIAL_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/bank-statements/{id}/reconcile` | Execute automated reconciliation matching | `FINANCIAL_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/bank-statements/transactions/{tx_id}/decision` | Accept, reject, or correct transaction match | `FINANCIAL_OPERATIONS` |

### Financial Reporting
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/properties/{property_id}/reports/financial-summary` | Property financial summary report (JSON) | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/reports/financial-summary/csv` | Property financial summary spreadsheet (CSV) | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/reports/owner-statement` | Monthly/period owner distribution statement (JSON) | `FINANCIAL_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/reports/owner-statement/csv` | Itemized owner statement spreadsheet (CSV) | `FINANCIAL_OPERATIONS` |

### Compliance & SLA Monitoring
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/properties/{property_id}/compliance/audit` | Execute automated property compliance scan | `READ_PROPERTY` |
| `GET` | `/api/v1/properties/{property_id}/compliance/findings` | List open and resolved compliance findings | `READ_PROPERTY` |
| `POST` | `/api/v1/properties/{property_id}/compliance/findings/{id}/resolve` | Mark compliance finding resolved with notes | `WRITE_PROPERTY` |

### Multi-Channel Notifications
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/properties/{property_id}/notifications/preview` | Dry-run preview of templated notification | `READ_PROPERTY` |
| `POST` | `/api/v1/properties/{property_id}/notifications/send` | Dispatch notification via email/SMS/in-app | `WRITE_PROPERTY` |
| `GET` | `/api/v1/properties/{property_id}/notifications` | List dispatched notification history | `READ_PROPERTY` |

### Maintenance & Operations
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/properties/{property_id}/maintenance/triage` | Triage issue text & detect emergencies | `MAINTENANCE_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/maintenance/auto-dispatch` | Match & auto-dispatch contractor work order | `MAINTENANCE_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/maintenance/inspections` | Record inspection checklist & create deficiency orders | `MAINTENANCE_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/maintenance/operations-analysis`| Operations analysis & overdue SLA review | `MAINTENANCE_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/maintenance/cost-analysis` | Budget variance & cost overrun reporting | `MAINTENANCE_OPERATIONS` |
| `POST` | `/api/v1/properties/{property_id}/work-orders` | Create work order | `MAINTENANCE_OPERATIONS` |
| `GET` | `/api/v1/properties/{property_id}/work-orders` | List property work orders | `MAINTENANCE_OPERATIONS` |

### Resident & Lease Intelligence
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/properties/{property_id}/resident-lease-analysis` | Duplicate resident scan & lease expiration risk analysis | `READ_PROPERTY` |

### Master Orchestrator Workflows
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/properties/{property_id}/workflows` | Create and initialize workflow execution | `WRITE_PROPERTY` |
| `GET` | `/api/v1/properties/{property_id}/workflows/{id}` | Check workflow execution status and step history | `READ_PROPERTY` |
| `POST` | `/api/v1/properties/{property_id}/workflows/{id}/resume` | Resume workflow after approval gate resolution | `WRITE_PROPERTY` |

### Cryptographic Audit Trail
| Method | Route | Description | Required Permission |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/audit/events` | Query audit trail records | `VIEW_AUDIT_LOGS` |
| `GET` | `/api/v1/audit/verify` | Verify SHA-256 cryptographic chain integrity | `VIEW_AUDIT_LOGS` |

---

## 7. Audited Business Rules & Financial Formulas

### Rule 1: Realized vs. Unsettled Payments
$$\text{Realized Cash Inflow} = \sum_{\substack{p \in \text{payments} \\ p.\text{status} \in \{\text{SETTLED}, \text{RECONCILED}\}}} p.\text{amount}$$
- Unsettled payments with status `PENDING` are excluded from cash inflows and tracked separately under `pending_rent_amount` and `pending_payments_count`.
- Payments with status `FAILED` or `REFUNDED` are excluded from cash inflows.

### Rule 2: Collection Rate & Zero-Billed Handling
$$\text{Collection Rate} = \begin{cases} 
\left(\dfrac{\text{total\_rent\_collected}}{\text{total\_rent\_billed}} \times 100\right)\% & \text{if } \text{total\_rent\_billed} > 0 \\ 
\text{None (N/A)} & \text{if } \text{total\_rent\_billed} = 0 
\end{cases}$$

### Rule 3: Outstanding Rent Obligations
$$\text{Total Rent Outstanding} = \sum_{\substack{\text{inv} \in \text{invoices} \\ \text{inv}.\text{type} = \text{RENT}}} \max(0, \text{inv}.\text{amount} - \text{inv}.\text{amount\_paid})$$
- Calculated directly from actual invoice obligations minus allocations within the reporting period. Prevents period mismatch and double counting.

### Rule 4: Management Fee Double-Deduction Prevention & Owner Distributions
$$\text{NOI} = \text{Gross Rental Income} + \text{Other Income} - \text{Total Operating Expenses}$$
$$\text{Owner Distribution} = \begin{cases}
\max(0, \text{NOI} - \text{Reserve Withheld}) & \text{if } \text{Fee} \in \text{Operating Expenses} \\
\max(0, \text{NOI} - \text{Management Fee Amount} - \text{Reserve Withheld}) & \text{if } \text{Fee} \notin \text{Operating Expenses}
\end{cases}$$
- If an expense with `category == ExpenseCategory.MANAGEMENT` is present, it is already part of operating expenses. Deducting it again from NOI would be double-deduction.
- Configurable fee rate resolution: Parameter $\rightarrow$ Property Record $\rightarrow$ `ENIGMA_MANAGEMENT_FEE_RATE` $\rightarrow$ Default `0.08` (8%).

---

## 8. Test Suites & Verification Coverage

The backend test suite is composed of **332 automated tests** across 9 test modules with a **100% pass rate**:

1. **`tests/test_verification_suite.py`** (18 tests):
   - Payment status realization (`settled`, `pending`, `failed`, `refunded`)
   - Zero-billed period collection rate N/A verification
   - Outstanding rent obligation-based calculations
   - Management fee included vs excluded double-deduction prevention
   - Configurable management fee rate resolution
   - Multi-currency isolation
   - Route ordering verification (`/bank-statements/transactions` before dynamic parameters)
   - Concurrent payment allocation race condition protection
   - Existing database safe schema migration
   - Property manager expense limit configuration (`ENIGMA_MANAGER_EXPENSE_LIMIT`)
   - Tamper-evident audit trail persistence across all financial actions
2. **`tests/test_finance_compliance_suite.py`** (12 tests):
   - Invoices & payment allocation lifecycle
   - Bank statement CSV parsing & auto-reconciliation
   - Reconciliation match decision flows
   - Expense approval threshold enforcement
   - Duplicate expense detection
   - Compliance auditing & SLA escalations
   - Multi-channel notification templates & preview simulation
   - Financial reporting summary & owner statements
   - Property-level data isolation
   - Financial record adapter integration
3. **`tests/test_maintenance_agent.py`** (28 tests):
   - Triage, emergency safety instructions, contractor dispatching, inspection checklists.
4. **`tests/test_resident_lease_agent.py`** (26 tests):
   - Resident deduplication, lease expiration risk analysis, overlap detection.
5. **`tests/test_master_orchestrator.py`** (38 tests):
   - Workflow checkpoints, worker locks, recovery, pause/resume approval gates.
6. **`tests/test_auth_and_property_authorization.py`** (42 tests):
   - Argon2id hashing, JWT lifecycles, RBAC role permissions, cross-tenant isolation.
7. **`tests/test_domain_repository.py`** (54 tests):
   - Relational CRUD operations across all 16 domain tables.
8. **`tests/test_comprehensive_audit_regression.py`** (46 tests):
   - Cryptographic hash-chain validation across Scenarios A through G.
9. **`tests/test_ocr_and_extractors.py`** (68 tests):
   - PyMuPDF, PaddleOCR, line-item arithmetic verification, currency extraction.
