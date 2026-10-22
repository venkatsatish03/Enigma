"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiRequest, formatMoney, readToken } from "@/lib/api";
import type { JsonRecord } from "@/types";

type TenantProperty = { property_id: string; property_name: string; resident: JsonRecord; leases: JsonRecord[]; units: JsonRecord[]; invoices: JsonRecord[]; payments: JsonRecord[]; maintenance_requests: JsonRecord[] };
type TenantDashboard = { properties: TenantProperty[]; has_linked_resident: boolean };
type ListingMedia = { media_id: string; mime_type: string; media_kind: string };
type Listing = { listing_id: string; status: string; data: JsonRecord; media: ListingMedia[]; updated_at: string };
type BookingRequest = { request_id: string; listing_id: string; property_label: string; city?: string | null; country?: string | null; owner_email?: string | null; status: string; decision_note?: string | null; created_at: string };
const text = (value: unknown, fallback = "—") => typeof value === "string" && value ? value : value === null || value === undefined ? fallback : String(value);
const money = (row: JsonRecord) => formatMoney(row.amount ?? row.total_amount ?? row.balance ?? row.rent_amount, String(row.currency ?? "USD"));
const field = (row: JsonRecord, ...names: string[]) => names.map((name) => row[name]).find((value) => value !== null && value !== undefined && value !== "");
const dueAmount = (invoice: JsonRecord) => Math.max(0, Number(invoice.outstanding_amount ?? (Number(invoice.amount ?? 0) - Number(invoice.amount_paid ?? 0))));
const outstandingText = (invoices: JsonRecord[]) => {
  const sums = new Map<string, number>();
  invoices.forEach((invoice) => { const currency = String(invoice.currency ?? "USD"); sums.set(currency, (sums.get(currency) ?? 0) + dueAmount(invoice)); });
  return sums.size ? Array.from(sums, ([currency, value]) => formatMoney(value, currency)).join(" · ") : "—";
};

