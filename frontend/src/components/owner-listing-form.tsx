"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiRequest, ApiError } from "@/lib/api";
import type { JsonRecord } from "@/types";

type ListingRecord = { listing_id: string; status: string; data: JsonRecord; media: JsonRecord[] };
type FormValues = Record<string, string>;
const initial: FormValues = {
  listing_type: "for_rent", owner_full_name: "", authorized_representative: "", contact_email: "", phone_country_code: "+1", contact_phone: "",
  country: "", region: "", street_address: "", unit_number: "", city: "", state_or_county: "", postal_code: "", property_type: "", property_status: "available", property_size: "", property_size_unit: "square_meters",
  bedrooms: "", bedroom_configuration: "", full_bathrooms: "", partial_bathrooms: "", furnishing: "", parking: "", other_facilities: "",
  description: "", local_area: "", price_currency: "USD", rent_amount: "", sale_price: "", payment_frequency: "monthly", other_payment_frequency: "", deposit_amount: "", deposit_rules: "", tenancy_duration: "", available_from: "", minimum_tenancy_duration: "", utilities_included: "", utilities_paid_separately: "", pet_policy: "", smoking_policy: "", occupancy_restrictions: "", parking_charge: "", service_charge: "", other_recurring_costs: "",
};
const listingTypes = [["for_rent", "For rent"], ["for_sale", "For sale"], ["long_term_let", "Long-term let"], ["short_term_rental", "Short-term rental"], ["room_rental", "Room rental"], ["commercial_lease", "Commercial lease"]];
const propertyTypes = [["detached_house", "Detached house"], ["semi_detached_house", "Semi-detached house"], ["terraced_house", "Terraced house"], ["flat_apartment", "Flat / apartment"], ["condominium", "Condominium"], ["townhouse", "Townhouse"], ["studio", "Studio"], ["room", "Room"], ["other", "Other"]];
const isSaleType = (value: string) => value === "for_sale";
const lineList = (value: string) => value.split(",").map((part) => part.trim()).filter(Boolean);
function fromRecord(data: JsonRecord): FormValues {
  const next = { ...initial };
  for (const key of Object.keys(next)) {
    const value = data[key];
    if (Array.isArray(value)) next[key] = value.join(", ");
    else if (value !== null && value !== undefined) next[key] = String(value);
  }
  next.other_recurring_costs = Array.isArray(data.other_recurring_costs) ? data.other_recurring_costs.map((item) => `${String((item as JsonRecord).name ?? "Other cost")}: ${String((item as JsonRecord).amount ?? "")}`).join("\n") : "";
  return next;
}
function toPayload(values: FormValues) {
  const data: Record<string, unknown> = { ...values };
  const numeric = ["property_size", "bedrooms", "full_bathrooms", "partial_bathrooms", "rent_amount", "sale_price", "deposit_amount", "parking_charge", "service_charge"];
  for (const key of numeric) data[key] = values[key] ? Number(values[key]) : null;
  for (const key of ["phone_country_code", "owner_full_name", "authorized_representative", "contact_email", "contact_phone", "country", "region", "street_address", "unit_number", "city", "state_or_county", "postal_code", "property_type", "furnishing", "parking", "bedroom_configuration", "description", "local_area", "payment_frequency", "other_payment_frequency", "deposit_rules", "tenancy_duration", "available_from", "minimum_tenancy_duration", "pet_policy", "smoking_policy", "occupancy_restrictions"]) data[key] = values[key] || null;
  data.other_facilities = lineList(values.other_facilities);
  data.utilities_included = lineList(values.utilities_included);
  data.utilities_paid_separately = lineList(values.utilities_paid_separately);
  data.other_recurring_costs = values.other_recurring_costs.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const [name, ...rest] = line.split(":"); return { name: name.trim(), amount: rest.join(":").trim() || null };
  });
  return data;
}
function displayError(error: unknown) {
  if (error instanceof ApiError && error.detail && typeof error.detail === "object") {
    const detail = error.detail as JsonRecord;
    if (Array.isArray(detail.errors)) return detail.errors.join(" ");
    if (typeof detail.message === "string") return detail.message;
  }
  return error instanceof ApiError ? error.message : "Could not save this listing. Please try again.";
}

