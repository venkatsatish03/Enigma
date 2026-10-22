"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useSession } from "@/context/session";

export function PortalShell({ children }: { children: React.ReactNode }) {
  const { profile, signOut } = useSession(); const router = useRouter(); const pathname = usePathname();
  const owner = pathname.startsWith("/owner");
  return <div className="portal-frame">
    <header className="portal-header"><Link className="brand-lockup portal-brand" href="/tenant"><span className="brand-mark">E</span><span>ENIGMA</span></Link>
      <nav className="portal-nav" aria-label="Customer portals"><Link className={!owner ? "portal-nav-active" : ""} href="/tenant">Tenant</Link><Link className={owner ? "portal-nav-active" : ""} href="/owner">Owner</Link></nav>
      <div className="portal-account"><span>{profile?.email}</span><button className="button button-secondary" onClick={() => { signOut(); router.replace("/login"); }}>Sign out</button></div>
    </header><main className="portal-main">{children}</main>
  </div>;
}
