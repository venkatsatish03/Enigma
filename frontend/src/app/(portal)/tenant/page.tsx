"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { apiRequest, formatMoney } from "@/lib/api";
import type { JsonRecord } from "@/types";

type TenantProperty = { property_id: string; property_name: string; leases: JsonRecord[]; invoices: JsonRecord[]; payments: JsonRecord[]; maintenance_requests: JsonRecord[] };
type TenantDashboard = { properties: TenantProperty[]; has_linked_resident: boolean };
type BookingRequest = { request_id: string; listing_id: string; property_label: string; city?: string | null; country?: string | null; owner_email?: string | null; status: string; decision_note?: string | null; created_at: string };
const amount = (value: unknown) => Number(value ?? 0) || 0;
const dateText = (value: unknown) => value ? new Date(String(value)).toLocaleDateString() : "Date not set";
const openStatuses = new Set(["completed", "cancelled", "closed"]);
const invoiceDue = (invoice: JsonRecord) => Math.max(0, amount(invoice.outstanding_amount ?? (amount(invoice.amount) - amount(invoice.amount_paid))));

function moneyGroups(rows: JsonRecord[], getAmount: (row: JsonRecord) => number) {
  const sums = new Map<string, number>();
  rows.forEach((row) => { const currency = String(row.currency ?? "USD"); sums.set(currency, (sums.get(currency) ?? 0) + getAmount(row)); });
  return sums.size ? Array.from(sums, ([currency, value]) => formatMoney(value, currency)).join(" · ") : "—";
}

function Panel({ title, subtitle, href, hrefLabel, children }: { title: string; subtitle: string; href?: string; hrefLabel?: string; children: React.ReactNode }) {
  return <section className="portal-dashboard-panel"><div className="portal-section-heading" style={{ margin: "0 0 7px" }}><div><h2>{title}</h2></div>{href && <Link className="table-link" href={href}>{hrefLabel ?? "View all →"}</Link>}</div><p>{subtitle}</p>{children}</section>;
}

