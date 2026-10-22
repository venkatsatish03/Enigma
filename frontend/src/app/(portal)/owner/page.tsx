"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest, formatMoney } from "@/lib/api";
import type { JsonRecord } from "@/types";

type Listing = { listing_id: string; status: string; data: JsonRecord; media: JsonRecord[]; updated_at: string };
type BookingRequest = { request_id: string; listing_id: string; property_label: string; city?: string | null; country?: string | null; listing_type: string; price_amount?: string | null; price_currency: string; tenant_email: string; desired_move_in_date?: string | null; message?: string | null; status: string; decision_note?: string | null; created_at: string; updated_at: string };
const dateText = (value: unknown) => value ? new Date(String(value)).toLocaleDateString() : "Date not set";

function Panel({ title, subtitle, href, hrefLabel, children }: { title: string; subtitle: string; href?: string; hrefLabel?: string; children: React.ReactNode }) {
  return <section className="portal-dashboard-panel"><div className="portal-section-heading" style={{ margin: "0 0 7px" }}><div><h2>{title}</h2></div>{href && <Link className="table-link" href={href}>{hrefLabel ?? "View all →"}</Link>}</div><p>{subtitle}</p>{children}</section>;
}

export default function OwnerDashboardPage() {
  const [listings, setListings] = useState<Listing[]>([]);
  const [requests, setRequests] = useState<BookingRequest[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [actionId, setActionId] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let active = true;
    setBusy(true);
    Promise.all([
      apiRequest<Listing[]>("/portal/owner/listings"),
      apiRequest<BookingRequest[]>("/portal/owner/booking-requests"),
    ]).then(([nextListings, nextRequests]) => {
      if (active) { setListings(nextListings); setRequests(nextRequests); setError(""); }
    }).catch(() => { if (active) setError("Your owner dashboard could not be loaded."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [revision]);

  const available = useMemo(() => listings.filter((item) => item.status === "approved" && item.data.property_status === "available"), [listings]);
  const underOffer = useMemo(() => listings.filter((item) => item.status === "approved" && item.data.property_status === "under_offer"), [listings]);
  const drafts = useMemo(() => listings.filter((item) => item.status === "draft"), [listings]);
  const pendingRequests = requests.filter((request) => request.status === "pending");
  const latestListings = [...listings].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 5);

  async function decide(request: BookingRequest, status: "accepted" | "declined") {
    setActionId(request.request_id); setNotice("");
    try {
      await apiRequest(`/portal/owner/booking-requests/${encodeURIComponent(request.request_id)}`, { method: "PATCH", body: JSON.stringify({ status }) });
      setNotice(status === "accepted" ? "Request accepted. The property is now marked under offer." : "Booking request declined.");
      refresh();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "The booking request could not be updated.");
    } finally { setActionId(""); }
  }

  return <div className="portal-content">
    <div className="portal-heading"><div><div className="eyebrow">YOUR PROPERTY</div><h1>Owner dashboard</h1><p>Keep an eye on your properties, availability, and tenant booking requests.</p></div><Link className="button button-primary" href="/owner/new">Add a property <span aria-hidden="true">＋</span></Link></div>
    {busy && <div className="portal-loading"><span className="spinner spinner-dark" />Loading your owner dashboard…</div>}
    {error && <div className="inline-error">{error} <button className="text-button" onClick={refresh}>Try again</button></div>}
    {!busy && !error && <>
      <div className="grid grid-4">
        <div className="metric-card"><div className="metric-top"><span>Available properties</span><span className="metric-icon">⌂</span></div><div><div className="metric-value">{available.length}</div><div className="metric-note">Approved and visible to tenants</div></div></div>
        <div className="metric-card"><div className="metric-top"><span>Pending booking requests</span><span className="metric-icon">◷</span></div><div><div className="metric-value">{pendingRequests.length}</div><div className="metric-note">Need your response</div></div></div>
        <div className="metric-card"><div className="metric-top"><span>Properties under offer</span><span className="metric-icon">✓</span></div><div><div className="metric-value">{underOffer.length}</div><div className="metric-note">Not accepting new requests</div></div></div>
        <div className="metric-card"><div className="metric-top"><span>Rent received / costs due</span><span className="metric-icon">$</span></div><div><div className="metric-value portal-money-unavailable">Not linked</div><div className="metric-note">Owner listings are not linked to financial records</div></div></div>
      </div>
      <div className="portal-dashboard-quick"><Link className="button button-secondary" href="/owner/properties">Manage properties</Link><Link className="button button-secondary" href="/owner#booking-requests">Review booking requests</Link><button className="button button-secondary" onClick={refresh}>Refresh dashboard</button></div>
      {notice && <div className="inline-success" role="status">{notice}</div>}
      {listings.length === 0 && <div className="inline-note">Add a property to build your portfolio. Complete listings become approved and visible to tenants as soon as you publish them.</div>}
      <div className="portal-dashboard-grid">
        <Panel title="Needs attention" subtitle="Booking requests to review and drafts that still need details." href="#booking-requests" hrefLabel="Review requests →">
          {pendingRequests.length || drafts.length ? <div className="portal-dashboard-list">
            {pendingRequests.slice(0, 4).map((request) => <div className="portal-dashboard-row" key={request.request_id}><span className="status-badge status-pending">pending</span><div><strong>{request.property_label}</strong><span>{request.tenant_email} · requested {dateText(request.created_at)}</span></div><a className="table-link" href="#booking-requests">Open</a></div>)}
            {drafts.slice(0, 3).map((listing) => <div className="portal-dashboard-row" key={listing.listing_id}><span className="status-badge">draft</span><div><strong>{String(listing.data.street_address ?? "Property draft")}</strong><span>Continue adding property details</span></div><Link className="table-link" href={`/owner/listings/${listing.listing_id}`}>Continue</Link></div>)}
          </div> : <div className="portal-dashboard-empty">No pending requests or unfinished drafts.</div>}
        </Panel>
        <Panel title="Property holdings" subtitle="Current availability across your properties." href="/owner/properties" hrefLabel="View properties →">
          {latestListings.length ? <div className="portal-dashboard-list">{latestListings.map((listing) => <div className="portal-dashboard-row" key={listing.listing_id}><span className={`status-badge status-${listing.status}`}>{listing.status === "approved" ? String(listing.data.property_status ?? "available").replaceAll("_", " ") : listing.status}</span><div><strong>{String(listing.data.street_address ?? "Property draft")}</strong><span>{[listing.data.city, listing.data.country].filter(Boolean).join(", ") || "Location not added"} · {String(listing.data.listing_type ?? "for rent").replaceAll("_", " ")}</span></div></div>)}</div> : <div className="portal-dashboard-empty">Your properties and drafts will appear here.</div>}
        </Panel>
        <Panel title="Booking request activity" subtitle="Respond to tenants asking to book an available property." href="#booking-requests" hrefLabel="Open request queue →">
          {requests.length ? <div className="portal-dashboard-list">{requests.slice(0, 5).map((request) => <div className="portal-dashboard-row" key={request.request_id}><span className={`status-badge status-${request.status}`}>{request.status}</span><div><strong>{request.property_label}</strong><span>{request.tenant_email} · {dateText(request.created_at)}</span></div></div>)}</div> : <div className="portal-dashboard-empty">Tenant booking requests will appear here.</div>}
        </Panel>
        <Panel title="Financial activity" subtitle="Rent receipts and costs will appear when your property listings are connected to the accounting records.">
          <div className="portal-dashboard-empty">There are no financial records linked to your owner listings yet. Amounts are not estimated from listing prices.</div>
        </Panel>
      </div>
      <section className="portal-panel request-panel" id="booking-requests"><div className="portal-section-heading"><div><div className="section-eyebrow">OWNER TASKS</div><h2>Booking requests</h2></div></div>
        {requests.length ? <div className="portal-dashboard-list">{requests.map((request) => <article className="portal-dashboard-row" key={request.request_id}><span className={`status-badge status-${request.status}`}>{request.status}</span><div><strong>{request.property_label} · {request.tenant_email}</strong><span>{[request.city, request.country].filter(Boolean).join(", ")} · {request.desired_move_in_date ? `Move in ${dateText(request.desired_move_in_date)}` : `Requested ${dateText(request.created_at)}`}</span>{request.message && <small>{request.message}</small>}{request.decision_note && <small>{request.decision_note}</small>}</div>{request.status === "pending" ? <div className="table-actions"><button className="button button-secondary" disabled={actionId === request.request_id} onClick={() => void decide(request, "declined")}>Decline</button><button className="button button-primary" disabled={actionId === request.request_id} onClick={() => void decide(request, "accepted")}>{actionId === request.request_id ? "Saving…" : "Accept"}</button></div> : request.status === "accepted" ? <a className="table-link" href={`mailto:${request.tenant_email}?subject=${encodeURIComponent(`Booking request for ${request.property_label}`)}`}>Contact tenant</a> : null}</article>)}</div> : <div className="portal-dashboard-empty">No booking requests yet.</div>}
      </section>
    </>}
  </div>;
}
