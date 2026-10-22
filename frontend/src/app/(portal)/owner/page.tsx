"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiRequest, formatMoney } from "@/lib/api";
import { useSession } from "@/context/session";
import type { JsonRecord } from "@/types";

type Listing = { listing_id: string; status: string; data: JsonRecord; media: JsonRecord[]; updated_at: string };
export default function OwnerPortalPage() {
  const { refreshProfile } = useSession(); const [listings, setListings] = useState<Listing[]>([]); const [busy, setBusy] = useState(true); const [error, setError] = useState("");
  useEffect(() => { let active = true; apiRequest<Listing[]>("/portal/owner/listings").then((items) => { if (active) setListings(items); }).catch(() => { if (active) setError("Your owner listings could not be loaded."); }).finally(() => { if (active) setBusy(false); }); return () => { active = false; }; }, []);
  useEffect(() => { if (!busy && listings.length) void refreshProfile().catch(() => undefined); }, [busy, listings.length, refreshProfile]);
  return <div className="portal-content"><div className="portal-heading"><div><div className="eyebrow">YOUR PROPERTY</div><h1>Owner portal</h1><p>Create and manage your property listings.</p></div><Link className="button button-secondary" href="/tenant">Switch to tenant portal <span aria-hidden="true">→</span></Link></div>
    {busy && <div className="portal-loading"><span className="spinner spinner-dark" />Loading your properties…</div>}
    {error && <div className="inline-error">{error}</div>}
    {!busy && !error && listings.length === 0 && <section className="portal-panel portal-empty owner-empty"><div className="portal-icon">⌂</div><div className="eyebrow">FIRST-TIME OWNER SETUP</div><h2>Add your property details</h2><p>Tell us what you are listing, where it is, its features, media, and pricing. You can save a draft and return to it later.</p><Link className="button button-primary" href="/owner/new">Start owner setup <span aria-hidden="true">→</span></Link></section>}
    {!busy && !error && listings.length > 0 && <><div className="portal-section-heading"><div><div className="section-eyebrow">YOUR LISTINGS</div><h2>Properties and drafts</h2></div><Link className="button button-primary" href="/owner/new">Add a property <span aria-hidden="true">+</span></Link></div><div className="owner-listing-grid">{listings.map((listing) => <article className="portal-panel owner-listing-card" key={listing.listing_id}><div className="owner-listing-top"><span className={`status-badge status-${listing.status}`}>{listing.status.replaceAll("_", " ")}</span><span className="muted">Updated {new Date(listing.updated_at).toLocaleDateString()}</span></div><h3>{String(listing.data.street_address ?? "Property listing draft")}</h3><p>{[listing.data.city, listing.data.country].filter(Boolean).join(", ") || "Location details not added"}</p><div className="owner-listing-details"><span>{String(listing.data.listing_type ?? "for_rent").replaceAll("_", " ")}</span><strong>{listing.data.sale_price ? formatMoney(listing.data.sale_price, String(listing.data.price_currency ?? "USD")) : listing.data.rent_amount ? `${formatMoney(listing.data.rent_amount, String(listing.data.price_currency ?? "USD"))} / ${String(listing.data.payment_frequency ?? "month")}` : "Price not set"}</strong></div><div className="owner-listing-bottom"><span>{listing.media.length} media files</span>{listing.status === "draft" ? <Link className="table-link" href={`/owner/listings/${listing.listing_id}`}>Continue draft →</Link> : <span className="owner-review-note">Submitted for review</span>}</div></article>)}</div></>}
    <div className="owner-review-callout"><span className="portal-icon">✓</span><div><strong>Listing review</strong><p>Submitted listings are saved for review. The public marketplace and publication status are managed separately.</p></div></div>
  </div>;
}