function ListingPhoto({ listing }: { listing: Listing }) {
  const photo = listing.media.find((item) => item.mime_type.startsWith("image/"));
  const [src, setSrc] = useState("");
  useEffect(() => {
    if (!photo) return;
    const controller = new AbortController();
    const token = readToken();
    let objectUrl = "";
    fetch(`/api/backend/api/v1/portal/tenant/listings/${encodeURIComponent(listing.listing_id)}/media/${encodeURIComponent(photo.media_id)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {}, cache: "no-store", signal: controller.signal,
    }).then((response) => { if (!response.ok) throw new Error("Photo unavailable"); return response.blob(); })
      .then((blob) => { if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setSrc(objectUrl); } })
      .catch(() => undefined);
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [listing.listing_id, photo?.media_id]);
  return src ? <img className="marketplace-photo" src={src} alt={`Photo of ${String(listing.data.street_address ?? "property")}`} /> : <div className="marketplace-photo-placeholder" aria-hidden="true">⌂</div>;
}

export default function TenantPropertiesPage() {
  const [dashboard, setDashboard] = useState<TenantDashboard | null>(null);
  const [listings, setListings] = useState<Listing[]>([]);
  const [bookingRequests, setBookingRequests] = useState<BookingRequest[]>([]);
  const [error, setError] = useState(""); const [marketplaceError, setMarketplaceError] = useState(""); const [busy, setBusy] = useState(true);
  const [requestUnit, setRequestUnit] = useState(""); const [description, setDescription] = useState(""); const [requestBusy, setRequestBusy] = useState(false); const [notice, setNotice] = useState("");
  const [bookingTarget, setBookingTarget] = useState(""); const [desiredMoveIn, setDesiredMoveIn] = useState(""); const [bookingMessage, setBookingMessage] = useState(""); const [bookingBusy, setBookingBusy] = useState(false);

  const reload = useCallback(async () => {
    setBusy(true);
    const [tenantResult, listingResult, bookingResult] = await Promise.allSettled([
      apiRequest<TenantDashboard>("/portal/tenant/dashboard"),
      apiRequest<Listing[]>("/portal/tenant/listings"),
      apiRequest<BookingRequest[]>("/portal/tenant/booking-requests"),
    ]);
    if (tenantResult.status === "fulfilled") { setDashboard(tenantResult.value); setError(""); }
    else setError("Your tenant information could not be loaded. Please try again later.");
    if (listingResult.status === "fulfilled") { setListings(listingResult.value); setMarketplaceError(""); }
    else setMarketplaceError("Available properties could not be loaded.");
    if (bookingResult.status === "fulfilled") setBookingRequests(bookingResult.value);
    setBusy(false);
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  const currentTenancies = useMemo(() => dashboard?.properties.flatMap((property) => property.leases.filter((lease) => String(lease.status ?? "").toLowerCase() === "active").map((lease) => ({ property, lease }))) ?? [], [dashboard]);
  async function submitMaintenance(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const selected = currentTenancies.find(({ property, lease }) => `${property.property_id}:${lease.unit_id}` === requestUnit);
    if (!selected) { setNotice("Choose a unit from your active tenancy."); return; }
    setRequestBusy(true); setNotice("");
    try {
      await apiRequest("/portal/tenant/maintenance-requests", { method: "POST", body: JSON.stringify({ property_id: selected.property.property_id, unit_id: String(selected.lease.unit_id), description }) });
      setDescription(""); setRequestUnit(""); setNotice("Your maintenance request has been sent to the property team."); await reload();
    } catch { setNotice("The maintenance request could not be sent. Check the selected active tenancy and try again."); }
    finally { setRequestBusy(false); }
  }
  async function submitBooking(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!bookingTarget) return;
    setBookingBusy(true); setNotice("");
    try {
      await apiRequest("/portal/tenant/booking-requests", { method: "POST", body: JSON.stringify({ listing_id: bookingTarget, desired_move_in_date: desiredMoveIn || null, message: bookingMessage.trim() || null }) });
      setNotice("Your booking request has been sent to the owner."); setBookingTarget(""); setDesiredMoveIn(""); setBookingMessage(""); await reload();
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : "The booking request could not be sent."); }
    finally { setBookingBusy(false); }
  }

  return <div className="portal-content">
    <div className="portal-heading"><div><div className="eyebrow">YOUR HOME</div><h1>My home & listings</h1><p>Review your current tenancy, rent and maintenance, and browse available properties.</p></div><Link className="button button-secondary" href="/tenant">← Dashboard</Link></div>
    {busy && <div className="portal-loading"><span className="spinner spinner-dark" />Loading your home…</div>}
    {error && <div className="inline-error">{error} <button className="text-button" onClick={() => void reload()}>Try again</button></div>}
    {!busy && !error && dashboard && !dashboard.has_linked_resident && <section className="portal-panel portal-empty"><div className="portal-icon">⌂</div><h2>No tenancy linked yet</h2><p>Your tenant account is ready. A property manager can link your email to an existing tenancy. You can still browse available properties and request a booking below.</p></section>}
    {!busy && dashboard?.properties.map((property) => {
      const outstandingInvoices = property.invoices.filter((invoice) => dueAmount(invoice) > 0 && !["cancelled", "void"].includes(String(invoice.status ?? "").toLowerCase()));
      return <section className="portal-panel tenant-property" key={property.property_id}>
        <div className="portal-section-heading"><div><div className="section-eyebrow">CURRENT HOLDING</div><h2>{property.property_name}</h2></div><span className="status-badge status-active">Tenant account linked</span></div>
        <div className="portal-stat-grid">
          <div className="portal-stat"><span>Active tenancies</span><strong>{property.leases.filter((lease) => String(lease.status ?? "").toLowerCase() === "active").length}</strong></div>
          <div className="portal-stat"><span>Amount to pay</span><strong>{outstandingText(outstandingInvoices)}</strong></div>
          <div className="portal-stat"><span>Open maintenance</span><strong>{property.maintenance_requests.filter((request) => !["completed", "cancelled", "closed"].includes(String(request.status ?? "").toLowerCase())).length}</strong></div>
        </div>
        <div className="portal-grid-two">
          <section className="portal-subpanel"><h3>Tenancy details</h3>{property.leases.length ? property.leases.map((lease) => {
            const unit = property.units.find((item) => item.unit_id === lease.unit_id);
            return <div className="portal-record" key={String(lease.lease_id ?? lease.id)}><div><strong>{text(unit?.unit_number ?? lease.unit_id, "Unit")}</strong><span>{text(lease.start_date)} – {text(lease.end_date, "Ongoing")}</span></div><div className="portal-record-end"><b>{formatMoney(lease.rent_amount, String(lease.currency ?? "USD"))}</b><span className="status-badge">{text(lease.status)}</span></div></div>;
          }) : <p className="portal-muted">No lease record is linked yet.</p>}</section>
          <section className="portal-subpanel"><h3>Maintenance requests</h3>{property.maintenance_requests.length ? property.maintenance_requests.map((request) => <div className="portal-record" key={String(request.request_id)}><div><strong>{text(request.description, "Maintenance request")}</strong><span>{text(request.created_at)}</span></div><span className={`status-badge status-${String(request.status ?? "open").toLowerCase()}`}>{text(request.status, "Open")}</span></div>) : <p className="portal-muted">No requests have been submitted.</p>}</section>
        </div>
        <div className="portal-grid-two portal-lower-grid">
          <section className="portal-subpanel"><h3>Rent and other charges</h3>{property.invoices.length ? property.invoices.map((invoice) => <div className="portal-record" key={String(invoice.invoice_id ?? invoice.id)}><div><strong>{text(field(invoice, "description", "invoice_number", "invoice_id"), "Invoice")}</strong><span>Due {text(invoice.due_date)} · {text(invoice.status)}</span></div><b>{formatMoney(dueAmount(invoice), String(invoice.currency ?? "USD"))}</b></div>) : <p className="portal-muted">No invoices are linked to this account.</p>}</section>
          <section className="portal-subpanel"><h3>Payment history</h3>{property.payments.length ? property.payments.map((payment) => <div className="portal-record" key={String(payment.payment_id ?? payment.id)}><div><strong>{text(field(payment, "reference", "reference_number", "payment_id"), "Payment")}</strong><span>{text(payment.payment_date ?? payment.created_at)} · {text(payment.status)}</span></div><b>{money(payment)}</b></div>) : <p className="portal-muted">No payments are recorded yet.</p>}</section>
        </div>
      </section>;
    })}

    <section className="request-panel" id="maintenance">
      {currentTenancies.length > 0 && <div className="portal-panel"><div className="portal-section-heading" style={{ marginTop: 0 }}><div><div className="section-eyebrow">SUPPORT</div><h2>Request maintenance</h2></div></div>
        <form className="portal-request-form" onSubmit={submitMaintenance}><label className="portal-field"><span>Active tenancy</span><select className="select" value={requestUnit} onChange={(event) => setRequestUnit(event.target.value)} required><option value="">Choose a unit</option>{currentTenancies.map(({ property, lease }) => <option key={`${property.property_id}:${lease.unit_id}`} value={`${property.property_id}:${lease.unit_id}`}>{property.property_name} · {text(property.units.find((item) => item.unit_id === lease.unit_id)?.unit_number ?? lease.unit_id, "Unit")}</option>)}</select></label><label className="portal-field portal-request-description"><span>What needs attention?</span><textarea className="textarea" rows={3} maxLength={5000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Describe the maintenance issue" required /></label><button className="button button-primary" disabled={requestBusy}>{requestBusy ? "Sending…" : "Send request"}</button></form></div>}
    </section>
    {notice && <p className={notice.startsWith("Your") ? "inline-success" : "inline-error"} role="status">{notice}</p>}

    <section className="portal-panel" id="booking-requests" style={{ marginTop: 20 }}><div className="portal-section-heading" style={{ marginTop: 0 }}><div><div className="section-eyebrow">YOUR REQUESTS</div><h2>Booking request status</h2></div></div>
      {bookingRequests.length ? <div className="portal-dashboard-list">{bookingRequests.map((request) => <div className="portal-dashboard-row" key={request.request_id}><span className={`status-badge status-${request.status}`}>{request.status}</span><div><strong>{request.property_label}</strong><span>{[request.city, request.country].filter(Boolean).join(", ")} · {new Date(request.created_at).toLocaleDateString()}</span>{request.decision_note && <small>{request.decision_note}</small>}{request.status === "accepted" && <small>The owner accepted your request. Contact them to complete the rental process; this request does not create a lease or take payment.</small>}</div>{request.status === "accepted" && request.owner_email && <a className="table-link" href={`mailto:${request.owner_email}?subject=${encodeURIComponent(`Booking request for ${request.property_label}`)}`}>Contact owner</a>}</div>)}</div> : <div className="portal-dashboard-empty">Your requests and owner responses will appear here.</div>}
    </section>

    <section id="available-listings" style={{ marginTop: 28 }}><div className="portal-section-heading" style={{ marginTop: 0 }}><div><div className="section-eyebrow">AVAILABLE NOW</div><h2>Properties from owners</h2><p className="portal-muted">Owners review booking requests. Their acceptance marks a property under offer; the tenancy agreement and payments happen separately.</p></div><button className="button button-secondary" onClick={() => void reload()}>Refresh listings</button></div>
      {marketplaceError && <div className="inline-error">{marketplaceError}</div>}
      {!marketplaceError && listings.length === 0 && <div className="portal-panel"><div className="portal-dashboard-empty">No approved available properties are listed right now.</div></div>}
      {!!listings.length && <div className="marketplace-grid">{listings.map((listing) => {
        const data = listing.data;
        const address = [data.street_address, data.unit_number].filter(Boolean).join(", ");
        const place = [data.city, data.state_or_county, data.country].filter(Boolean).join(", ");
        const sale = data.listing_type === "for_sale";
        const price = sale ? formatMoney(data.sale_price, String(data.price_currency ?? "USD")) : `${formatMoney(data.rent_amount, String(data.price_currency ?? "USD"))} / ${String(data.payment_frequency ?? "monthly")}`;
        const existingRequest = bookingRequests.find((request) => request.listing_id === listing.listing_id && request.status !== "declined");
        const ownerEmail = String(data.contact_email ?? "");
        const ownerPhone = `${String(data.phone_country_code ?? "")} ${String(data.contact_phone ?? "")}`.trim();
        return <article className="portal-panel marketplace-card" key={listing.listing_id}>
          <ListingPhoto listing={listing} />
          <div className="marketplace-copy"><h3>{address || "Property listing"}</h3><p>{place || "Location not provided"}</p>
            <div className="marketplace-price"><span>{String(data.listing_type ?? "for rent").replaceAll("_", " ")}</span><strong>{price}</strong></div>
            <p style={{ marginTop: 11 }}>{text(data.description, "No description supplied.")}</p>
            <p style={{ marginTop: 7 }}>{[data.bedrooms !== undefined && `${data.bedrooms} bed`, data.full_bathrooms !== undefined && `${data.full_bathrooms} bath`, data.property_size && `${data.property_size} ${data.property_size_unit === "square_feet" ? "sq ft" : "sq m"}`].filter(Boolean).join(" · ")}</p>
            <div className="marketplace-actions"><div className="marketplace-contact">{ownerEmail && <a className="table-link" href={`mailto:${ownerEmail}?subject=${encodeURIComponent(`Property enquiry: ${address}`)}`}>Email owner</a>}{ownerPhone && <a className="table-link" href={`tel:${ownerPhone.replaceAll(" ", "")}`}>Call owner</a>}</div>
              {sale ? <span className="booking-status-note">Contact the owner to enquire</span> : existingRequest ? <span className="booking-status-note">Request {existingRequest.status}</span> : <button className="button button-primary" onClick={() => { setBookingTarget(listing.listing_id); setNotice(""); }}>Request booking</button>}
            </div>
            {bookingTarget === listing.listing_id && <form className="marketplace-request-form" onSubmit={submitBooking}><strong>Send a booking request</strong><label className="portal-field"><span>Preferred move-in date (optional)</span><input className="input" type="date" value={desiredMoveIn} onChange={(event) => setDesiredMoveIn(event.target.value)} /></label><label className="portal-field"><span>Message for the owner</span><textarea className="textarea" maxLength={2000} value={bookingMessage} onChange={(event) => setBookingMessage(event.target.value)} placeholder="Introduce yourself and ask any questions" /></label><div className="marketplace-actions"><button type="button" className="button button-secondary" onClick={() => setBookingTarget("")}>Cancel</button><button className="button button-primary" disabled={bookingBusy}>{bookingBusy ? "Sending…" : "Send request"}</button></div></form>}
          </div>
        </article>;
      })}</div>}
    </section>
  </div>;
}
