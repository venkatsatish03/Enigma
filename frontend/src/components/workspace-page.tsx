"use client";

import Link from "next/link";
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { apiRequest, downloadCsv, displayValue, formatMoney, propertyPath, queryString, recordId } from "@/lib/api";
import { useSession } from "@/context/session";
import type { JsonRecord } from "@/types";

type ResourceState<T> = { data: T | null; loading: boolean; error: string; refresh: () => void };
function useResource<T>(path: string, enabled = true): ResourceState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((current) => current + 1), []);
  useEffect(() => {
    if (!enabled) { setLoading(false); return; }
    let live = true;
    setLoading(true);
    setError("");
    apiRequest<T>(path).then((result) => { if (live) setData(result); }).catch((reason: unknown) => {
      if (live) setError(reason instanceof Error ? reason.message : "Could not load data.");
    }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [path, enabled, revision]);
  return { data, loading, error, refresh };
}

function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const run = useCallback(async (action: () => Promise<unknown>, successMessage = "Changes saved.") => {
    setBusy(true); setError(""); setSuccess("");
    try { const result = await action(); setSuccess(successMessage); return result; }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The action could not be completed."); return null; }
    finally { setBusy(false); }
  }, []);
  return { busy, error, success, run, clear: () => { setError(""); setSuccess(""); } };
}

function formValues(form: HTMLFormElement): JsonRecord {
  const values: JsonRecord = {};
  new FormData(form).forEach((value, key) => {
    if (typeof value === "string" && value.trim()) values[key] = value;
  });
  return values;
}

function SectionHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) {
  return <div className="page-heading"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{actions && <div className="heading-actions">{actions}</div>}</div>;
}

function Panel({ title, subtitle, action, children, className = "" }: { title?: string; subtitle?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{(title || action) && <div className="panel-head"><div>{title && <h2>{title}</h2>}{subtitle && <p>{subtitle}</p>}</div>{action}</div>}{children}</section>;
}

function Loading({ text = "Loading records…" }: { text?: string }) { return <div className="loading-state"><span className="spinner spinner-dark" />{text}</div>; }
function ErrorBox({ error, retry }: { error: string; retry?: () => void }) { return <div className="error-state"><strong>Could not load this information</strong>{error}{retry && <button className="table-link" style={{ marginLeft: 10 }} onClick={retry}>Try again</button>}</div>; }
function Empty({ title, text }: { title: string; text: string }) { return <div className="empty-state"><div className="empty-icon">⌁</div><h3>{title}</h3><p>{text}</p></div>; }
function Status({ value }: { value: unknown }) { const label = displayValue(value); const key = label.toLowerCase().replaceAll(" ", "_"); return <span className={`status-badge status-${key}`}>{label.replaceAll("_", " ")}</span>; }

function DataTable({ rows, columns, onOpen, actions, emptyTitle = "Nothing here yet", emptyText = "Records will appear here when they are available." }: { rows: JsonRecord[]; columns: string[]; onOpen?: (row: JsonRecord) => void; actions?: (row: JsonRecord) => ReactNode; emptyTitle?: string; emptyText?: string }) {
  if (!rows.length) return <Empty title={emptyTitle} text={emptyText} />;
  return <div className="table-wrap"><table className="data-table"><thead><tr>{columns.map((key) => <th key={key}>{key.replaceAll("_", " ")}</th>)}{(onOpen || actions) && <th>Actions</th>}</tr></thead><tbody>{rows.map((row, index) => <tr key={recordId(row) || index}>
    {columns.map((key) => <td key={key}>{key.includes("status") || key === "severity" || key === "priority" || key === "match_status" ? <Status value={row[key]} /> : <span title={typeof row[key] === "string" ? row[key] as string : undefined}>{displayValue(row[key])}</span>}</td>)}
    {(onOpen || actions) && <td><div className="table-actions">{onOpen && <button className="table-link" onClick={() => onOpen(row)}>Open</button>}{actions?.(row)}</div></td>}
  </tr>)}</tbody></table></div>;
}

function DetailModal({ title, value, onClose, children }: { title: string; value?: unknown; onClose: () => void; children?: ReactNode }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-label={title}><div className="modal-head"><h2>{title}</h2><button className="modal-close" onClick={onClose} aria-label="Close">×</button></div><div className="modal-content">{children ?? <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>}</div></section></div>;
}

function IconAction({ href, icon, label }: { href: string; icon: string; label: string }) {
  return <Link className="quick-action" href={href}><span>{icon}</span><span>{String(label)}</span></Link>;
}

type Summary = {
  currency?: string; total_rent_billed?: string | number; total_rent_collected?: string | number; total_rent_outstanding?: string | number; collection_rate_percentage?: string | number | null; pending_rent_amount?: string | number; pending_payments_count?: number; total_expenses?: string | number; net_operating_income?: string | number; reconciliation_discrepancies_count?: number;
};

function Dashboard() {
  const { propertyId, activeMembership } = useSession();
  const base = propertyPath("");
  const [data, setData] = useState<{ summary: Summary | null; approvals: JsonRecord[]; findings: JsonRecord[]; requests: JsonRecord[]; escalations: JsonRecord[]; workflows: JsonRecord[]; events: JsonRecord[] }>({ summary: null, approvals: [], findings: [], requests: [], escalations: [], workflows: [], events: [] });
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [range, setRange] = useState("month");
  const [rangeStart, rangeEnd] = useMemo(() => {
    const end = new Date(); const start = new Date();
    if (range === "month") start.setDate(1);
    if (range === "quarter") start.setMonth(Math.floor(start.getMonth() / 3) * 3, 1);
    if (range === "year") start.setMonth(0, 1);
    return [start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)];
  }, [range]);
  useEffect(() => {
    let live = true;
    setLoading(true); setErrors([]);
    const tasks: [keyof typeof data, Promise<unknown>][] = [
      ["summary", apiRequest<Summary>(`${base}/reports/financial-summary${queryString({ start_date: rangeStart, end_date: rangeEnd })}`)],
      ["approvals", apiRequest<JsonRecord[]>("/approvals?status=pending&limit=10")],
      ["findings", apiRequest<JsonRecord[]>(`${base}/compliance/findings?status=open&limit=10`)],
      ["requests", apiRequest<JsonRecord[]>(`${base}/maintenance-requests?limit=10`)],
      ["escalations", apiRequest<JsonRecord[]>(`${base}/sla/escalations?limit=10`)],
      ["workflows", apiRequest<JsonRecord[]>(`${base}/workflows?limit=10`)],
      ["events", apiRequest<JsonRecord[]>("/audit/events?limit=6")],
    ];
    Promise.all(tasks.map(async ([key, promise]) => [key, await promise] as const).map((task, index) => task.catch((error: unknown) => [tasks[index][0], error] as const)))
      .then((results) => {
        if (!live) return;
        const next = { summary: null, approvals: [], findings: [], requests: [], escalations: [], workflows: [], events: [] } as typeof data;
        const failed: string[] = [];
        results.forEach(([key, value]) => {
          if (value instanceof Error) failed.push(`${String(key)}: ${value.message}`);
          else (next as JsonRecord)[key] = value as never;
        });
        setData(next); setErrors(failed);
      })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [base, rangeStart, rangeEnd]);
  const summary = data.summary;
  const pendingItems = [
    ...data.approvals.map((item) => ({ title: String(item.action_type ?? "Approval request"), meta: `${formatMoney(item.amount, String(item.currency ?? "USD"))} · submitted by ${item.requested_by ?? "Unknown"}`, type: "Approval", href: "/approvals" })),
    ...data.findings.slice(0, 4).map((item) => ({ title: String(item.title ?? "Compliance finding"), meta: `${displayValue(item.severity)} · ${displayValue(item.category)}`, type: "Finding", href: "/compliance" })),
    ...data.escalations.slice(0, 3).map((item) => ({ title: `${displayValue(item.escalation_type)} maintenance SLA`, meta: `Due ${displayValue(item.sla_deadline_at)}`, type: "SLA", href: "/maintenance" })),
  ].slice(0, 6);
  const openRequests = data.requests.filter((row) => !["completed", "cancelled", "closed"].includes(String(row.status ?? "").toLowerCase())).slice(0, 5);
  const currency = summary?.currency ?? "USD";
  return <>
    <SectionHeader eyebrow="PROPERTY OVERVIEW" title={activeMembership?.property_name || propertyId || "Property"} description="A clear view of what is happening across your property." actions={<><select className="select" value={range} onChange={(event) => setRange(event.target.value)} aria-label="Date range"><option value="month">This month</option><option value="quarter">This quarter</option><option value="year">This year</option></select><Link className="button button-primary" href="/documents">＋ Upload document</Link></>} />
    {errors.length > 0 && <div className="inline-note">Some dashboard panels are unavailable for your current permissions or backend state. Available panels are still shown.</div>}
    <div className="grid grid-4">
      <Metric label="Rent billed" value={summary ? formatMoney(summary.total_rent_billed, currency) : "—"} note="In selected period" icon="$" />
      <Metric label="Collected" value={summary ? formatMoney(summary.total_rent_collected, currency) : "—"} note={`Pending ${formatMoney(summary?.pending_rent_amount ?? 0, currency)}`} icon="↗" />
      <Metric label="Outstanding" value={summary ? formatMoney(summary.total_rent_outstanding, currency) : "—"} note={`${summary?.pending_payments_count ?? 0} pending payments`} icon="◷" />
      <Metric label="Collection rate" value={summary?.collection_rate_percentage == null ? "N/A" : `${Number(summary.collection_rate_percentage).toFixed(1)}%`} note="Based on billed rent" icon="◎" progress={summary?.collection_rate_percentage == null ? 0 : Number(summary.collection_rate_percentage)} />
    </div>
    {loading && <Loading text="Loading property activity…" />}
    <div className="quick-actions">{activeMembership?.permissions.includes("documents:analyze") && <IconAction href="/documents" icon="▤" label="Analyze document" />}{activeMembership?.permissions.includes("financial:operations") && <><IconAction href="/finance?tab=reconciliation" icon="↔" label="Import statement" /><IconAction href="/finance?tab=invoices" icon="$" label="Create invoice" /></>}{activeMembership?.permissions.includes("property:manage") && <IconAction href="/maintenance" icon="⌁" label="New maintenance request" />}</div>
    <div className="dashboard-grid">
      <Panel title="Needs attention" subtitle="Items that may need a decision or follow-up" action={<Link className="table-link" href="/work-queue">View work queue →</Link>}>
        <div className="action-list">{pendingItems.length ? pendingItems.map((item, index) => <div className="action-row" key={`${item.type}-${index}`}><span className="action-marker">{item.type === "Approval" ? "✓" : item.type === "SLA" ? "!" : "◈"}</span><div className="action-copy"><strong>{item.title}</strong><span>{item.meta}</span><Link href={item.href}>Open {item.type.toLowerCase()} →</Link></div><Status value="open" /></div>) : <Empty title="You are all caught up" text="New approvals, findings, and escalations will appear here." />}</div>
      </Panel>
      <Panel title="Maintenance & SLA" subtitle="Open requests and response deadlines" action={<Link className="table-link" href="/maintenance">View maintenance →</Link>}>
        <div className="action-list">{openRequests.length ? openRequests.map((row) => <div className="action-row" key={recordId(row)}><span className="action-marker">⌁</span><div className="action-copy"><strong>{String(row.category ?? row.description ?? "Maintenance request")}</strong><span>Unit {displayValue(row.unit_id)} · {displayValue(row.priority)} priority</span><span>Reported {displayValue(row.created_at)}</span></div><Status value={row.status} /></div>) : <Empty title="No open requests" text="New maintenance requests will appear here." />}</div>
      </Panel>
      <Panel title="Workflow activity" subtitle="Progress across long-running work" action={<Link className="table-link" href="/workflows">View workflows →</Link>}>
        <div className="action-list">{data.workflows.length ? data.workflows.slice(0, 5).map((row) => <div className="action-row" key={recordId(row)}><span className="action-marker">⟳</span><div className="action-copy"><strong>{String(row.workflow_type ?? "Workflow").replaceAll("_", " ")}</strong><span>{displayValue(row.current_step)} · updated {displayValue(row.updated_at)}</span></div><Status value={row.status} /></div>) : <Empty title="No active workflows" text="Started workflows and their current step will appear here." />}</div>
      </Panel>
      <Panel title="Recent activity" subtitle="Latest recorded actions" action={<Link className="table-link" href="/audit">View audit →</Link>}>
        <div className="action-list">{data.events.length ? data.events.slice(0, 5).map((row) => <div className="action-row" key={recordId(row)}><span className="action-marker">⌕</span><div className="action-copy"><strong>{String(row.event_type ?? "Activity").replaceAll(".", " · ").replaceAll("_", " ")}</strong><span>{displayValue(row.actor_id)} · {displayValue(row.created_at)}</span></div></div>) : <Empty title="No recent events" text="Actions recorded in the audit trail will appear here." />}</div>
      </Panel>
    </div>
  </>;
}

function Metric({ label, value, note, icon, progress }: { label: string; value: string; note: string; icon: string; progress?: number }) {
  return <div className="metric-card"><div className="metric-top"><span>{String(label)}</span><span className="metric-icon">{icon}</span></div><div><div className="metric-value">{value}</div><div className="metric-note">{note}</div>{progress !== undefined && <div className="progress-track"><div className="progress-value" style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} /></div>}</div></div>;
}