export function OwnerListingForm({ listingId }: { listingId?: string }) {
  const router = useRouter(); const [values, setValues] = useState<FormValues>(initial); const [listing, setListing] = useState<ListingRecord | null>(null);
  const [busy, setBusy] = useState(false); const [loading, setLoading] = useState(Boolean(listingId)); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [mediaKind, setMediaKind] = useState("front_exterior"); const [files, setFiles] = useState<FileList | null>(null);
  useEffect(() => {
    if (!listingId) return;
    let active = true;
    apiRequest<ListingRecord>(`/portal/owner/listings/${encodeURIComponent(listingId)}`).then((record) => { if (active) { setListing(record); setValues(fromRecord(record.data)); } }).catch(() => { if (active) setError("This listing could not be loaded. It may not belong to your account."); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [listingId]);
  function set(key: string, value: string) { setValues((current) => ({ ...current, [key]: value })); }
  const locked = listing?.status !== undefined && listing.status !== "draft";
  const existingId = listingId ?? listing?.listing_id;

  async function saveDraft() {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = existingId
        ? await apiRequest<ListingRecord>(`/portal/owner/listings/${encodeURIComponent(existingId)}`, { method: "PUT", body: JSON.stringify(toPayload(values)) })
        : await apiRequest<ListingRecord>("/portal/owner/listings", { method: "POST", body: JSON.stringify(toPayload(values)) });
      setListing(result);
      if (files?.length) {
        for (const file of Array.from(files)) { const form = new FormData(); form.append("file", file); await apiRequest(`/portal/owner/listings/${result.listing_id}/media?media_kind=${encodeURIComponent(mediaKind)}`, { method: "POST", body: form }); }
        setFiles(null);
      }
      const refreshed = await apiRequest<ListingRecord>(`/portal/owner/listings/${result.listing_id}`);
      setListing(refreshed);
      if (!listingId) router.replace(`/owner/listings/${result.listing_id}`);
      setNotice("Draft saved. Your details and uploaded media are stored securely.");
    } catch (reason) { setError(displayError(reason)); }
    finally { setBusy(false); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); await saveDraft();
    // For first save, the newly created id is placed in the route; the owner can submit from the saved draft.
  }
  async function publishListing() {
    setBusy(true); setError(""); setNotice("");
    try {
      let current = listing;
      if (!current) {
        current = await apiRequest<ListingRecord>("/portal/owner/listings", { method: "POST", body: JSON.stringify(toPayload(values)) });
        setListing(current);
        if (files?.length) for (const file of Array.from(files)) { const form = new FormData(); form.append("file", file); await apiRequest(`/portal/owner/listings/${current.listing_id}/media?media_kind=${encodeURIComponent(mediaKind)}`, { method: "POST", body: form }); }
      } else {
        current = await apiRequest<ListingRecord>(`/portal/owner/listings/${current.listing_id}`, { method: "PUT", body: JSON.stringify(toPayload(values)) });
        setListing(current);
        if (files?.length) for (const file of Array.from(files)) { const form = new FormData(); form.append("file", file); await apiRequest(`/portal/owner/listings/${current.listing_id}/media?media_kind=${encodeURIComponent(mediaKind)}`, { method: "POST", body: form }); }
      }
      const published = await apiRequest<ListingRecord>(`/portal/owner/listings/${current.listing_id}/submit`, { method: "POST" }); setListing(published); setFiles(null); setNotice("Your listing is approved and visible to tenants.");
      if (!listingId) router.replace(`/owner/listings/${current.listing_id}`);
    } catch (reason) { setError(displayError(reason)); }
    finally { setBusy(false); }
  }

  if (loading) return <div className="portal-loading"><span className="spinner spinner-dark" />Loading listing details…</div>;
  return <div className="portal-content"><div className="portal-heading"><div><div className="eyebrow">OWNER PORTAL · PROPERTY DETAILS</div><h1>{listingId ? "Edit property draft" : "Add a property"}</h1><p>Complete the details tenants need to decide whether to request a booking.</p></div><Link className="button button-secondary" href="/owner/properties">← Your properties</Link></div>
    {error && <div className="inline-error" role="alert">{error}</div>}{notice && <div className="inline-success" role="status">{notice}</div>}
    {locked && <div className="portal-review-note">This listing has been published. Its listing details are read-only.</div>}
    <form className="owner-form" onSubmit={submit}>
      <section className="portal-panel form-section"><div className="form-section-head"><span className="form-step">01</span><div><h2>Owner and listing information</h2><p>Tell us who is responsible for the listing and what is being offered.</p></div></div><div className="portal-form-grid">
        <label className="portal-field"><span>Listing type *</span><select className="select" value={values.listing_type} onChange={(event) => set("listing_type", event.target.value)} disabled={locked}>{listingTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label className="portal-field"><span>Owner or representative full name *</span><input className="input" value={values.owner_full_name} onChange={(event) => set("owner_full_name", event.target.value)} required disabled={locked} /></label>
        <label className="portal-field"><span>Authorized representative (if applicable)</span><input className="input" value={values.authorized_representative} onChange={(event) => set("authorized_representative", event.target.value)} disabled={locked} /></label>
        <label className="portal-field"><span>Contact email *</span><input className="input" type="email" value={values.contact_email} onChange={(event) => set("contact_email", event.target.value)} required disabled={locked} /></label>
        <label className="portal-field"><span>Country calling code *</span><input className="input" list="calling-codes" value={values.phone_country_code} onChange={(event) => set("phone_country_code", event.target.value)} required disabled={locked} /><datalist id="calling-codes">{["+1", "+44", "+91", "+61", "+33", "+49", "+81", "+86", "+971", "+27", "+234", "+55", "+34", "+39", "+65", "+64", "+52", "+7", "+82", "+46", "+31", "+41", "+353", "+351", "+20", "+254"].map((code) => <option key={code} value={code} />)}</datalist></label>
        <label className="portal-field"><span>Contact phone *</span><input className="input" type="tel" value={values.contact_phone} onChange={(event) => set("contact_phone", event.target.value)} required disabled={locked} placeholder="Phone number without country code" /></label>
      </div></section>

      <section className="portal-panel form-section"><div className="form-section-head"><span className="form-step">02</span><div><h2>Property identification and location</h2><p>Provide the address and the basic property facts.</p></div></div><div className="portal-form-grid">
        <label className="portal-field"><span>Country *</span><input className="input" list="countries" value={values.country} onChange={(event) => set("country", event.target.value)} required disabled={locked} /><datalist id="countries">{["United States", "United Kingdom", "India", "Canada", "Australia", "Ireland", "New Zealand", "Singapore", "United Arab Emirates", "Germany", "France", "Spain", "Italy", "Netherlands", "South Africa", "Nigeria", "Brazil", "Japan", "Mexico", "Kenya"].map((country) => <option key={country} value={country} />)}</datalist></label>
        <label className="portal-field"><span>Region / state</span><input className="input" value={values.region} onChange={(event) => set("region", event.target.value)} disabled={locked} /></label>
        <label className="portal-field portal-field-wide"><span>Street address *</span><input className="input" value={values.street_address} onChange={(event) => set("street_address", event.target.value)} required disabled={locked} /></label>
        <label className="portal-field"><span>Unit / apartment number</span><input className="input" value={values.unit_number} onChange={(event) => set("unit_number", event.target.value)} disabled={locked} /></label>
        <label className="portal-field"><span>City *</span><input className="input" value={values.city} onChange={(event) => set("city", event.target.value)} required disabled={locked} /></label>
        <label className="portal-field"><span>State or county</span><input className="input" value={values.state_or_county} onChange={(event) => set("state_or_county", event.target.value)} disabled={locked} /></label>
        <label className="portal-field"><span>Postcode / ZIP</span><input className="input" value={values.postal_code} onChange={(event) => set("postal_code", event.target.value)} disabled={locked} /></label>
        <label className="portal-field"><span>Property type *</span><select className="select" value={values.property_type} onChange={(event) => set("property_type", event.target.value)} required disabled={locked}><option value="">Choose property type</option>{propertyTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label className="portal-field"><span>Property status</span><select className="select" value={values.property_status} onChange={(event) => set("property_status", event.target.value)} disabled={locked}>{[["available", "Available"], ["occupied", "Occupied"], ["under_offer", "Under offer"], ["pending", "Pending"], ["sold", "Sold"], ["let", "Let"]].map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label className="portal-field"><span>Property size *</span><div className="inline-inputs"><input className="input" type="number" min="0.01" step="0.01" value={values.property_size} onChange={(event) => set("property_size", event.target.value)} required disabled={locked} /><select className="select" value={values.property_size_unit} onChange={(event) => set("property_size_unit", event.target.value)} disabled={locked}><option value="square_meters">m²</option><option value="square_feet">sq ft</option></select></div></label>
      </div></section>

      <section className="portal-panel form-section"><div className="form-section-head"><span className="form-step">03</span><div><h2>Physical features and facilities</h2><p>Add useful information about the rooms, finish, parking, and facilities.</p></div></div><div className="portal-form-grid">
        <label className="portal-field"><span>Bedrooms</span><input className="input" type="number" min="0" value={values.bedrooms} onChange={(event) => set("bedrooms", event.target.value)} disabled={locked} /></label>
        <label className="portal-field"><span>Bedroom configuration</span><input className="input" value={values.bedroom_configuration} onChange={(event) => set("bedroom_configuration", event.target.value)} placeholder="For example, 2 double and 1 single" disabled={locked} /></label>
        <label className="portal-field"><span>Full bathrooms</span><input className="input" type="number" min="0" value={values.full_bathrooms} onChange={(event) => set("full_bathrooms", event.target.value)} disabled={locked} /></label>
        <label className="portal-field"><span>Partial bathrooms</span><input className="input" type="number" min="0" value={values.partial_bathrooms} onChange={(event) => set("partial_bathrooms", event.target.value)} disabled={locked} /></label>
        <label className="portal-field"><span>Furnishing</span><select className="select" value={values.furnishing} onChange={(event) => set("furnishing", event.target.value)} disabled={locked}><option value="">Choose</option><option value="furnished">Furnished</option><option value="unfurnished">Unfurnished</option><option value="partly_furnished">Partly furnished</option></select></label>
        <label className="portal-field"><span>Parking</span><select className="select" value={values.parking} onChange={(event) => set("parking", event.target.value)} disabled={locked}><option value="">Choose</option><option value="garage">Garage</option><option value="driveway">Driveway</option><option value="allocated_space">Allocated space</option><option value="street_parking">Street parking</option><option value="none">No parking</option><option value="other">Other</option></select></label>
        <label className="portal-field portal-field-wide"><span>Other facilities (comma separated)</span><input className="input" value={values.other_facilities} onChange={(event) => set("other_facilities", event.target.value)} placeholder="Garden, balcony, lift, heating, air conditioning, laundry, storage, accessibility, security, internet" disabled={locked} /></label>
      </div></section>

      <section className="portal-panel form-section"><div className="form-section-head"><span className="form-step">04</span><div><h2>Photographs and other media</h2><p>Add exterior, entrance, rooms, amenities, a floor plan, or a video tour.</p></div></div>
        {listing?.media?.length ? <div className="media-chip-list">{listing.media.map((media) => <span className="media-chip" key={String(media.media_id)}>{String(media.media_kind).replaceAll("_", " ")} · {String(media.original_filename)}</span>)}</div> : <p className="portal-muted">Recommended: front exterior and entrance, living room, kitchen and bathrooms, each bedroom, outdoor areas and parking, plus a floor plan where available.</p>}
        {!locked && <div className="portal-form-grid upload-grid"><label className="portal-field"><span>Media category</span><select className="select" value={mediaKind} onChange={(event) => setMediaKind(event.target.value)}><option value="front_exterior">Front exterior</option><option value="entrance">Entrance</option><option value="living_room">Living room</option><option value="common_area">Common area</option><option value="kitchen">Kitchen</option><option value="bathroom">Bathroom</option><option value="bedroom">Bedroom</option><option value="garden">Garden</option><option value="balcony">Balcony</option><option value="parking">Parking</option><option value="shared_amenity">Shared amenity</option><option value="floor_plan">Floor plan</option><option value="video_walkthrough">Video walkthrough</option></select></label><label className="portal-field"><span>Choose file(s)</span><input className="input file-input" type="file" multiple accept={mediaKind === "video_walkthrough" ? "video/mp4,video/webm,video/quicktime" : mediaKind === "floor_plan" ? "image/jpeg,image/png,image/webp,application/pdf" : "image/jpeg,image/png,image/webp"} onChange={(event) => setFiles(event.target.files)} /></label></div>}
      </section>

      <section className="portal-panel form-section"><div className="form-section-head"><span className="form-step">05</span><div><h2>Description and local area</h2><p>Describe the property and useful nearby amenities.</p></div></div><div className="portal-form-grid">
        <label className="portal-field portal-field-wide"><span>Property description *</span><textarea className="textarea" rows={5} maxLength={10000} value={values.description} onChange={(event) => set("description", event.target.value)} required disabled={locked} placeholder="Describe the property, layout, and what makes it useful for a tenant or buyer." /></label>
        <label className="portal-field portal-field-wide"><span>Nearby area and amenities</span><textarea className="textarea" rows={4} maxLength={5000} value={values.local_area} onChange={(event) => set("local_area", event.target.value)} disabled={locked} placeholder="Nearby transport, shops, schools, parks, and other helpful amenities." /></label>
      </div></section>

      <section className="portal-panel form-section"><div className="form-section-head"><span className="form-step">06</span><div><h2>Pricing and commercial terms</h2><p>Pricing fields follow the selected rent or sale listing type.</p></div></div><div className="portal-form-grid">
        <label className="portal-field"><span>Currency code</span><input className="input" maxLength={3} value={values.price_currency} onChange={(event) => set("price_currency", event.target.value.toUpperCase())} disabled={locked} /></label>
        {isSaleType(values.listing_type) ? <label className="portal-field"><span>Sale price *</span><input className="input" type="number" min="0.01" step="0.01" value={values.sale_price} onChange={(event) => set("sale_price", event.target.value)} required disabled={locked} /></label> : <>
          <label className="portal-field"><span>Rent amount *</span><input className="input" type="number" min="0.01" step="0.01" value={values.rent_amount} onChange={(event) => set("rent_amount", event.target.value)} required disabled={locked} /></label>
          <label className="portal-field"><span>Payment frequency *</span><select className="select" value={values.payment_frequency} onChange={(event) => set("payment_frequency", event.target.value)} disabled={locked}><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="other">Other permitted arrangement</option></select></label>
          {values.payment_frequency === "other" && <label className="portal-field"><span>Describe payment frequency</span><input className="input" value={values.other_payment_frequency} onChange={(event) => set("other_payment_frequency", event.target.value)} disabled={locked} />
          </label>}
          <label className="portal-field"><span>Deposit amount</span><input className="input" type="number" min="0" step="0.01" value={values.deposit_amount} onChange={(event) => set("deposit_amount", event.target.value)} disabled={locked} /></label>
          <label className="portal-field"><span>Deposit rules and conditions</span><input className="input" value={values.deposit_rules} onChange={(event) => set("deposit_rules", event.target.value)} placeholder="Rules that apply in the property's location" disabled={locked} /></label>
          <label className="portal-field"><span>Tenancy duration</span><input className="input" value={values.tenancy_duration} onChange={(event) => set("tenancy_duration", event.target.value)} placeholder="For example, 12 months or periodic" disabled={locked} /></label>
          <label className="portal-field"><span>Available from</span><input className="input" type="date" value={values.available_from} onChange={(event) => set("available_from", event.target.value)} disabled={locked} /></label>
          <label className="portal-field"><span>Minimum tenancy duration</span><input className="input" value={values.minimum_tenancy_duration} onChange={(event) => set("minimum_tenancy_duration", event.target.value)} disabled={locked} /></label>
          <label className="portal-field"><span>Utilities included (comma separated)</span><input className="input" value={values.utilities_included} onChange={(event) => set("utilities_included", event.target.value)} placeholder="Water, internet" disabled={locked} /></label>
          <label className="portal-field"><span>Utilities paid separately (comma separated)</span><input className="input" value={values.utilities_paid_separately} onChange={(event) => set("utilities_paid_separately", event.target.value)} placeholder="Electricity, gas" disabled={locked} /></label>
          <label className="portal-field"><span>Pet policy</span><input className="input" value={values.pet_policy} onChange={(event) => set("pet_policy", event.target.value)} disabled={locked} /></label>
          <label className="portal-field"><span>Smoking policy</span><input className="input" value={values.smoking_policy} onChange={(event) => set("smoking_policy", event.target.value)} disabled={locked} /></label>
          <label className="portal-field"><span>Lawful occupancy restrictions</span><input className="input" value={values.occupancy_restrictions} onChange={(event) => set("occupancy_restrictions", event.target.value)} disabled={locked} /></label>
        </>}
        <label className="portal-field"><span>Parking charges</span><input className="input" type="number" min="0" step="0.01" value={values.parking_charge} onChange={(event) => set("parking_charge", event.target.value)} disabled={locked} /></label>
        <label className="portal-field"><span>Service charges</span><input className="input" type="number" min="0" step="0.01" value={values.service_charge} onChange={(event) => set("service_charge", event.target.value)} disabled={locked} /></label>
        <label className="portal-field portal-field-wide"><span>Other mandatory recurring costs (one per line: name: amount)</span><textarea className="textarea" rows={3} value={values.other_recurring_costs} onChange={(event) => set("other_recurring_costs", event.target.value)} disabled={locked} placeholder="Building fee: 25.00" /></label>
      </div><p className="portal-footnote">Deposit rules and occupancy terms must comply with the laws that apply in the property's location.</p></section>
      {!locked && <div className="owner-form-actions"><span>{listing ? `Draft · ${listing.media.length} uploaded file(s)` : "Your progress is saved as a private draft."}</span><div><Link className="button button-secondary" href="/owner/properties">Cancel</Link><button className="button button-secondary" type="button" disabled={busy} onClick={() => void saveDraft()}>{busy ? "Saving…" : "Save draft"}</button><button className="button button-primary" type="button" disabled={busy} onClick={() => void publishListing()}>{busy ? "Publishing…" : "Publish property"}</button></div></div>}
    </form>
  </div>;
}
