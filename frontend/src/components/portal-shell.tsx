"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useSession } from "@/context/session";

export function PortalShell({ children }: { children: React.ReactNode }) {
  const { profile, signOut } = useSession(); const router = useRouter(); const pathname = usePathname();
  const owner = pathname.startsWith("/owner");
  const propertiesHref = owner ? "/owner/properties" : "/tenant/properties";
  const propertiesActive = pathname.startsWith(propertiesHref) || pathname.startsWith("/owner/listings") || pathname.startsWith("/owner/new");
  return <div className="portal-frame">
    <header className="portal-header"><Link className="brand-lockup portal-brand" href="/tenant"><span className="brand-mark">E</span><span>ENIGMA</span></Link>
      <nav className="portal-nav" aria-label="Customer portals"><Link className={!owner ? "portal-nav-active" : ""} href="/tenant">Tenant</Link><Link className={owner ? "portal-nav-active" : ""} href="/owner">Owner</Link></nav>
      <div className="portal-account"><span>{profile?.email}</span><button className="button button-secondary" onClick={() => { signOut(); router.replace("/login"); }}>Sign out</button></div>
    </header><main className="portal-main"><nav className="portal-page-nav" aria-label={`${owner ? "Owner" : "Tenant"} pages`}><Link className={!propertiesActive ? "portal-page-active" : ""} href={owner ? "/owner" : "/tenant"}>Dashboard</Link><Link className={propertiesActive ? "portal-page-active" : ""} href={propertiesHref}>{owner ? "Properties" : "My home & listings"}</Link></nav>{children}</main>
  </div>;
}