function WorkQueue() {
  const base = propertyPath("");
  const searchParams = useSearchParams();
  const query = (searchParams.get("q") ?? "").toLowerCase();
  const [filter, setFilter] = useState("All items");
  const [rows, setRows] = useState<JsonRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [errors, setErrors] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    const specs: [string, string, string][] = [
      ["Approvals", "/approvals?status=pending&limit=100", "approval"],
      ["Paused workflows", `${base}/workflows?limit=100`, "workflow"],
      ["Compliance", `${base}/compliance/findings?status=open&limit=100`, "compliance"],
      ["Maintenance/SLA", `${base}/sla/escalations?status=open&limit=100`, "sla"],
      ["Reconciliation review", `${base}/bank-statements/transactions?match_status=manual_review&limit=100`, "reconciliation"],
    ];
    setLoading(true); setErrors([]);
    Promise.all(specs.map(async ([group, path, kind]) => {
      try { const result = await apiRequest<JsonRecord[]>(path); return (result ?? []).filter((row) => kind !== "workflow" || ["awaiting_approval", "needs_review"].includes(String(row.status))).map((row) => ({ ...row, _group: group, _kind: kind })); }
      catch (error) { return { error: `${group}: ${error instanceof Error ? error.message : "Unavailable"}` }; }
    })).then((results) => {
      if (!live) return;
      const failed = results.flatMap((result) => Array.isArray(result) ? [] : [result.error]);
      const all = results.flatMap((result) => Array.isArray(result) ? result : []);
      setErrors(failed); setRows(all);
    }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [base, revision]);
  const visible = rows.filter((row) => (filter === "All items" || row._group === filter) && (!query || JSON.stringify(row).toLowerCase().includes(query)));
  const columns = ["_group", "title", "action_type", "description", "severity", "status", "amount", "requested_by", "created_at"].filter((column) => visible.some((row) => row[column] !== undefined)).slice(0, 6);
  const filters = ["All items", "Approvals", "Paused workflows", "Maintenance/SLA", "Compliance", "Reconciliation review"];
  return <>
    <SectionHeader eyebrow="HUMAN REVIEW" title="Work queue" description="Human decisions and operational follow-ups from across your property." actions={<button className="button button-secondary" onClick={() => setRevision((value) => value + 1)}>↻ Refresh queue</button>} />
    {errors.length > 0 && <div className="inline-note">Some queue sources are unavailable for your role or backend state. Items from available sources are still shown.</div>}
    <Panel title={`${visible.length} items`} subtitle="Select a category to focus the queue">
      <div className="tabs">{filters.map((item) => <button key={item} className={`tab ${filter === item ? "tab-active" : ""}`} onClick={() => setFilter(item)}>{item}</button>)}</div>
      {loading ? <Loading text="Combining review items…" /> : <DataTable rows={visible} columns={columns.length ? columns : ["_group", "status", "created_at"]} onOpen={(row) => { window.location.href = row._kind === "approval" ? "/approvals" : row._kind === "sla" || row._kind === "maintenance" ? "/maintenance" : row._kind === "reconciliation" ? "/finance?tab=reconciliation" : row._kind === "compliance" ? "/compliance" : "/workflows"; }} emptyTitle="No items in this view" emptyText="There is nothing waiting for review in this category." />}
    </Panel>
  </>;
}

