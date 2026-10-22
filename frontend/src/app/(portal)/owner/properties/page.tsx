"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiRequest, formatMoney } from "@/lib/api";
import { useSession } from "@/context/session";
import type { JsonRecord } from "@/types";

type Listing = { listing_id: string; status: string; data: JsonRecord; media: JsonRecord[]; updated_at: string };

export default function OwnerPropertiesPage() {
  const { refreshProfile } = useSession();
  const [listings, setListings] = useState<Listing[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [statusError, setStatusError] = useState("");
  const [updatingId, setUpdatingId] = useState("");

  useEffect(() => {
    let active = true;
    apiRequest<Listing[]>("/portal/owner/listings")
      .then((items) => { if (active) setListings(items); })
      .catch(() => { if (active) setError("Your owner listings could not be loaded."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, []);
  useEffect(() => { if (!busy && listings.length) void refreshProfile().catch(() => undefined); }, [busy, listings.length, refreshProfile]);

  async function changeAvailability(listing: Listing, property_status: string) {
    setUpdatingId(listing.listing_id); setStatusError("");
    try {
      const updated = await apiRequest<Listing>(`/portal/owner/listings/${encodeURIComponent(listing.listing_id)}/availability`, { method: "PATCH", body: JSON.stringify({ property_status }) });
      setListings((current) => current.map((item) => item.listing_id === updated.listing_id ? updated : item));
    } catch (reason) { setStatusError(reason instanceof Error ? reason.message : "Property availability could not be updated."); }
    finally { setUpdatingId(""); }
  }

  return <div className="portal-content">
    <div className="portal-heading"><div><div className="eyebrow">YOUR PROPERTY</div><h1>Your properties</h1><p>Manage drafts, availability, and properties published for tenants.</p></div><Link className="button button-primary" href="/owner/new">Add a property <span aria-hidden="true">＋</span></Link></div>
    {busy && <div className="portal-loading"><span className="spinner spinner-dark" />Loading your properties…</div>}
    {error && <div className="inline-error">{error}</div>}
    {statusError && <div className="inline-error">{statusError}</div>}
    {!busy && !error && listings.length === 0 && <section className="portal-panel portal-empty owner-empty"><div className="portal-icon">⌂</div><div className="eyebrow">OWNER PROPERTIES</div><h2>Add your first property</h2><p>Save a draft while you gather details. When you publish a complete listing, it becomes approved and visible to tenants immediately.</p><Link className="button button-primary" href="/owner/new">Add a property <span aria-hidden="true">→</span></Link></section>}
    {!busy && !error && listings.length > 0 && <><div className="portal-section-heading"><div><div className="section-eyebrow">YOUR HOLDINGS</div><h2>Properties and drafts</h2></div></div><div className="owner-listing-grid">{listings.map((listing) => {
      const propertyStatus = String(listing.data.property_status ?? "available").replaceAll("_", " ");
      const stateLabel = listing.status === "approved" ? propertyStatus : listing.status.replaceAll("_", " ");
      const price = listing.data.sale_price ? formatMoney(listing.data.sale_price, String(listing.data.price_currency ?? "USD")) : listing.data.rent_amount ? `${formatMoney(listing.data.rent_amount, String(listing.data.price_currency ?? "USD"))} / ${String(listing.data.payment_frequency ?? "month")}` : "Price not set";
      return <article className="portal-panel owner-listing-card" key={listing.listing_id}>
        <div className="owner-listing-top"><span className={`status-badge status-${listing.status}`}>{stateLabel}</span><span className="muted">Updated {new Date(listing.updated_at).toLocaleDateString()}</span></div>
        <h3>{String(listing.data.street_address ?? "Property listing draft")}</h3><p>{[listing.data.city, listing.data.country].filter(Boolean).join(", ") || "Location details not added"}</p>
        <div className="owner-listing-details"><span>{String(listing.data.listing_type ?? "for_rent").replaceAll("_", " ")}</span><strong>{price}</strong></div>
        <div className="owner-listing-bottom"><span>{listing.media.length} media files</span>{listing.status === "draft" ? <Link className="table-link" href={`/owner/listings/${listing.listing_id}`}>Continue draft →</Link> : <label className="owner-availability"><span>Availability</span><select className="select" value={String(listing.data.property_status ?? "available")} disabled={updatingId === listing.listing_id} onChange={(event) => void changeAvailability(listing, event.target.value)}><option value="available">Available to tenants</option><option value="under_offer">Under offer</option><option value="occupied">Occupied</option><option value="pending">Pending</option><option value="let">Let</option><option value="sold">Sold</option></select></label>}</div>
      </article>;
    })}</div></>}
    <div className="owner-review-callout"><span className="portal-icon">✓</span><div><strong>Publishing</strong><p>Complete listings are approved when published. Available properties appear in tenant listings; properties under offer or otherwise unavailable are hidden from new booking requests.</p></div></div>
  </div>;
}