export default function TenantDashboardPage() {
  const [dashboard, setDashboard] = useState<TenantDashboard | null>(null);
  const [bookingRequests, setBookingRequests] = useState<BookingRequest[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [range, setRange] = useState("month");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setBusy(true);
    Promise.all([
      apiRequest<TenantDashboard>("/portal/tenant/dashboard"),
      apiRequest<BookingRequest[]>("/portal/tenant/booking-requests").catch(() => []),
    ]).then(([nextDashboard, requests]) => {
      if (active) { setDashboard(nextDashboard); setBookingRequests(requests); setError(""); }
    }).catch(() => { if (active) setError("Your dashboard information could not be loaded."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [revision]);

  const [rangeStart, rangeEnd] = useMemo(() => {
    const end = new Date(); const start = new Date();
    if (range === "month") start.setDate(1);
    if (range === "quarter") start.setMonth(Math.floor(start.getMonth() / 3) * 3, 1);
    if (range === "year") start.setMonth(0, 1);
    return [start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)];
  }, [range]);

  const properties = dashboard?.properties ?? [];
  const invoices = properties.flatMap((property) => property.invoices.map((invoice) => ({ ...invoice, property_name: property.property_name })));
  const payments = properties.flatMap((property) => property.payments.map((payment) => ({ ...payment, property_name: property.property_name })));
  const maintenance = properties.flatMap((property) => property.maintenance_requests.map((request) => ({ ...request, property_name: property.property_name })));
  const leases = properties.flatMap((property) => property.leases);
  const activeLeases = leases.filter((lease) => String(lease.status ?? "").toLowerCase() === "active");
  const dueInvoices = invoices.filter((invoice) => invoiceDue(invoice) > 0 && !["cancelled", "void"].includes(String(invoice.status ?? "").toLowerCase()));
  const periodInvoices = invoices.filter((invoice) => String(invoice.issue_date ?? "").slice(0, 10) >= rangeStart && String(invoice.issue_date ?? "").slice(0, 10) <= rangeEnd);
  const periodPayments = payments.filter((payment) => ["settled", "reconciled"].includes(String(payment.status ?? "").toLowerCase()) && String(payment.payment_date ?? "").slice(0, 10) >= rangeStart && String(payment.payment_date ?? "").slice(0, 10) <= rangeEnd);
  const openMaintenance = maintenance.filter((request) => !openStatuses.has(String(request.status ?? "").toLowerCase()));
  const attention = [
    ...dueInvoices.sort((a, b) => String(a.due_date ?? "").localeCompare(String(b.due_date ?? ""))).slice(0, 4).map((invoice) => ({ title: String(invoice.description ?? invoice.invoice_number ?? "Rent or other charge"), meta: `${String(invoice.property_name)} · due ${dateText(invoice.due_date)} · ${formatMoney(invoiceDue(invoice), String(invoice.currency ?? "USD"))}`, state: String(invoice.status ?? "unpaid").replaceAll("_", " ") })),
    ...bookingRequests.filter((request) => request.status === "accepted").slice(0, 2).map((request) => ({ title: `Booking request accepted`, meta: `${request.property_label} · ${[request.city, request.country].filter(Boolean).join(", ")}`, state: "accepted" })),
  ].slice(0, 5);
  const recent = [
    ...periodPayments.map((payment) => ({ id: String(payment.payment_id ?? payment.id), title: `Payment · ${String(payment.property_name)}`, meta: `${dateText(payment.payment_date)} · ${formatMoney(payment.amount, String(payment.currency ?? "USD"))}`, date: String(payment.payment_date ?? "") })),
    ...maintenance.map((request) => ({ id: String(request.request_id), title: `Maintenance · ${String(request.property_name)}`, meta: `${String(request.status ?? "open").replaceAll("_", " ")} · ${dateText(request.created_at)}`, date: String(request.created_at ?? "") })),
  ].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 5);

  return <div className="portal-content">
    <div className="portal-heading"><div><div className="eyebrow">YOUR HOME</div><h1>Tenant dashboard</h1><p>Track your rent, current tenancy, maintenance, and booking requests.</p></div><label className="portal-field"><span>Financial period</span><select className="select" value={range} onChange={(event) => setRange(event.target.value)} aria-label="Financial period"><option value="month">This month</option><option value="quarter">This quarter</option><option value="year">This year</option></select></label></div>
    {busy && <div className="portal-loading"><span className="spinner spinner-dark" />Loading your dashboard…</div>}
    {error && <div className="inline-error">{error} <button className="text-button" onClick={() => setRevision((value) => value + 1)}>Try again</button></div>}
    {!busy && !error && <>
      <div className="grid grid-4">
        <div className="metric-card"><div className="metric-top"><span>Rent and charges due</span><span className="metric-icon">◷</span></div><div><div className="metric-value">{moneyGroups(dueInvoices, invoiceDue)}</div><div className="metric-note">Across your linked tenancies</div></div></div>
        <div className="metric-card"><div className="metric-top"><span>Paid this period</span><span className="metric-icon">↗</span></div><div><div className="metric-value">{moneyGroups(periodPayments, (payment) => amount(payment.amount))}</div><div className="metric-note">Settled or reconciled payments</div></div></div>
        <div className="metric-card"><div className="metric-top"><span>Rent and charges billed</span><span className="metric-icon">$</span></div><div><div className="metric-value">{moneyGroups(periodInvoices, (invoice) => amount(invoice.amount))}</div><div className="metric-note">Invoices issued in selected period</div></div></div>
        <div className="metric-card"><div className="metric-top"><span>Active tenancies</span><span className="metric-icon">⌂</span></div><div><div className="metric-value">{activeLeases.length}</div><div className="metric-note">Across {properties.length} linked propert{properties.length === 1 ? "y" : "ies"}</div></div></div>
      </div>
      <div className="portal-dashboard-quick"><Link className="button button-primary" href="/tenant/properties#available-listings">Browse available properties</Link><Link className="button button-secondary" href="/tenant/properties#maintenance">Request maintenance</Link><button className="button button-secondary" onClick={() => setRevision((value) => value + 1)}>Refresh dashboard</button></div>
      {!dashboard?.has_linked_resident && <div className="inline-note">No tenancy is linked to your account yet. You can still browse available properties and send booking requests.</div>}
      <div className="portal-dashboard-grid">
        <Panel title="Needs attention" subtitle="Charges, requests, and decisions that may need a follow-up." href="/tenant/properties" hrefLabel="View your home →">
          {attention.length ? <div className="portal-dashboard-list">{attention.map((item, index) => <div className="portal-dashboard-row" key={`${item.title}-${index}`}><span className={`status-badge status-${item.state.replaceAll(" ", "_")}`}>{item.state}</span><div><strong>{item.title}</strong><span>{item.meta}</span></div></div>)}</div> : <div className="portal-dashboard-empty">You are all caught up. New charges and booking decisions will appear here.</div>}
        </Panel>
        <Panel title="Maintenance & SLA" subtitle="Open maintenance requests and their current status." href="/tenant/properties#maintenance" hrefLabel="View requests →">
          {openMaintenance.length ? <div className="portal-dashboard-list">{openMaintenance.slice(0, 5).map((request) => <div className="portal-dashboard-row" key={String(request.request_id)}><span className={`status-badge status-${String(request.status ?? "open")}`}>{String(request.status ?? "open").replaceAll("_", " ")}</span><div><strong>{String(request.category ?? request.description ?? "Maintenance request")}</strong><span>{String(request.property_name)} · reported {dateText(request.created_at)}</span></div></div>)}</div> : <div className="portal-dashboard-empty">No open maintenance requests.</div>}
        </Panel>
        <Panel title="Booking request activity" subtitle="Track responses from property owners." href="/tenant/properties#booking-requests" hrefLabel="View requests →">
          {bookingRequests.length ? <div className="portal-dashboard-list">{bookingRequests.slice(0, 5).map((request) => <div className="portal-dashboard-row" key={request.request_id}><span className={`status-badge status-${request.status}`}>{request.status}</span><div><strong>{request.property_label}</strong><span>{[request.city, request.country].filter(Boolean).join(", ")} · {dateText(request.created_at)}</span></div>{request.status === "accepted" && request.owner_email && <a className="table-link" href={`mailto:${request.owner_email}?subject=${encodeURIComponent(`Booking request for ${request.property_label}`)}`}>Contact owner</a>}</div>)}</div> : <div className="portal-dashboard-empty">Your booking requests and owner responses will appear here.</div>}
        </Panel>
        <Panel title="Recent activity" subtitle="Payments and maintenance updates in the selected period." href="/tenant/properties" hrefLabel="Open properties →">
          {recent.length ? <div className="portal-dashboard-list">{recent.map((item) => <div className="portal-dashboard-row" key={`${item.title}-${item.id}`}><span className="metric-icon">{item.title.startsWith("Payment") ? "↗" : "⌁"}</span><div><strong>{item.title}</strong><span>{item.meta}</span></div></div>)}</div> : <div className="portal-dashboard-empty">Recent payments and requests will appear here.</div>}
        </Panel>
      </div>
    </>}
  </div>;
}