function Approvals() {
  const resource = useResource<JsonRecord[]>("/approvals?status=pending&limit=250");
  const { profile, activeMembership } = useSession();
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const [reason, setReason] = useState("");
  const action = useAction();
  const rows = resource.data ?? [];
  const canDecide = activeMembership?.permissions.includes("approvals:decide") ?? false;
  const selectedId = selected ? String(selected.approval_id ?? "") : "";
  const isRequester = selected?.requested_by === profile?.user_id;
  async function decide(decision: "approve" | "reject") {
    if (!selectedId || !reason.trim()) return;
    const result = await action.run(() => apiRequest(`/approvals/${encodeURIComponent(selectedId)}/decision`, { method: "POST", body: JSON.stringify({ decision, reason: reason.trim() }) }), `Approval ${decision === "approve" ? "approved" : "rejected"}.`);
    if (result) { setSelected(null); setReason(""); resource.refresh(); }
  }
  return <>
    <SectionHeader eyebrow="HUMAN REVIEW" title="Approval center" description="Review proposed actions with their rationale, evidence, and decision history." />
    {action.error && <div className="error-state">{action.error}</div>}{action.success && <div className="inline-success">{action.success}</div>}
    <Panel title="Pending approvals" subtitle="The API enforces approver permissions, thresholds, and separation of duties.">
      {resource.loading ? <Loading /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <DataTable rows={rows} columns={["action_type", "amount", "currency", "requested_by", "created_at", "status"]} onOpen={(row) => { setSelected(row); setReason(""); }} emptyTitle="No approvals waiting" emptyText="New requests that need a decision will appear here." />}
    </Panel>
    {selected && <DetailModal title="Review approval" value={selected} onClose={() => setSelected(null)}>
      <div className="detail-grid">{[["Action", selected.action_type], ["Amount", selected.amount ? formatMoney(selected.amount, String(selected.currency ?? "USD")) : "Not specified"], ["Requester", selected.requested_by], ["Submitted", selected.created_at], ["Expires", selected.expires_at], ["Status", selected.status]].map(([label, value]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{displayValue(value)}</strong></div>)}</div>
      <div className="panel" style={{ marginTop: 13 }}><div className="panel-head"><h3>Rationale</h3></div><div className="panel-body" style={{ color: "#566c73", fontSize: 10, lineHeight: 1.6 }}>{displayValue(selected.rationale)}</div></div>
      {!!selected.evidence_ids && <div className="inline-note">Evidence references: {displayValue(selected.evidence_ids)}</div>}
      {isRequester && <div className="inline-note warning">You requested this action. The server prevents self-approval, so decision controls are unavailable.</div>}
      {!canDecide && !isRequester && <div className="inline-note warning">Your property role can view this request but cannot decide it.</div>}
      {canDecide && !isRequester && selected.status === "pending" && <div style={{ marginTop: 14 }}><div className="form-field"><label htmlFor="approval-reason">Decision reason (required)</label><textarea className="textarea" id="approval-reason" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Explain the decision for the audit history." /></div><div className="form-actions"><button className="button button-danger" disabled={action.busy || !reason.trim()} onClick={() => void decide("reject")}>Reject</button><button className="button button-primary" disabled={action.busy || !reason.trim()} onClick={() => void decide("approve")}>{action.busy ? "Saving…" : "Approve"}</button></div></div>}
      {action.error && <div className="error-state">{action.error}</div>}
    </DetailModal>}
  </>;
}

function Documents() {
  const { activeMembership } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  async function analyze() {
    if (!file) return;
    setBusy(true); setError(""); setAnalysis(null);
    const form = new FormData(); form.append("file", file);
    try { setAnalysis(await apiRequest("/documents/analyze", { method: "POST", body: form })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Document analysis failed."); }
    finally { setBusy(false); }
  }
  return <>
    <SectionHeader eyebrow="DOCUMENT INTELLIGENCE" title="Documents" description="Analyze invoices and supporting PDFs, then review extraction findings before using them in financial operations." actions={<span className="status-badge status-simulated">Review before creating records</span>} />
    <div className="split-layout">
      <Panel title="Analyze a document" subtitle={`Property: ${activeMembership?.property_name ?? "Active property"}`}>
        <div className="panel-body">
          <div className="warning-banner"><span>!</span><span>Analysis is temporary. The current API does not save the original upload or a durable correction history. Review the result here before taking any financial action.</span></div>
          <label className={`drop-zone ${dragOver ? "drop-over" : ""}`} htmlFor="document-file" onDragOver={(event) => { event.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)} onDrop={(event) => { event.preventDefault(); setDragOver(false); const next = event.dataTransfer.files?.[0]; if (next) setFile(next); }}>
            <span className="drop-zone-icon">⇧</span><strong>Drop a PDF here or browse files</strong><span>PDF only · Up to 20 MB</span><input className="file-input" id="document-file" type="file" accept="application/pdf,.pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </label>
          {file && <div className="file-chip"><span>▤</span><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(2)} MB</span><button className="table-link" onClick={() => setFile(null)}>Remove</button></div>}
          {error && <div className="error-state">{error}</div>}
          <div className="form-actions" style={{ marginTop: 13 }}><button className="button button-primary" disabled={!file || busy} onClick={analyze}>{busy ? <><span className="spinner" />Analyzing…</> : "Analyze document"}</button></div>
        </div>
      </Panel>
      <Panel title="Review checklist" subtitle="Check these before you create or approve financial records.">
        <div className="panel-body"><div className="insight-list">{["Confirm vendor or resident identity", "Check invoice number, dates, currency, and totals", "Review warnings, missing fields, and arithmetic checks", "Verify every extracted value against the PDF", "Use a durable document reference for workflows"].map((item, index) => <div className="insight" key={item}><span className="insight-dot" style={{ background: index === 4 ? "#8d84bf" : undefined }} /><span>{item}</span></div>)}</div><div className="inline-note warning">Creating a financial record from this browser upload is disabled because the API does not yet return a saved document ID or correction workflow.</div></div>
      </Panel>
    </div>
    {analysis !== null && <Panel title="Extraction result" subtitle="Inspect the agent output and confirm the source evidence before proceeding." className="" ><div className="panel-body"><pre className="json-view">{JSON.stringify(analysis, null, 2)}</pre><div className="form-actions" style={{ marginTop: 12 }}><button className="button button-secondary" disabled={busy} onClick={analyze}>Re-analyze</button></div></div></Panel>}
  </>;
}

type FinanceTab = "invoices" | "payments" | "expenses" | "reconciliation" | "reports";
const financeTabs: { id: FinanceTab; label: string }[] = [
  { id: "invoices", label: "Invoices" }, { id: "payments", label: "Payments" }, { id: "expenses", label: "Expenses" }, { id: "reconciliation", label: "Reconciliation" }, { id: "reports", label: "Reports" },
];

function Finance() {
  const [tab, setTab] = useState<FinanceTab>("invoices");
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const [revision, setRevision] = useState(0);
  const [reason, setReason] = useState("");
  const [correctEntityType, setCorrectEntityType] = useState("payment");
  const [correctEntityId, setCorrectEntityId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [duplicateResult, setDuplicateResult] = useState<JsonRecord | null>(null);
  const [allocationAmount, setAllocationAmount] = useState("");
  const [editInvoice, setEditInvoice] = useState(false);
  const action = useAction();
  const paths: Record<FinanceTab, string> = {
      invoices: `${propertyPath("/invoices?limit=250")}&__refresh=${revision}`, payments: `${propertyPath("/payments?limit=250")}&__refresh=${revision}`, expenses: `${propertyPath("/expenses?limit=250")}&__refresh=${revision}`,
    reconciliation: `${propertyPath("/bank-statements/transactions?limit=250")}&__refresh=${revision}`, reports: propertyPath("/reports/financial-summary"),
  };
  const resource = useResource<JsonRecord[] | JsonRecord>(paths[tab], tab !== "reports");
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("tab");
    if (requested && financeTabs.some((item) => item.id === requested)) setTab(requested as FinanceTab);
  }, []);
  const rows = Array.isArray(resource.data) ? resource.data : [];
  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = formValues(event.currentTarget);
    const path = { invoices: propertyPath("/invoices"), payments: propertyPath("/payments"), expenses: propertyPath("/expenses") }[tab as "invoices" | "payments" | "expenses"];
    if (!path) return;
    const result = await action.run(() => apiRequest(path, { method: "POST", body: JSON.stringify(data) }), `${tab.slice(0, -1)} created.`);
    if (result) { setCreateOpen(false); setRevision((value) => value + 1); }
  }
  async function checkDuplicateExpense() {
    const form = document.getElementById("create-record") as HTMLFormElement | null;
    if (!form) return;
    const data = formValues(form);
    const result = await action.run(() => apiRequest(propertyPath("/expenses/check-duplicate"), { method: "POST", body: JSON.stringify(data) }), "Duplicate check completed.");
    if (result) setDuplicateResult(result as JsonRecord);
  }
  async function saveInvoiceEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected) return;
    const data = formValues(event.currentTarget);
    const id = String(selected.invoice_id);
    const result = await action.run(() => apiRequest(propertyPath(`/invoices/${encodeURIComponent(id)}`), { method: "PATCH", body: JSON.stringify(data) }), "Invoice details updated.");
    if (result) { setSelected(result as JsonRecord); setEditInvoice(false); setRevision((value) => value + 1); }
  }
  async function allocateInvoicePayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected || !allocationAmount) return;
    const id = String(selected.invoice_id);
    const result = await action.run(() => apiRequest(propertyPath(`/invoices/${encodeURIComponent(id)}/payments`), { method: "POST", body: JSON.stringify({ amount: Number(allocationAmount) }) }), "Payment allocated to invoice.");
    if (result) { setSelected(result as JsonRecord); setAllocationAmount(""); setRevision((value) => value + 1); }
  }
  async function importStatement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    const body = new FormData(); body.append("file", file);
    const result = await action.run(() => apiRequest(propertyPath("/bank-statements/import"), { method: "POST", body }), "Statement imported and matching completed.");
    if (result) { setShowImport(false); setFile(null); setRevision((value) => value + 1); }
  }
  async function expenseAction(expense: JsonRecord, decision?: "approve" | "reject") {
    const id = String(expense.expense_id);
    const path = propertyPath(`/expenses/${encodeURIComponent(id)}/${decision ? "decide" : "submit"}`);
    if (decision) {
      const result = await action.run(() => apiRequest(path, { method: "POST", body: JSON.stringify({ decision, reason: reason.trim() }) }), `Expense ${decision}d.`);
      if (result) { setSelected(null); setRevision((value) => value + 1); }
    } else {
      const result = await action.run(() => apiRequest(path, { method: "POST" }), "Expense submitted for approval.");
      if (result) setRevision((value) => value + 1);
    }
  }
  async function matchDecision(decision: "approve" | "reject" | "correct") {
    if (!selected) return;
    const id = String(selected.transaction_id);
    const payload: JsonRecord = { decision, notes: reason.trim() };
    if (decision === "correct") { payload.matched_entity_type = correctEntityType; payload.matched_entity_id = correctEntityId.trim(); }
    const result = await action.run(() => apiRequest(propertyPath(`/bank-statements/transactions/${encodeURIComponent(id)}/decide`), { method: "POST", body: JSON.stringify(payload) }), `Match ${decision}d.`);
    if (result) { setSelected(null); setRevision((value) => value + 1); }
  }
  const columnsByTab: Record<Exclude<FinanceTab, "reports">, string[]> = {
    invoices: ["invoice_number", "invoice_type", "resident_id", "due_date", "amount", "amount_paid", "currency", "status"],
    payments: ["payment_date", "resident_id", "reference_number", "amount", "currency", "payment_method", "status"],
    expenses: ["expense_date", "category", "description", "amount", "currency", "approval_status", "status"],
    reconciliation: ["transaction_date", "description", "reference", "amount", "currency", "match_status", "match_confidence"],
  };
  return <>
    <SectionHeader eyebrow="FINANCIAL OPERATIONS" title="Finance" description="Track obligations, receipts, expenses, and bank matches for this property." actions={tab === "reconciliation" ? <button className="button button-primary" onClick={() => { action.clear(); setShowImport(true); }}>＋ Import statement</button> : tab !== "reports" ? <button className="button button-primary" onClick={() => { action.clear(); setCreateOpen(true); }}>＋ Create {tab.slice(0, -1)}</button> : undefined} />
    {(action.error || action.success) && <div className={action.error ? "error-state" : "inline-success"}>{action.error || action.success}</div>}
    <Panel>
      <div className="tabs">{financeTabs.map((item) => <button className={`tab ${tab === item.id ? "tab-active" : ""}`} onClick={() => setTab(item.id)} key={item.id}>{item.label}</button>)}</div>
      {tab === "reports" ? <FinancialReport /> : <>
        {tab === "reconciliation" && <div className="toolbar"><select className="select" aria-label="Reconciliation status"><option>All matches</option><option>Matched</option><option>Partial</option><option>Unmatched</option><option>Discrepancy</option><option>Manual review</option></select><span className="toolbar-spacer" /><Link href="/reports" className="table-link">Financial reports →</Link></div>}
        {resource.loading ? <Loading /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <DataTable rows={rows} columns={columnsByTab[tab as Exclude<FinanceTab, "reports">]} onOpen={(row) => { setSelected(row); setAllocationAmount(""); setEditInvoice(false); }} actions={tab === "expenses" ? (row) => ["draft", "returned"].includes(String(row.approval_status)) ? <button className="table-link" onClick={() => void expenseAction(row)}>Submit</button> : null : tab === "reconciliation" ? (row) => <button className="table-link" onClick={() => { setSelected(row); setReason(""); setCorrectEntityId(""); }}>Review</button> : tab === "invoices" ? (row) => <button className="table-link" onClick={() => { setSelected(row); setAllocationAmount(""); }}>Record payment</button> : undefined} emptyTitle={`No ${tab} yet`} emptyText={tab === "reconciliation" ? "Import a statement CSV to start matching transactions." : `New ${tab} will appear here.`} />}
      </>}
    </Panel>
    {createOpen && <DetailModal title={`Create ${tab.slice(0, -1)}`} onClose={() => setCreateOpen(false)}><form id="create-record" className="form-grid" onSubmit={submitCreate}>
      {tab === "invoices" && <><Field label="Invoice number" name="invoice_number" required placeholder="INV-2026-001" /><Field label="Type" name="invoice_type" type="select" options={["rent", "maintenance", "utility", "fee", "other"]} /><Field label="Amount" name="amount" type="number" required /><Field label="Currency" name="currency" defaultValue="USD" required /><Field label="Issue date" name="issue_date" type="date" required /><Field label="Due date" name="due_date" type="date" required /><Field label="Resident ID" name="resident_id" /><Field label="Lease ID" name="lease_id" /><Field label="Description" name="description" className="full" /></>}
      {tab === "payments" && <><Field label="Amount" name="amount" type="number" required /><Field label="Currency" name="currency" defaultValue="USD" required /><Field label="Payment date" name="payment_date" type="date" required /><Field label="Payment method" name="payment_method" type="select" options={["ach", "check", "credit_card", "bank_transfer", "wire", "cash"]} /><Field label="Resident ID" name="resident_id" /><Field label="Lease ID" name="lease_id" /><Field label="Reference number" name="reference_number" className="full" /></>}
      {tab === "expenses" && <><Field label="Category" name="category" type="select" options={["repairs", "maintenance", "utilities", "insurance", "taxes", "management", "supplies", "other"]} /><Field label="Amount" name="amount" type="number" required /><Field label="Currency" name="currency" defaultValue="USD" required /><Field label="Expense date" name="expense_date" type="date" required /><Field label="Vendor ID" name="vendor_id" /><Field label="Unit ID" name="unit_id" /><Field label="Description" name="description" required className="full" /></>}
      {action.error && <div className="error-state form-field full">{action.error}</div>}
      {duplicateResult?.is_duplicate === true && <div className="warning-banner form-field full">A matching expense was found. Existing expense ID: {displayValue(duplicateResult.duplicate_expense_id)}. Review before creating another record.</div>}
    </form><div className="modal-footer"><button className="button button-secondary" onClick={() => setCreateOpen(false)}>Cancel</button>{tab === "expenses" && <button className="button button-secondary" type="button" onClick={() => void checkDuplicateExpense()} disabled={action.busy}>Check duplicate</button>}<button className="button button-primary" type="submit" form="create-record" disabled={action.busy}>{action.busy ? "Saving…" : "Save record"}</button></div></DetailModal>}
    {showImport && <DetailModal title="Import bank statement" onClose={() => setShowImport(false)}><form id="import-statement" onSubmit={importStatement}><div className="warning-banner">Upload a CSV statement. The backend imports it and runs matching during the same request.</div><label className="drop-zone" htmlFor="statement-file"><span className="drop-zone-icon">⇧</span><strong>{file?.name ?? "Choose a statement CSV"}</strong><span>CSV file · Includes transaction date, description, and amount</span><input id="statement-file" type="file" className="file-input" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>{action.error && <div className="error-state">{action.error}</div>}</form><div className="modal-footer"><button className="button button-secondary" onClick={() => setShowImport(false)}>Cancel</button><button className="button button-primary" form="import-statement" disabled={!file || action.busy}>{action.busy ? "Importing…" : "Import & match"}</button></div></DetailModal>}
    {selected && tab === "reconciliation" && <DetailModal title="Review bank transaction" value={selected} onClose={() => setSelected(null)}>
      <div className="recon-detail"><div className="recon-box"><h4>Bank statement line</h4><strong>{formatMoney(selected.amount, String(selected.currency ?? "USD"))}</strong><p>{displayValue(selected.transaction_date)} · {displayValue(selected.description)}</p><p>Reference: {displayValue(selected.reference)}</p></div><div className="recon-box"><h4>Suggested match</h4><strong>{displayValue(selected.matched_entity_type)}</strong><p>{displayValue(selected.matched_entity_id)}</p><div className="confidence-track"><i style={{ width: `${Math.max(0, Math.min(100, Number(selected.match_confidence ?? 0) * 100))}%` }} /></div><p>{selected.match_confidence == null ? "No confidence score" : `${(Number(selected.match_confidence) * 100).toFixed(0)}% confidence`} · {displayValue(selected.match_notes)}</p></div></div>
      <div className="form-grid" style={{ marginTop: 13 }}><Field label="Decision notes (required)" name="decision-notes" className="full" value={reason} onChange={(event) => setReason(event.target.value)} /><Field label="Correct entity type" name="entity-type" value={correctEntityType} onChange={(event) => setCorrectEntityType(event.target.value)} /><Field label="Correct entity ID" name="entity-id" value={correctEntityId} onChange={(event) => setCorrectEntityId(event.target.value)} /></div>
      {action.error && <div className="error-state">{action.error}</div>}
      <div className="form-actions"><button className="button button-danger" disabled={!reason.trim() || action.busy} onClick={() => void matchDecision("reject")}>Reject match</button><button className="button button-secondary" disabled={!reason.trim() || !correctEntityId.trim() || action.busy} onClick={() => void matchDecision("correct")}>Correct match</button><button className="button button-primary" disabled={!reason.trim() || action.busy} onClick={() => void matchDecision("approve")}>Approve match</button></div>
    </DetailModal>}
    {selected && tab === "expenses" && <DetailModal title="Expense review" value={selected} onClose={() => setSelected(null)}><div className="detail-grid">{[["Description", selected.description], ["Amount", formatMoney(selected.amount, String(selected.currency ?? "USD"))], ["Requester", selected.created_by], ["Approval status", selected.approval_status], ["Vendor", selected.vendor_id], ["Date", selected.expense_date]].map(([label, value]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{displayValue(value)}</strong></div>)}</div>{selected.approval_status === "pending" && <><div className="form-field" style={{ marginTop: 13 }}><label htmlFor="expense-reason">Decision reason (required)</label><textarea id="expense-reason" className="textarea" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Record why this expense is approved or rejected." /></div><div className="form-actions"><button className="button button-danger" disabled={!reason.trim() || action.busy} onClick={() => void expenseAction(selected, "reject")}>Reject</button><button className="button button-primary" disabled={!reason.trim() || action.busy} onClick={() => void expenseAction(selected, "approve")}>Approve</button></div></>}{action.error && <div className="error-state">{action.error}</div>}</DetailModal>}
    {selected && tab === "invoices" && <DetailModal title={`Invoice ${displayValue(selected.invoice_number)}`} value={selected} onClose={() => setSelected(null)}><div className="detail-grid">{[["Resident / vendor", selected.resident_id ?? selected.vendor_id], ["Due date", selected.due_date], ["Amount", formatMoney(selected.amount, String(selected.currency ?? "USD"))], ["Paid", formatMoney(selected.amount_paid, String(selected.currency ?? "USD"))], ["Outstanding", formatMoney(Number(selected.amount ?? 0) - Number(selected.amount_paid ?? 0), String(selected.currency ?? "USD"))], ["Status", selected.status]].map(([label, value]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{displayValue(value)}</strong></div>)}</div><div className="tabs" style={{ marginTop: 14, padding: 0 }}><button className={`tab ${!editInvoice ? "tab-active" : ""}`} onClick={() => setEditInvoice(false)}>Invoice details</button><button className={`tab ${editInvoice ? "tab-active" : ""}`} onClick={() => setEditInvoice(true)}>Edit metadata</button></div>{editInvoice ? <form id="invoice-edit" className="form-grid" style={{ marginTop: 12 }} onSubmit={saveInvoiceEdit}><Field label="Due date" name="due_date" type="date" defaultValue={String(selected.due_date ?? "")} /><Field label="Status" name="status" type="select" options={["unpaid", "partially_paid", "paid", "overdue", "cancelled"]} defaultValue={String(selected.status ?? "unpaid")} /><Field label="Amount paid" name="amount_paid" type="number" defaultValue={String(selected.amount_paid ?? "0")} /><Field label="Description" name="description" defaultValue={String(selected.description ?? "")} className="full" /><div className="form-actions form-field full"><button className="button button-primary" type="submit" disabled={action.busy}>Save metadata</button></div>{action.error && <div className="error-state form-field full">{action.error}</div>}</form> : <><div className="inline-note">Use the amount field below to record a full or partial allocation against the outstanding balance.</div><form id="allocate-payment" className="form-actions" onSubmit={allocateInvoicePayment}><input aria-label="Allocation amount" className="input" type="number" min="0.01" step="0.01" placeholder="Amount to allocate" value={allocationAmount} onChange={(event) => setAllocationAmount(event.target.value)} required /><button className="button button-primary" type="submit" disabled={action.busy || !allocationAmount}>{action.busy ? "Saving…" : "Allocate payment"}</button></form></>}{action.error && <div className="error-state">{action.error}</div>}</DetailModal>}
  </>;
}

function Field({ label, name, type = "text", required = false, placeholder = "", defaultValue, options, className = "", value, onChange }: { label: string; name: string; type?: string; required?: boolean; placeholder?: string; defaultValue?: string; options?: string[]; className?: string; value?: string; onChange?: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => void }) {
  return <div className={`form-field ${className}`}>
    <label htmlFor={name}>{label}{required && " *"}</label>
    {type === "select"
      ? <select id={name} name={name} className="select" defaultValue={value === undefined ? defaultValue ?? options?.[0] : undefined} value={value} required={required} onChange={onChange}>{options?.map((option) => <option key={option} value={option}>{option.replaceAll("_", " ")}</option>)}</select>
      : <input id={name} name={name} className="input" type={type} required={required} placeholder={placeholder} defaultValue={value === undefined ? defaultValue : undefined} value={value} onChange={onChange} />}
  </div>;
}

function FinancialReport() {
  const today = new Date().toISOString().slice(0, 10);
  const monthStart = `${today.slice(0, 8)}01`;
  const [start, setStart] = useState(monthStart);
  const [end, setEnd] = useState(today);
  const [currency, setCurrency] = useState("USD");
  const [applied, setApplied] = useState({ start, end, currency });
  const [ownerStatement, setOwnerStatement] = useState<JsonRecord | null>(null);
  const [ownerBusy, setOwnerBusy] = useState(false);
  const [error, setError] = useState("");
  const path = `${propertyPath("/reports/financial-summary")}${queryString({ start_date: applied.start, end_date: applied.end, currency: applied.currency })}`;
  const resource = useResource<JsonRecord>(path);
  const report = resource.data;
  const reportAction = useAction();
  async function generateOwner() {
    setOwnerBusy(true); setError("");
    try { setOwnerStatement(await apiRequest<JsonRecord>(`${propertyPath("/reports/owner-statement")}${queryString({ start_date: applied.start, end_date: applied.end, currency: applied.currency })}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not generate owner statement."); }
    finally { setOwnerBusy(false); }
  }
  const value = (key: string) => report ? formatMoney(report[key], String(report.currency ?? currency)) : "—";
  return <div className="panel-body">
    <div className="toolbar" style={{ padding: "0 0 13px", borderBottom: 0 }}><label className="context-key" htmlFor="report-start">From</label><input id="report-start" className="input" type="date" value={start} onChange={(event) => setStart(event.target.value)} /><label className="context-key" htmlFor="report-end">To</label><input id="report-end" className="input" type="date" value={end} onChange={(event) => setEnd(event.target.value)} /><select className="select" value={currency} onChange={(event) => setCurrency(event.target.value)}><option>USD</option><option>CAD</option><option>EUR</option><option>GBP</option><option>INR</option></select><button className="button button-secondary" onClick={() => { setApplied({ start, end, currency }); setOwnerStatement(null); }}>Apply dates</button><span className="toolbar-spacer" /><button className="button button-secondary" onClick={() => void reportAction.run(() => downloadCsv(`${propertyPath("/reports/financial-summary/csv")}${queryString({ start_date: applied.start, end_date: applied.end, currency: applied.currency })}`, "financial-summary.csv"), "Summary CSV downloaded.")}>Export summary CSV</button><button className="button button-primary" onClick={generateOwner} disabled={ownerBusy}>{ownerBusy ? "Generating…" : "Generate owner statement"}</button></div>
    {(error || reportAction.error) && <div className="error-state">{error || reportAction.error}</div>}{reportAction.success && <div className="inline-success">{reportAction.success}</div>}
    {resource.loading ? <Loading text="Calculating financial summary…" /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <>
      <div className="grid grid-4" style={{ marginTop: 8 }}><Metric label="Rent billed" value={value("total_rent_billed")} note={`${displayValue(report?.period_start)} — ${displayValue(report?.period_end)}`} icon="$" /><Metric label="Realized collections" value={value("total_rent_collected")} note={`${displayValue(report?.pending_payments_count)} pending payment(s)`} icon="↗" /><Metric label="Outstanding rent" value={value("total_rent_outstanding")} note={`${displayValue(report?.unpaid_invoices_count)} unpaid invoice(s)`} icon="◷" /><Metric label="Collection rate" value={report?.collection_rate_percentage == null ? "N/A" : `${Number(report.collection_rate_percentage).toFixed(1)}%`} note="Based on rent billed" icon="◎" /></div>
      <div className="grid grid-3" style={{ marginTop: 13 }}><Metric label="Expenses" value={value("total_expenses")} note="In selected period" icon="↘" /><Metric label="Net operating income" value={value("net_operating_income")} note="Collected income less expenses" icon="∑" /><Metric label="Pending payments" value={value("pending_rent_amount")} note={`${displayValue(report?.pending_payments_count)} item(s) · not counted as collected`} icon="◷" /></div>
    </>}
    {ownerStatement && <div className="panel" style={{ marginTop: 15 }}><div className="panel-head"><div><h2>Owner statement preview</h2><p>{displayValue(ownerStatement.property_name)} · {displayValue(ownerStatement.period_start)} to {displayValue(ownerStatement.period_end)}</p></div><button className="button button-secondary" onClick={() => void reportAction.run(() => downloadCsv(`${propertyPath("/reports/owner-statement/csv")}${queryString({ start_date: applied.start, end_date: applied.end, currency: applied.currency })}`, "owner-statement.csv"), "Owner statement CSV downloaded.")}>Export owner statement CSV</button></div><div className="panel-body"><div className="detail-grid">{[["Gross rental income", ownerStatement.gross_rental_income], ["Other income", ownerStatement.other_income], ["Operating expenses", ownerStatement.total_operating_expenses], ["Management fee", ownerStatement.management_fee_amount], ["Net operating income", ownerStatement.net_operating_income], ["Owner distribution", ownerStatement.owner_distribution_amount]].map(([label, amount]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{formatMoney(amount, String(ownerStatement.currency ?? currency))}</strong></div>)}</div></div></div>}
    <div className="inline-note">Pending payments are shown separately from realized collections. Collection rate is N/A when no rent was billed in the selected period.</div>
  </div>;
}

type MaintenanceTab = "requests" | "work-orders" | "sla" | "assets" | "preventive" | "inspections";
const maintenanceTabs: { id: MaintenanceTab; label: string }[] = [
  { id: "requests", label: "Requests" }, { id: "work-orders", label: "Work orders" }, { id: "sla", label: "SLA" }, { id: "assets", label: "Assets" }, { id: "preventive", label: "Preventive" }, { id: "inspections", label: "Inspections" },
];

function Maintenance() {
  const base = propertyPath("");
  const [tab, setTab] = useState<MaintenanceTab>("requests");
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const [analysis, setAnalysis] = useState<unknown>(null);
  const [inspectionItems, setInspectionItems] = useState([{ item_name: "Smoke detector", category: "general", status: "pass", notes: "", severity_if_failed: "medium" }]);
  const [triagePreview, setTriagePreview] = useState<{ request: JsonRecord; result: JsonRecord } | null>(null);
  const [busyOperation, setBusyOperation] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const action = useAction();
  const routes: Record<MaintenanceTab, string> = {
    requests: `${base}/maintenance-requests?limit=250`, "work-orders": `${base}/work-orders?limit=250`, sla: `${base}/sla/escalations?limit=250`,
    assets: `${base}/maintenance/assets?limit=250`, preventive: `${base}/maintenance/preventive-tasks?limit=250`, inspections: `${base}/work-orders?limit=0`,
  };
  const resource = useResource<JsonRecord[]>(`${routes[tab]}${routes[tab].includes("?") ? "&" : "?"}__refresh=${revision}`, tab !== "inspections");
  const columns: Record<MaintenanceTab, string[]> = {
    requests: ["request_id", "unit_id", "category", "priority", "is_emergency", "sla_deadline_at", "status"],
    "work-orders": ["work_order_id", "title", "unit_id", "priority", "vendor_id", "estimated_cost", "status"],
    sla: ["escalation_type", "request_id", "sla_deadline_at", "detected_at", "status"],
    assets: ["name", "unit_id", "asset_category", "make_model", "warranty_expiry_date", "total_maintenance_spend", "health_status"],
    preventive: ["title", "category", "frequency", "unit_id", "next_due_date", "is_overdue"],
    inspections: [],
  };
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formValues(form);
    const endpoint = tab === "requests" ? `${base}/maintenance-requests` : tab === "work-orders" ? `${base}/work-orders` : tab === "assets" ? `${base}/maintenance/assets` : `${base}/maintenance/preventive-tasks`;
    const result = await action.run(() => apiRequest(endpoint, { method: "POST", body: JSON.stringify(data) }), "Record created.");
    if (result) { setCreateOpen(false); setRevision((value) => value + 1); }
  }
  async function previewTriage(row: JsonRecord) {
    const result = await action.run(() => apiRequest(`${base}/maintenance/triage`, { method: "POST", body: JSON.stringify({ unit_id: row.unit_id, resident_id: row.resident_id ?? undefined, description: row.description }) }), "Triage preview generated.");
    if (result) setTriagePreview({ request: row, result: result as JsonRecord });
  }
  async function saveTriage() {
    if (!triagePreview) return;
    const id = String(triagePreview.request.request_id);
    const result = await action.run(() => apiRequest(`${base}/maintenance-requests/${encodeURIComponent(id)}/triage`, { method: "POST" }), "Triage saved to maintenance request.");
    if (result) { setTriagePreview(null); setAnalysis({ request: result, triage: triagePreview.result }); setRevision((value) => value + 1); }
  }
  async function runMaintenanceAction(row: JsonRecord, actionName: "convert" | "dispatch") {
    const id = String(row.request_id);
    const successMessage = actionName === "convert" ? "Request converted to a work order." : "Request dispatched.";
    const result = await action.run(() => apiRequest(`${base}/maintenance-requests/${encodeURIComponent(id)}/${actionName}`, { method: "POST" }), successMessage);
    if (result) { setAnalysis(result); setRevision((value) => value + 1); }
  }
  async function monitorSla() {
    const result = await action.run(() => apiRequest(`${base}/sla/monitor`, { method: "POST" }), "SLA monitor completed.");
    if (result) { setAnalysis(result); setRevision((value) => value + 1); }
  }
  async function runAnalysis(kind: "operations-analysis" | "cost-analysis") {
    setBusyOperation(true); setError("");
    try { const result = kind === "operations-analysis" ? await apiRequest(`${base}/maintenance/operations-analysis`, { method: "POST", body: JSON.stringify({}) }) : await apiRequest(`${base}/maintenance/cost-analysis`); setAnalysis(result); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Analysis failed."); }
    finally { setBusyOperation(false); }
  }
  async function saveInspection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const items = inspectionItems.filter((item) => item.item_name.trim());
    if (!items.length) { setError("Add at least one checklist item."); return; }
    const payload = { unit_id: form.get("unit_id"), inspection_type: form.get("inspection_type"), inspector_name: form.get("inspector_name"), inspection_date: form.get("inspection_date") || undefined, overall_notes: form.get("overall_notes") || undefined, items };
    const result = await action.run(() => apiRequest(`${base}/maintenance/inspections`, { method: "POST", body: JSON.stringify(payload) }), "Inspection saved and deficiencies analyzed.");
    if (result) { setAnalysis(result); setCreateOpen(false); }
  }
  return <>
    <SectionHeader eyebrow="PROPERTY OPERATIONS" title="Maintenance" description="Triage resident requests, coordinate work orders, and keep response commitments visible." actions={<><button className="button button-secondary" onClick={() => void runAnalysis("operations-analysis")} disabled={busyOperation}>{busyOperation ? "Analyzing…" : "Run operations analysis"}</button><button className="button button-secondary" onClick={() => void runAnalysis("cost-analysis")} disabled={busyOperation}>View cost analysis</button><button className="button button-secondary" onClick={monitorSla} disabled={action.busy}>Run SLA monitor</button><button className="button button-primary" onClick={() => { action.clear(); setCreateOpen(true); }}>{tab === "requests" ? "＋ New request" : tab === "work-orders" ? "＋ Create work order" : tab === "assets" ? "＋ Add asset" : tab === "preventive" ? "＋ Create schedule" : tab === "inspections" ? "＋ Record inspection" : "＋ New request"}</button></>} />
    {(error || action.error) && <div className="error-state">{error || action.error}</div>}{action.success && <div className="inline-success">{action.success}</div>}
    <Panel title="Maintenance operations" subtitle="Prioritize hazards, track response times, and connect requests to assigned work.">
      <div className="tabs">{maintenanceTabs.map((item) => <button key={item.id} className={`tab ${tab === item.id ? "tab-active" : ""}`} onClick={() => setTab(item.id)}>{item.label}</button>)}</div>
      {tab === "requests" && <div className="inline-note warning">Emergency notices and safety instructions appear after triage. Review them before dispatch.</div>}
      {tab === "inspections" ? <div className="panel-body"><Empty title="Inspection history is not available" text="The current API accepts inspection checklists and returns findings and generated work orders, but it does not provide an inspection history list." /><button className="button button-primary" onClick={() => { setCreateOpen(true); setError(""); }}>Record inspection</button></div> : resource.loading ? <Loading /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <DataTable rows={resource.data ?? []} columns={columns[tab]} onOpen={(row) => setSelected(row)} actions={tab === "requests" ? (row) => row.status === "submitted" ? <button className="table-link" onClick={() => void previewTriage(row)}>Triage</button> : row.status === "triaged" ? <button className="table-link" onClick={() => void runMaintenanceAction(row, "convert")}>Convert</button> : row.status === "converted_to_work_order" ? <button className="table-link" onClick={() => void runMaintenanceAction(row, "dispatch")}>Dispatch</button> : null : undefined} emptyTitle={`No ${tab.replaceAll("-", " ")} found`} emptyText={tab === "requests" ? "New resident maintenance reports will appear here." : "Records will appear here when they are available."} />}
    </Panel>
    {createOpen && tab === "inspections" ? <DetailModal title="Record inspection" onClose={() => setCreateOpen(false)}><form id="inspection-form" className="form-grid" onSubmit={saveInspection}><Field label="Unit ID" name="unit_id" required /><Field label="Inspection type" name="inspection_type" type="select" options={["move_in", "move_out", "routine_quarterly", "annual_safety", "hvac_seasonal"]} /><Field label="Inspector" name="inspector_name" required /><Field label="Inspection date" name="inspection_date" type="date" /><div className="form-field full"><div className="field-row"><label>Checklist items</label><button className="table-link" type="button" onClick={() => setInspectionItems((items) => [...items, { item_name: "", category: "general", status: "attention_needed", notes: "", severity_if_failed: "medium" }])}>＋ Add checklist item</button></div>{inspectionItems.map((item, index) => <div className="inspection-row" key={index}><Field label="Item" name={`item-${index}`} value={item.item_name} onChange={(event) => setInspectionItems((items) => items.map((current, row) => row === index ? { ...current, item_name: event.target.value } : current))} /><Field label="Category" name={`category-${index}`} type="select" value={item.category} options={["plumbing", "electrical", "hvac", "appliance", "structural", "pest_control", "landscaping", "cleaning", "security", "general"]} onChange={(event) => setInspectionItems((items) => items.map((current, row) => row === index ? { ...current, category: event.target.value } : current))} /><Field label="Result" name={`status-${index}`} type="select" value={item.status} options={["pass", "fail", "attention_needed", "not_applicable"]} onChange={(event) => setInspectionItems((items) => items.map((current, row) => row === index ? { ...current, status: event.target.value } : current))} /><Field label="Severity" name={`severity-${index}`} type="select" value={item.severity_if_failed} options={["low", "medium", "high", "emergency"]} onChange={(event) => setInspectionItems((items) => items.map((current, row) => row === index ? { ...current, severity_if_failed: event.target.value } : current))} /><Field label="Notes" name={`notes-${index}`} value={item.notes} onChange={(event) => setInspectionItems((items) => items.map((current, row) => row === index ? { ...current, notes: event.target.value } : current))} />{inspectionItems.length > 1 && <button type="button" className="table-link danger" onClick={() => setInspectionItems((items) => items.filter((_, row) => row !== index))}>Remove</button>}</div>)}</div><Field label="Overall notes" name="overall_notes" className="full" />{error && <div className="error-state form-field full">{error}</div>}</form><div className="modal-footer"><button className="button button-secondary" onClick={() => setCreateOpen(false)}>Cancel</button><button className="button button-primary" type="submit" form="inspection-form" disabled={action.busy}>{action.busy ? "Saving…" : "Save inspection"}</button></div></DetailModal> : createOpen && <DetailModal title={tab === "requests" ? "New maintenance request" : tab === "work-orders" ? "Create work order" : tab === "assets" ? "Add asset" : "Create preventive schedule"} onClose={() => setCreateOpen(false)}><form id="maintenance-create" className="form-grid" onSubmit={submit}>
      {tab === "requests" && <div className="warning-banner form-field full"><strong>Emergency notice</strong>If someone is in immediate danger, contact local emergency services. The form will classify urgent requests during triage.</div>}
      {(tab === "requests" || tab === "work-orders") && <><Field label="Unit ID" name="unit_id" required /><Field label="Resident ID" name="resident_id" />{tab === "work-orders" && <Field label="Title" name="title" required />}<Field label="Description" name="description" required className="full" /></>}
      {tab === "work-orders" && <><Field label="Priority" name="priority" type="select" options={["low", "medium", "high", "emergency"]} /><Field label="Estimated cost" name="estimated_cost" type="number" /></>}
      {tab === "assets" && <><Field label="Asset name" name="name" required /><Field label="Category" name="asset_category" type="select" options={["hvac", "water_heater", "refrigerator", "dishwasher", "stove_oven", "washer_dryer", "electrical_panel", "roof", "plumbing_pump", "other"]} /><Field label="Unit ID" name="unit_id" /><Field label="Make / model" name="make_model" /><Field label="Serial number" name="serial_number" /><Field label="Install date" name="install_date" type="date" /><Field label="Warranty expiry" name="warranty_expiry_date" type="date" /><Field label="Purchase cost" name="purchase_cost" type="number" /></>}
      {tab === "preventive" && <><Field label="Task title" name="title" required /><Field label="Category" name="category" type="select" options={["hvac", "plumbing", "electrical", "appliance", "structural", "pest_control", "landscaping", "cleaning", "security", "general"]} /><Field label="Frequency" name="frequency" type="select" options={["monthly", "quarterly", "semi_annual", "annual", "custom_days"]} /><Field label="Interval days" name="interval_days" type="number" defaultValue="90" /><Field label="Unit ID" name="unit_id" /><Field label="Asset ID" name="asset_id" /><Field label="Next due date" name="next_due_date" type="date" required /><Field label="Estimated cost" name="estimated_cost" type="number" /><Field label="Description" name="description" className="full" /></>}
      {action.error && <div className="error-state form-field full">{action.error}</div>}
    </form><div className="modal-footer"><button className="button button-secondary" onClick={() => setCreateOpen(false)}>Cancel</button><button className="button button-primary" type="submit" form="maintenance-create" disabled={action.busy}>{action.busy ? "Saving…" : "Save"}</button></div></DetailModal>}
    {selected && <DetailModal title={tab === "requests" ? "Maintenance request" : tab === "work-orders" ? "Work order" : tab === "sla" ? "SLA escalation" : tab === "assets" ? "Asset details" : "Preventive maintenance task"} value={selected} onClose={() => setSelected(null)}>{Boolean(selected.is_emergency) && <div className="safety-banner"><strong>Emergency request</strong>Review the triage result and immediate safety instructions before assigning work.</div>}<pre className="json-view" style={{ marginTop: 12 }}>{JSON.stringify(selected, null, 2)}</pre>{tab === "work-orders" && <div className="inline-note warning">Vendor selection is not available in the current API. Dispatch uses automatic assignment.</div>}{tab === "requests" && <div className="form-actions">{selected.status === "submitted" && <button className="button button-primary" onClick={() => void previewTriage(selected)}>Triage request</button>}{selected.status === "triaged" && <button className="button button-primary" onClick={() => void runMaintenanceAction(selected, "convert")}>Convert to work order</button>}{selected.status === "converted_to_work_order" && <button className="button button-primary" onClick={() => void runMaintenanceAction(selected, "dispatch")}>Auto-dispatch</button>}</div>}</DetailModal>}
    {triagePreview && <DetailModal title="Triage result" value={triagePreview.result} onClose={() => setTriagePreview(null)}><div className="detail-grid">{[["Category", triagePreview.result.category], ["Priority", triagePreview.result.priority], ["Emergency", triagePreview.result.is_emergency], ["Estimated SLA", `${displayValue(triagePreview.result.estimated_sla_hours)} hours`], ["Suggested vendor category", triagePreview.result.suggested_vendor_category], ["Recommended action", triagePreview.result.recommended_action]].map(([label, value]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{displayValue(value)}</strong></div>)}</div>{Boolean(triagePreview.result.is_emergency) && <div className="safety-banner" style={{ marginTop: 13 }}><strong>Emergency safety instructions</strong>{Array.isArray(triagePreview.result.immediate_safety_instructions) ? triagePreview.result.immediate_safety_instructions.map((instruction, index) => <div key={index}>• {String(instruction)}</div>) : "Follow emergency guidance."}<p>{displayValue(triagePreview.result.emergency_rationale)}</p></div>}<div className="inline-note">Review this result. Save triage to update the request, then convert it to a work order.</div><div className="form-actions"><button className="button button-secondary" onClick={() => setTriagePreview(null)}>Cancel</button><button className="button button-primary" onClick={() => void saveTriage()} disabled={action.busy}>{action.busy ? "Saving…" : "Apply triage"}</button></div>{action.error && <div className="error-state">{action.error}</div>}</DetailModal>}
    {analysis !== null && <DetailModal title="Maintenance analysis result" value={analysis} onClose={() => setAnalysis(null)}><pre className="json-view">{JSON.stringify(analysis, null, 2)}</pre></DetailModal>}
  </>;
}

type ResidentTab = "residents" | "leases" | "units" | "vendors" | "analysis";
const residentTabs: { id: ResidentTab; label: string }[] = [{ id: "residents", label: "Residents" }, { id: "leases", label: "Leases" }, { id: "units", label: "Units" }, { id: "vendors", label: "Vendors" }, { id: "analysis", label: "Analysis" }];

function ResidentsLeases() {
  const { activeMembership } = useSession();
  const base = propertyPath("");
  const [tab, setTab] = useState<ResidentTab>("residents");
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const [windowDays, setWindowDays] = useState("60");
  const [revision, setRevision] = useState(0);
  const action = useAction();
  const routes: Record<ResidentTab, string> = { residents: `${base}/residents?limit=250`, leases: `${base}/leases?limit=250`, units: `${base}/units?limit=250`, vendors: `${base}/vendors?limit=250`, analysis: `${base}/residents?limit=0` };
  const resource = useResource<JsonRecord[]>(`${routes[tab]}${routes[tab].includes("?") ? "&" : "?"}__refresh=${revision}`, tab !== "analysis");
  const columns: Record<ResidentTab, string[]> = { residents: ["first_name", "last_name", "email", "phone", "status"], leases: ["unit_id", "resident_id", "start_date", "end_date", "rent_amount", "status"], units: ["unit_number", "status", "rent_amount"], vendors: ["name", "category", "email", "phone", "is_active"], analysis: [] };
  async function createRecord(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const payload = formValues(event.currentTarget);
    const endpoint = { residents: `${base}/residents`, leases: `${base}/leases`, units: `${base}/units`, vendors: `${base}/vendors` }[tab as Exclude<ResidentTab, "analysis">];
    const saved = await action.run(() => apiRequest(endpoint, { method: "POST", body: JSON.stringify(payload) }), `${tab.slice(0, -1)} created.`);
    if (saved) { setCreateOpen(false); setRevision((value) => value + 1); }
  }
  async function runAnalysis() {
    const response = await action.run(() => apiRequest(`${base}/resident-lease-analysis`, { method: "POST", body: JSON.stringify({ expiration_window_days: Number(windowDays) }) }), "Resident and lease analysis completed.");
    if (response) setResult(response);
  }
  return <>
    <SectionHeader eyebrow="RECORDS & INTELLIGENCE" title="Residents & leases" description="Manage the people, units, and agreements connected to this property." actions={tab === "analysis" ? <><select className="select" value={windowDays} onChange={(event) => setWindowDays(event.target.value)}><option value="30">30 day lookahead</option><option value="60">60 day lookahead</option><option value="90">90 day lookahead</option><option value="180">180 day lookahead</option></select><button className="button button-primary" onClick={runAnalysis} disabled={action.busy}>{action.busy ? "Analyzing…" : "Run analysis"}</button></> : activeMembership?.permissions.includes("property:manage") ? <button className="button button-primary" onClick={() => { action.clear(); setCreateOpen(true); }}>＋ Add {tab.slice(0, -1)}</button> : undefined} />
    {action.error && <div className="error-state">{action.error}</div>}{action.success && <div className="inline-success">{action.success}</div>}
    <Panel title={tab === "analysis" ? "Data quality findings" : `${tab[0].toUpperCase()}${tab.slice(1)}`} subtitle={tab === "analysis" ? "The analysis is read-only. Open the affected record to correct it." : "Property-scoped records and linked identifiers."}>
      <div className="tabs">{residentTabs.map((item) => <button key={item.id} className={`tab ${tab === item.id ? "tab-active" : ""}`} onClick={() => setTab(item.id)}>{item.label}</button>)}</div>
      {tab === "analysis" ? <div className="panel-body"><Empty title="Run an analysis to find risks" text="Check for duplicate residents, missing details, lease dates, expirations, and overlaps." />{result !== null && <pre className="json-view">{JSON.stringify(result, null, 2)}</pre>}</div> : resource.loading ? <Loading /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <DataTable rows={resource.data ?? []} columns={columns[tab]} onOpen={(row) => setSelected(row)} emptyTitle={`No ${tab} records`} emptyText={`Create a ${tab.slice(0, -1)} record to get started.`} />}
    </Panel>
    {createOpen && <DetailModal title={`Create ${tab.slice(0, -1)}`} onClose={() => setCreateOpen(false)}><form id="record-form" className="form-grid" onSubmit={createRecord}>
      {tab === "residents" && <><Field label="First name" name="first_name" required /><Field label="Last name" name="last_name" required /><Field label="Email" name="email" type="email" required /><Field label="Phone" name="phone" /><Field label="Status" name="status" type="select" options={["active", "past", "applicant"]} /></>}
      {tab === "units" && <><Field label="Unit number" name="unit_number" required /><Field label="Status" name="status" type="select" options={["vacant", "occupied", "maintenance"]} /><Field label="Rent amount" name="rent_amount" type="number" /></>}
      {tab === "leases" && <><Field label="Unit ID" name="unit_id" required /><Field label="Resident ID" name="resident_id" required /><Field label="Start date" name="start_date" type="date" required /><Field label="End date" name="end_date" type="date" required /><Field label="Rent amount" name="rent_amount" type="number" required /><Field label="Deposit amount" name="deposit_amount" type="number" /><Field label="Status" name="status" type="select" options={["pending", "active", "expired", "terminated"]} /></>}
      {tab === "vendors" && <><Field label="Vendor name" name="name" required /><Field label="Category" name="category" type="select" options={["general", "plumbing", "electrical", "hvac", "landscaping", "cleaning"]} /><Field label="Email" name="email" type="email" /><Field label="Phone" name="phone" /></>}
      {action.error && <div className="error-state form-field full">{action.error}</div>}
    </form><div className="modal-footer"><button className="button button-secondary" onClick={() => setCreateOpen(false)}>Cancel</button><button className="button button-primary" type="submit" form="record-form" disabled={action.busy}>{action.busy ? "Saving…" : "Create record"}</button></div></DetailModal>}
    {selected && <DetailModal title={`${tab.slice(0, -1)} details`} value={selected} onClose={() => setSelected(null)}><pre className="json-view">{JSON.stringify(selected, null, 2)}</pre></DetailModal>}
    {result !== null && <DetailModal title="Resident & lease analysis" value={result} onClose={() => setResult(null)}><pre className="json-view">{JSON.stringify(result, null, 2)}</pre><div className="inline-note">Analysis does not modify records. Use the entity IDs in a finding to open and correct the corresponding record.</div></DetailModal>}
  </>;
}

function Compliance() {
  const base = propertyPath("");
  const resource = useResource<JsonRecord[]>(`${base}/compliance/findings?limit=250`);
  const action = useAction();
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const [mode, setMode] = useState<"assign" | "resolve" | "dismiss">("assign");
  const [reason, setReason] = useState("");
  const [runResult, setRunResult] = useState<JsonRecord[] | null>(null);
  async function runAudit() {
    const result = await action.run(() => apiRequest<JsonRecord[]>(`${base}/compliance/audit`, { method: "POST" }), "Compliance audit completed.");
    if (result) { setRunResult(result as JsonRecord[]); resource.refresh(); }
  }
  async function saveAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected) return;
    const id = String(selected.finding_id);
    const payload = mode === "assign" ? { assigned_to: reason.trim() } : { action: mode, reason: reason.trim() };
    const endpoint = mode === "assign" ? `${base}/compliance/findings/${encodeURIComponent(id)}/assign` : `${base}/compliance/findings/${encodeURIComponent(id)}/resolve`;
    const result = await action.run(() => apiRequest(endpoint, { method: "POST", body: JSON.stringify(payload) }), mode === "assign" ? "Finding assigned." : `Finding ${mode}d with notes.`);
    if (result) { setSelected(null); setReason(""); resource.refresh(); }
  }
  return <>
    <SectionHeader eyebrow="RISK & POLICY" title="Compliance" description="Assign findings, review their evidence, and document how each issue was resolved." actions={<button className="button button-primary" onClick={runAudit} disabled={action.busy}>{action.busy ? "Running audit…" : "Run compliance audit"}</button>} />
    {action.error && <div className="error-state">{action.error}</div>}{action.success && <div className="inline-success">{action.success}</div>}
    {runResult && <Panel title="Latest audit results" subtitle={`${runResult.length} finding(s) returned`} action={<button className="button button-quiet" onClick={() => setRunResult(null)}>Dismiss</button>}><DataTable rows={runResult} columns={["severity", "category", "title", "status"]} onOpen={(row) => setSelected(row)} /></Panel>}
    <Panel title="Findings" subtitle="Severity, affected records, and resolution status." >{resource.loading ? <Loading /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <DataTable rows={resource.data ?? []} columns={["severity", "category", "title", "status", "assigned_to", "created_at"]} onOpen={(row) => { setSelected(row); setMode("assign"); setReason(""); }} actions={(row) => row.status !== "resolved" && row.status !== "dismissed" ? <><button className="table-link" onClick={() => { setSelected(row); setMode("assign"); setReason(""); }}>Assign</button><button className="table-link" onClick={() => { setSelected(row); setMode("resolve"); setReason(""); }}>Resolve</button></> : null} emptyTitle="No compliance findings" emptyText="Run a compliance audit to identify open issues." />}</Panel>
    {selected && <DetailModal title="Compliance finding" value={selected} onClose={() => setSelected(null)}>
      <div className="detail-grid">{[["Severity", selected.severity], ["Category", selected.category], ["Status", selected.status], ["Assignee", selected.assigned_to], ["Triggering rule", selected.triggering_rule], ["Affected records", selected.affected_record_ids]].map(([label, value]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{displayValue(value)}</strong></div>)}</div>
      <div className="panel" style={{ marginTop: 12 }}><div className="panel-head"><h3>{displayValue(selected.title)}</h3></div><div className="panel-body" style={{ color: "#586c73", fontSize: 10, lineHeight: 1.55 }}>{displayValue(selected.description)}<br /><br /><strong>Evidence:</strong> {displayValue(selected.evidence)}</div></div>
      <div className="tabs" style={{ marginTop: 12, padding: 0 }}><button className={`tab ${mode === "assign" ? "tab-active" : ""}`} onClick={() => { setMode("assign"); setReason(""); }}>Assign</button><button className={`tab ${mode === "resolve" ? "tab-active" : ""}`} onClick={() => { setMode("resolve"); setReason(""); }}>Resolve</button><button className={`tab ${mode === "dismiss" ? "tab-active" : ""}`} onClick={() => { setMode("dismiss"); setReason(""); }}>Dismiss</button></div>
      <form id="finding-action" style={{ marginTop: 12 }} onSubmit={saveAction}><div className="form-field"><label htmlFor="finding-reason">{mode === "assign" ? "Assign to user ID or email" : "Resolution notes (required)"}</label><input id="finding-reason" className="input" value={reason} onChange={(event) => setReason(event.target.value)} required placeholder={mode === "assign" ? "user@example.com" : "Describe the resolution"} /></div>{action.error && <div className="error-state">{action.error}</div>}</form>
      <div className="form-actions"><button className="button button-secondary" onClick={() => setSelected(null)}>Close</button><button className="button button-primary" type="submit" form="finding-action" disabled={action.busy || !reason.trim()}>{action.busy ? "Saving…" : mode === "assign" ? "Assign finding" : `${mode[0].toUpperCase()}${mode.slice(1)} finding`}</button></div>
    </DetailModal>}
  </>;
}

function Workflows() {
  const base = propertyPath("");
  const [revision, setRevision] = useState(0);
  const resource = useResource<JsonRecord[]>(`${base}/workflows?limit=250&__refresh=${revision}`);
  const action = useAction();
  const [createOpen, setCreateOpen] = useState(false);
  const [cancelTarget, setCancelTarget] = useState<JsonRecord | null>(null);
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const [steps, setSteps] = useState<JsonRecord[]>([]);
  const [reason, setReason] = useState("");
  async function startWorkflow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = formValues(event.currentTarget);
    const metadata = String(data.metadata ?? "{}"); let parsed: unknown;
    try { parsed = JSON.parse(metadata); } catch { action.run(async () => { throw new Error("Metadata must be valid JSON."); }); return; }
    const payload = { workflow_type: data.workflow_type, input_reference: data.input_reference, metadata: parsed };
    const result = await action.run(() => apiRequest(base + "/workflows", { method: "POST", body: JSON.stringify(payload) }), "Workflow started.");
    if (result) { setCreateOpen(false); setRevision((value) => value + 1); }
  }
  async function openWorkflow(row: JsonRecord) {
    setSelected(row); setSteps([]);
    const id = String(row.workflow_id ?? "");
    const [detail, history] = await Promise.allSettled([apiRequest<JsonRecord>(`${base}/workflows/${encodeURIComponent(id)}`), apiRequest<JsonRecord[]>(`${base}/workflows/${encodeURIComponent(id)}/steps`)]);
    if (detail.status === "fulfilled") setSelected(detail.value);
    if (history.status === "fulfilled") setSteps(history.value);
  }
  async function resumeWorkflow(row: JsonRecord) {
    const id = String(row.workflow_id);
    const result = await action.run(() => apiRequest(`${base}/workflows/${encodeURIComponent(id)}/resume`, { method: "POST", body: JSON.stringify({ reason: "Resumed from workflow monitor" }) }), "Workflow resumed.");
    if (result) setRevision((value) => value + 1);
  }
  async function cancelWorkflow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!cancelTarget) return;
    const id = String(cancelTarget.workflow_id);
    const result = await action.run(() => apiRequest(`${base}/workflows/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) }), "Workflow cancelled.");
    if (result) { setCancelTarget(null); setReason(""); setRevision((value) => value + 1); }
  }
  return <>
    <SectionHeader eyebrow="DURABLE AGENT RUNS" title="Workflow monitor" description="Track long-running agent work, human approval gates, retries, and verification." actions={<button className="button button-primary" onClick={() => { action.clear(); setCreateOpen(true); }}>＋ Start workflow</button>} />
    <div className="inline-note warning">Workflows need a server-accessible PDF path or stored document reference. A browser-local file path cannot be used as workflow input.</div>
    {action.error && <div className="error-state">{action.error}</div>}{action.success && <div className="inline-success">{action.success}</div>}
    <Panel title="Workflow runs" subtitle="Each workflow records step status, retries, output summaries, and approval references.">{resource.loading ? <Loading /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <DataTable rows={resource.data ?? []} columns={["workflow_type", "status", "current_step", "initiated_by", "created_at", "updated_at"]} onOpen={(row) => void openWorkflow(row)} actions={(row) => <>{["created", "running", "awaiting_approval", "needs_review"].includes(String(row.status)) && <button className="table-link" onClick={() => void resumeWorkflow(row)}>Resume</button>}{!["completed", "failed", "cancelled"].includes(String(row.status)) && <button className="table-link danger" onClick={() => { setCancelTarget(row); setReason(""); }}>Cancel</button>}</>} emptyTitle="No workflows started" emptyText="Start an invoice, reconciliation, financial audit, or maintenance workflow." />}</Panel>
    {createOpen && <DetailModal title="Start workflow" onClose={() => setCreateOpen(false)}><form id="workflow-create" className="form-grid" onSubmit={startWorkflow}><Field label="Workflow type" name="workflow_type" type="select" options={["invoice_processing", "rent_reconciliation", "financial_audit", "maintenance_lifecycle"]} /><Field label="Server document reference or path" name="input_reference" required placeholder="stored-doc-123 or /server/path/document.pdf" className="full" /><div className="form-field full"><label htmlFor="workflow-metadata">Metadata (JSON, optional)</label><textarea id="workflow-metadata" name="metadata" className="textarea" defaultValue="{}" /></div>{action.error && <div className="error-state form-field full">{action.error}</div>}</form><div className="modal-footer"><button className="button button-secondary" onClick={() => setCreateOpen(false)}>Cancel</button><button className="button button-primary" type="submit" form="workflow-create" disabled={action.busy}>{action.busy ? "Starting…" : "Start workflow"}</button></div></DetailModal>}
    {cancelTarget && <DetailModal title="Cancel workflow" onClose={() => setCancelTarget(null)}><div className="warning-banner">Cancellation is recorded in the workflow history. Provide a reason to continue.</div><form id="cancel-workflow" onSubmit={cancelWorkflow}><div className="form-field"><label htmlFor="cancel-reason">Cancellation reason (required)</label><textarea id="cancel-reason" className="textarea" value={reason} onChange={(event) => setReason(event.target.value)} required /></div>{action.error && <div className="error-state">{action.error}</div>}</form><div className="modal-footer"><button className="button button-secondary" onClick={() => setCancelTarget(null)}>Keep running</button><button className="button button-danger" type="submit" form="cancel-workflow" disabled={action.busy || !reason.trim()}>Cancel workflow</button></div></DetailModal>}
    {selected && <DetailModal title="Workflow details" value={selected} onClose={() => setSelected(null)}><div className="detail-grid">{[["Workflow ID", selected.workflow_id], ["Type", selected.workflow_type], ["Status", selected.status], ["Current step", selected.current_step], ["Initiated by", selected.initiated_by], ["Approval request", selected.approval_request_id]].map(([label, value]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{displayValue(value)}</strong></div>)}</div><h3 style={{ color: "#50676e", fontSize: 11, margin: "18px 0 9px" }}>Step history</h3>{steps.length ? <DataTable rows={steps} columns={["step_index", "step_name", "status", "retry_count", "output_summary", "error_message"]} /> : <Empty title="No step history returned" text="Step records appear after a workflow begins processing." />}<h3 style={{ color: "#50676e", fontSize: 11, margin: "18px 0 9px" }}>Workflow errors</h3><pre className="json-view">{JSON.stringify(selected.errors ?? [], null, 2)}</pre></DetailModal>}
  </>;
}

function Notifications() {
  const base = propertyPath("");
  const resource = useResource<JsonRecord[]>(`${base}/notifications?limit=250`);
  const action = useAction();
  const [compose, setCompose] = useState(false);
  const [confirmSend, setConfirmSend] = useState(false);
  const [preview, setPreview] = useState<JsonRecord | null>(null);
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const [draft, setDraft] = useState<JsonRecord | null>(null);
  async function getDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    let context_data: unknown = {};
    try { context_data = JSON.parse(String(form.get("context_data") ?? "{}")); }
    catch { action.run(async () => { throw new Error("Context fields must be valid JSON."); }); return; }
    const payload = { notification_type: form.get("notification_type"), recipient_email: form.get("recipient_email"), recipient_name: form.get("recipient_name") || undefined, context_data };
    setDraft(payload as JsonRecord);
    const result = await action.run(() => apiRequest(`${base}/notifications/preview`, { method: "POST", body: JSON.stringify(payload) }), "Preview generated.");
    if (result) setPreview(result as JsonRecord);
  }
  async function send() {
    if (!draft || !preview) return;
    const payload = { ...draft, subject: preview.subject, idempotency_key: crypto.randomUUID() };
    const result = await action.run(() => apiRequest(`${base}/notifications/send`, { method: "POST", body: JSON.stringify(payload) }), "Notification request completed.");
    if (result) { setSelected(result as JsonRecord); setConfirmSend(false); setCompose(false); resource.refresh(); }
  }
  return <>
    <SectionHeader eyebrow="COMMUNICATIONS" title="Notifications" description="Preview message content and inspect delivery outcomes for this property." actions={<button className="button button-primary" onClick={() => { setCompose(true); setPreview(null); setDraft(null); action.clear(); }}>＋ Compose notification</button>} />
    <div className="inline-note warning">The backend currently supports email delivery and simulation mode. SMS and in-app delivery are not available.</div>
    {action.error && <div className="error-state">{action.error}</div>}{action.success && <div className="inline-success">{action.success}</div>}
    <Panel title="Notification history" subtitle="Recipient, delivery mode, status, retries, and failure details.">{resource.loading ? <Loading /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <DataTable rows={resource.data ?? []} columns={["recipient_email", "notification_type", "subject", "delivery_mode", "status", "retry_count", "sent_at"]} onOpen={(row) => setSelected(row)} emptyTitle="No notifications sent" emptyText="Send or simulate a notification after reviewing its preview." />}</Panel>
    {compose && <DetailModal title="Compose notification" onClose={() => setCompose(false)}><form id="notification-form" className="form-grid" onSubmit={getDraft}><Field label="Template" name="notification_type" type="select" options={["general", "overdue_rent", "compliance_alert", "sla_escalation", "owner_statement"]} /><Field label="Recipient email" name="recipient_email" type="email" required /><Field label="Recipient name" name="recipient_name" /><div className="form-field full"><label htmlFor="notification-context">Context fields (JSON)</label><textarea className="textarea" id="notification-context" name="context_data" defaultValue="{}" placeholder={'{"property_name":"..."}'} /></div>{action.error && <div className="error-state form-field full">{action.error}</div>}</form><div className="modal-footer"><button className="button button-secondary" onClick={() => setCompose(false)}>Close</button><button className="button button-primary" form="notification-form" type="submit" disabled={action.busy}>{action.busy ? "Preparing…" : "Preview message"}</button></div>
      {preview && <div className="modal-content" style={{ borderTop: "1px solid #edf0f0" }}><div className="detail-grid"><div className="detail-cell"><span>Recipient</span><strong>{displayValue(preview.recipient_email)}</strong></div><div className="detail-cell"><span>Template</span><strong>{displayValue(preview.notification_type)}</strong></div><div className="detail-cell"><span>Delivery</span><strong>Configured by backend</strong></div></div><div className="inline-note warning">Confirm before sending. Depending on backend configuration, this may deliver a live email.</div><div className="recon-box"><div className="context-key">Subject</div><div className="context-value">{displayValue(preview.subject)}</div></div><pre className="json-view" style={{ marginTop: 10 }}>{String(preview.body_text ?? "")}</pre><div className="form-actions"><button className="button button-primary" onClick={() => setConfirmSend(true)}>Confirm and send</button></div></div>}
    </DetailModal>}
    {confirmSend && <DetailModal title="Confirm notification delivery" value={preview} onClose={() => setConfirmSend(false)}><div className="warning-banner">Send this message to {displayValue(preview?.recipient_email)}? The backend configuration determines whether it is delivered live or simulated.</div>{action.error && <div className="error-state">{action.error}</div>}<div className="form-actions"><button className="button button-secondary" onClick={() => setConfirmSend(false)}>Go back</button><button className="button button-primary" disabled={action.busy} onClick={() => void send()}>{action.busy ? "Sending…" : "Confirm send"}</button></div></DetailModal>}
    {selected && <DetailModal title="Notification details" value={selected} onClose={() => setSelected(null)}><div className="detail-grid">{[["Recipient", selected.recipient_email], ["Type", selected.notification_type], ["Delivery mode", selected.delivery_mode], ["Status", selected.status], ["Sent at", selected.sent_at], ["Retries", selected.retry_count]].map(([label, value]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{displayValue(value)}</strong></div>)}</div>{Boolean(selected.error_message) && <div className="error-state">{String(selected.error_message)}</div>}<div className="recon-box" style={{ marginTop: 12 }}><div className="context-key">Subject</div><div className="context-value">{displayValue(selected.subject)}</div></div><pre className="json-view" style={{ marginTop: 10 }}>{String(selected.body_text ?? "")}</pre></DetailModal>}
  </>;
}

function Audit() {
  const resource = useResource<JsonRecord[]>("/audit/events?limit=250");
  const { activeMembership } = useSession();
  const [verification, setVerification] = useState<JsonRecord | null>(null);
  const [verificationError, setVerificationError] = useState("");
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const [busy, setBusy] = useState(false);
  async function verify() {
    setBusy(true); setVerificationError("");
    try { const result = await apiRequest<JsonRecord>("/audit/verify"); setVerification(result); }
    catch (reason) { setVerificationError(reason instanceof Error ? reason.message : "Audit verification failed."); }
    finally { setBusy(false); }
  }
  return <>
    <SectionHeader eyebrow="TAMPER-EVIDENT HISTORY" title="Audit trail" description="Review append-only actions and verify the cryptographic event chain." actions={<button className="button button-primary" onClick={verify} disabled={busy || !activeMembership?.permissions.includes("audit:verify")}>{busy ? "Verifying…" : "Verify audit chain"}</button>} />
    {verification && <div className={`inline-note ${verification.valid ? "" : "warning"}`}><strong>Chain {verification.valid ? "verified" : "verification failed"}.</strong> {displayValue(verification.events_checked)} events checked. {displayValue(verification.message)}{verification.first_invalid_sequence != null && ` First invalid sequence: ${verification.first_invalid_sequence}.`}</div>}
    {verificationError && <div className="error-state">{verificationError}</div>}
    <Panel title="Events" subtitle="Events are append-only. This screen does not provide edit or delete controls.">{resource.loading ? <Loading /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <DataTable rows={resource.data ?? []} columns={["created_at", "actor_id", "event_type", "entity_type", "entity_id", "property_id"]} onOpen={(row) => setSelected(row)} emptyTitle="No audit events" emptyText="Property actions will appear here as they occur." />}</Panel>
    {selected && <DetailModal title="Audit event" value={selected} onClose={() => setSelected(null)}><div className="detail-grid">{[["Event ID", selected.event_id], ["Sequence", selected.sequence_number], ["Timestamp", selected.created_at], ["Actor", selected.actor_id], ["Entity", `${displayValue(selected.entity_type)} · ${displayValue(selected.entity_id)}`], ["Property", selected.property_id]].map(([label, value]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{displayValue(value)}</strong></div>)}</div><h3 style={{ color: "#50676e", fontSize: 10, margin: "15px 0 8px" }}>Event payload</h3><pre className="json-view">{JSON.stringify(selected.payload, null, 2)}</pre><div className="detail-grid" style={{ marginTop: 12 }}><div className="detail-cell"><span>Previous hash</span><strong>{displayValue(selected.previous_hash)}</strong></div><div className="detail-cell"><span>Event hash</span><strong>{displayValue(selected.event_hash)}</strong></div><div className="detail-cell"><span>Evidence</span><strong>{displayValue(selected.evidence_ids)}</strong></div></div><div className="form-actions"><button className="button button-secondary" onClick={() => void navigator.clipboard.writeText(String(selected.event_id ?? ""))}>Copy event ID</button></div></DetailModal>}
  </>;
}

function Settings() {
  const { activeMembership, profile } = useSession();
  const base = propertyPath("");
  const resource = useResource<JsonRecord>(base);
  const action = useAction();
  const [savedMessage, setSavedMessage] = useState("");
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = formValues(event.currentTarget);
    const payload = { name: data.name, address: data.address || null, management_fee_rate: data.management_fee_rate || null };
    const result = await action.run(() => apiRequest(base, { method: "PATCH", body: JSON.stringify(payload) }), "Property details saved.");
    if (result) setSavedMessage("Property details saved.");
  }
  const property = resource.data;
  return <>
    <SectionHeader eyebrow="PROPERTY & ACCESS" title="Settings" description="Review your account permissions and update the property fields supported by the API." />
    {action.error && <div className="error-state">{action.error}</div>}{(savedMessage || action.success) && <div className="inline-success">{savedMessage || action.success}</div>}
    <div className="grid grid-2"><Panel title="Property details" subtitle="Only fields with backend update routes can be saved.">{resource.loading ? <Loading /> : resource.error ? <ErrorBox error={resource.error} retry={resource.refresh} /> : <div className="panel-body"><form id="property-settings" className="form-grid" onSubmit={save}><Field label="Property name" name="name" required defaultValue={String(property?.name ?? "")} /><Field label="Management fee rate" name="management_fee_rate" type="number" defaultValue={String(property?.management_fee_rate ?? "")} /><Field label="Address" name="address" className="full" defaultValue={String(property?.address ?? "")} /></form><div className="form-actions"><button className="button button-primary" type="submit" form="property-settings" disabled={action.busy}>{action.busy ? "Saving…" : "Save changes"}</button></div></div>}</Panel>
      <Panel title="Your access" subtitle="The server remains the authority for every permission check."><div className="panel-body"><div className="detail-grid">{[["Account", profile?.email], ["Property role", activeMembership?.role], ["Property", activeMembership?.property_name], ["Permissions", activeMembership?.permissions.length]].map(([label, value]) => <div className="detail-cell" key={String(label)}><span>{String(label)}</span><strong>{displayValue(value)}</strong></div>)}</div><h3 style={{ color: "#566c73", fontSize: 10, margin: "17px 0 9px" }}>Granted permissions</h3><div className="insight-list">{activeMembership?.permissions.map((permission) => <div className="insight" key={permission}><span className="insight-dot" style={{ background: "#56a78b" }} /><span>{permission}</span></div>)}</div><div className="inline-note warning">User and role administration, invitations, and password recovery are not supported by the current API.</div></div></Panel></div>
  </>;
}

function Reports() {
  return <><SectionHeader eyebrow="FINANCIAL INSIGHT" title="Reports" description="Review billed rent, realized collections, expenses, NOI, and owner statements." /><Panel title="Financial summary" subtitle="Choose a period and export a CSV or generate an owner statement."><FinancialReport /></Panel></>;
}

export function WorkspacePage({ section }: { section: string }) {
  switch (section) {
    case "dashboard": return <Dashboard />;
    case "work-queue": return <WorkQueue />;
    case "documents": return <Documents />;
    case "finance": return <Finance />;
    case "maintenance": return <Maintenance />;
    case "residents-leases": return <ResidentsLeases />;
    case "compliance": return <Compliance />;
    case "approvals": return <Approvals />;
    case "workflows": return <Workflows />;
    case "notifications": return <Notifications />;
    case "audit": return <Audit />;
    case "settings": return <Settings />;
    case "reports": return <Reports />;
    default: return <><SectionHeader eyebrow="ENIGMA" title="Page not found" description="This workspace route does not exist." /><Link href="/dashboard" className="button button-primary">Return to overview</Link></>;
  }
}
