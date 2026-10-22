"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiRequest, formatMoney } from "@/lib/api";
import type { JsonRecord } from "@/types";

type TenantProperty = { property_id: string; property_name: string; resident: JsonRecord; leases: JsonRecord[]; units: JsonRecord[]; invoices: JsonRecord[]; payments: JsonRecord[]; maintenance_requests: JsonRecord[] };
type TenantDashboard = { properties: TenantProperty[]; has_linked_resident: boolean };
const text = (value: unknown, fallback = "—") => typeof value === "string" && value ? value : value === null || value === undefined ? fallback : String(value);
const money = (row: JsonRecord) => formatMoney(row.amount ?? row.total_amount ?? row.balance ?? row.rent_amount, String(row.currency ?? "USD"));
const field = (row: JsonRecord, ...names: string[]) => names.map((name) => row[name]).find((value) => value !== null && value !== undefined && value !== "");

export default function TenantPortalPage() {
  const [dashboard, setDashboard] = useState<TenantDashboard | null>(null); const [error, setError] = useState(""); const [busy, setBusy] = useState(true);
  const [requestUnit, setRequestUnit] = useState(""); const [description, setDescription] = useState(""); const [requestBusy, setRequestBusy] = useState(false); const [notice, setNotice] = useState("");
  const reload = useCallback(async () => { setBusy(true); try { setDashboard(await apiRequest<TenantDashboard>("/portal/tenant/dashboard")); setError(""); } catch { setError("Your tenant information could not be loaded. Please try again later."); } finally { setBusy(false); } }, []);
  useEffect(() => { void reload(); }, [reload]);

  const currentTenancies = useMemo(() => dashboard?.properties.flatMap((property) => property.leases.filter((lease) => String(lease.status ?? "").toLowerCase() === "active").map((lease) => ({ property, lease }))) ?? [], [dashboard]);
  async function submitRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const selected = currentTenancies.find(({ property, lease }) => `${property.property_id}:${lease.unit_id}` === requestUnit);
    if (!selected) { setNotice("Choose a unit from your active tenancy."); return; }
    setRequestBusy(true); setNotice("");
    try {
      await apiRequest("/portal/tenant/maintenance-requests", { method: "POST", body: JSON.stringify({ property_id: selected.property.property_id, unit_id: String(selected.lease.unit_id), description }) });
      setDescription(""); setRequestUnit(""); setNotice("Your maintenance request has been sent to the property team."); await reload();
    } catch { setNotice("The maintenance request could not be sent. Check the selected active tenancy and try again."); }
    finally { setRequestBusy(false); }
  }

  return <div className="portal-content">
    <div className="portal-heading"><div><div className="eyebrow">YOUR HOME</div><h1>Tenant portal</h1><p>View your tenancy, rent charges, payments, and maintenance requests.</p></div><Link className="button button-secondary" href="/owner">Switch to owner portal <span aria-hidden="true">→</span></Link></div>
    {busy && <div className="portal-loading"><span className="spinner spinner-dark" />Loading your tenancy…</div>}
    {error && <div className="inline-error">{error} <button className="text-button" onClick={() => void reload()}>Try again</button></div>}
    {!busy && !error && dashboard && !dashboard.has_linked_resident && <section className="portal-panel portal-empty"><div className="portal-icon">⌂</div><h2>No tenancy linked yet</h2><p>Your tenant account is ready. Once your property manager adds this email to your resident record, your lease, rent, payments, and maintenance requests will appear here.</p><Link className="button button-primary" href="/owner">I own a property <span aria-hidden="true">→</span></Link></section>}
    {!busy && dashboard?.properties.map((property) => <section className="portal-panel tenant-property" key={property.property_id}>
      <div className="portal-section-heading"><div><div className="section-eyebrow">PROPERTY</div><h2>{property.property_name}</h2></div><span className="status-badge status-active">Tenant account linked</span></div>
      <div className="portal-stat-grid">
        <div className="portal-stat"><span>Active tenancies</span><strong>{property.leases.filter((lease) => String(lease.status ?? "").toLowerCase() === "active").length}</strong></div>
        <div className="portal-stat"><span>Outstanding invoices</span><strong>{property.invoices.filter((invoice) => !["paid", "void", "cancelled"].includes(String(invoice.status ?? "").toLowerCase())).length}</strong></div>
        <div className="portal-stat"><span>Maintenance requests</span><strong>{property.maintenance_requests.length}</strong></div>
      </div>
      <div className="portal-grid-two">
        <section className="portal-subpanel"><h3>Tenancy details</h3>{property.leases.length ? property.leases.map((lease) => {
          const unit = property.units.find((item) => item.unit_id === lease.unit_id);
          return <div className="portal-record" key={String(lease.lease_id ?? lease.id)}><div><strong>{text(unit?.unit_number ?? lease.unit_id, "Unit")}</strong><span>{text(lease.start_date)} – {text(lease.end_date, "Ongoing")}</span></div><div className="portal-record-end"><b>{formatMoney(lease.rent_amount, String(lease.currency ?? "USD"))}</b><span className="status-badge">{text(lease.status)}</span></div></div>;
        }) : <p className="portal-muted">No lease record is linked yet.</p>}</section>
        <section className="portal-subpanel"><h3>Maintenance requests</h3>{property.maintenance_requests.length ? property.maintenance_requests.map((request) => <div className="portal-record" key={String(request.request_id)}><div><strong>{text(request.description, "Maintenance request")}</strong><span>{text(request.created_at)}</span></div><span className={`status-badge status-${String(request.status ?? "open").toLowerCase()}`}>{text(request.status, "Open")}</span></div>) : <p className="portal-muted">No requests have been submitted.</p>}</section>
      </div>
      <div className="portal-grid-two portal-lower-grid">
        <section className="portal-subpanel"><h3>Rent and other charges</h3>{property.invoices.length ? property.invoices.map((invoice) => <div className="portal-record" key={String(invoice.invoice_id ?? invoice.id)}><div><strong>{text(field(invoice, "description", "invoice_number", "invoice_id"), "Invoice")}</strong><span>Due {text(invoice.due_date)} · {text(invoice.status)}</span></div><b>{money(invoice)}</b></div>) : <p className="portal-muted">No invoices are linked to this account.</p>}</section>
        <section className="portal-subpanel"><h3>Payment history</h3>{property.payments.length ? property.payments.map((payment) => <div className="portal-record" key={String(payment.payment_id ?? payment.id)}><div><strong>{text(field(payment, "reference", "payment_id"), "Payment")}</strong><span>{text(payment.payment_date ?? payment.created_at)} · {text(payment.status)}</span></div><b>{money(payment)}</b></div>) : <p className="portal-muted">No payments are recorded yet.</p>}</section>
      </div>
    </section>)}
    {!busy && currentTenancies.length > 0 && <section className="portal-panel request-panel"><div className="portal-section-heading"><div><div className="section-eyebrow">SUPPORT</div><h2>Request maintenance</h2></div></div>
      <form className="portal-request-form" onSubmit={submitRequest}><label className="portal-field"><span>Active tenancy</span><select className="select" value={requestUnit} onChange={(event) => setRequestUnit(event.target.value)} required><option value="">Choose a unit</option>{currentTenancies.map(({ property, lease }) => <option key={`${property.property_id}:${lease.unit_id}`} value={`${property.property_id}:${lease.unit_id}`}>{property.property_name} · {text(property.units.find((item) => item.unit_id === lease.unit_id)?.unit_number ?? lease.unit_id, "Unit")}</option>)}</select></label><label className="portal-field portal-request-description"><span>What needs attention?</span><textarea className="textarea" rows={3} maxLength={5000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Describe the maintenance issue" required /></label><button className="button button-primary" disabled={requestBusy}>{requestBusy ? "Sending…" : "Send request"}</button></form>{notice && <p className={notice.startsWith("Your") ? "inline-success" : "inline-error"}>{notice}</p>}
    </section>}
  </div>;
}
