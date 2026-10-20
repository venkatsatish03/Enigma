"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/context/session";

export default function SelectPropertyPage() {
  const router = useRouter();
  const { ready, profile, propertyId, selectProperty, signOut } = useSession();

  useEffect(() => {
    if (!ready) return;
    if (!profile) router.replace("/login");
    else if (profile.memberships.length === 1) router.replace("/dashboard");
    else if (propertyId) router.replace("/dashboard");
  }, [ready, profile, propertyId, router]);

  if (!ready || !profile) return <div className="center-loading"><span className="spinner spinner-dark" />Loading your properties…</div>;
  return (
    <main className="property-screen">
      <header className="property-top"><div className="auth-brand"><div className="brand-mark">E</div><span>ENIGMA</span></div><span className="muted">Signed in as <strong>{profile.email}</strong></span></header>
      <section className="property-content">
        <div className="eyebrow">YOUR WORKSPACE</div><h1>Choose a property</h1><p className="muted">Select the property you want to work in. Your records stay scoped to this property.</p>
        {!profile.memberships.length ? <div className="empty-state large"><div className="empty-icon">⌂</div><h3>No property access yet</h3><p>Your account does not have an active property membership. Contact your administrator to get access.</p></div> :
          <div className="property-grid">{profile.memberships.map((membership) => <button key={membership.property_id} className="property-card" onClick={() => { selectProperty(membership.property_id); router.replace("/dashboard"); }}>
            <span className="property-symbol">⌂</span><span className="property-name">{membership.property_name || membership.property_id}</span><span className="property-role">{membership.role.replaceAll("_", " ")}</span><span className="property-permissions">{membership.permissions.length} permissions</span><span className="property-open">Open property <span>→</span></span>
          </button>)}</div>}
        <button className="text-button" onClick={() => { signOut(); router.replace("/login"); }}>Sign out</button>
      </section>
    </main>
  );
}
