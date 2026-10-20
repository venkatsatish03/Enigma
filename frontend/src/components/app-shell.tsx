"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useSession } from "@/context/session";

type NavItem = { title: string; href: string; icon: string; permission?: string; roles?: string[] };
const navGroups: { label: string; items: NavItem[] }[] = [
  { label: "WORKSPACE", items: [
    { title: "Overview", href: "/dashboard", icon: "◫" },
    { title: "Work queue", href: "/work-queue", icon: "☷" },
  ] },
  { label: "OPERATIONS", items: [
    { title: "Documents", href: "/documents", icon: "▤", permission: "documents:analyze" },
    { title: "Finance", href: "/finance", icon: "$", permission: "financial:operations" },
    { title: "Maintenance", href: "/maintenance", icon: "⌁", roles: ["admin", "property_manager"] },
    { title: "Residents & leases", href: "/residents-leases", icon: "♧", permission: "resident:read" },
    { title: "Compliance", href: "/compliance", icon: "◈", roles: ["admin", "property_manager"] },
  ] },
  { label: "REVIEW & INSIGHT", items: [
    { title: "Approvals", href: "/approvals", icon: "✓", permission: "approvals:read" },
    { title: "Workflows", href: "/workflows", icon: "⟳", roles: ["admin", "property_manager", "accountant"] },
    { title: "Reports", href: "/reports", icon: "▥", permission: "financial:operations" },
    { title: "Notifications", href: "/notifications", icon: "♧", permission: "financial:operations" },
    { title: "Audit", href: "/audit", icon: "⌕", permission: "audit:read" },
  ] },
  { label: "ADMINISTRATION", items: [
    { title: "Settings", href: "/settings", icon: "⚙", permission: "property:manage" },
  ] },
];

const titleMap: Record<string, string> = {
  dashboard: "Overview", "work-queue": "Work queue", documents: "Documents", finance: "Finance", maintenance: "Maintenance", "residents-leases": "Residents & leases", compliance: "Compliance", approvals: "Approval center", workflows: "Workflow monitor", reports: "Reports", notifications: "Notifications", audit: "Audit trail", settings: "Settings",
};

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { profile, propertyId, activeMembership, signOut } = useSession();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [search, setSearch] = useState("");
  const section = pathname.split("/")[1] || "dashboard";
  const pageTitle = titleMap[section] ?? "Overview";
  const visibleGroups = useMemo(() => navGroups.map((group) => ({ ...group, items: group.items.filter((item) => {
    const permissions = activeMembership?.permissions ?? [];
    const role = activeMembership?.role ?? "";
    return (!item.permission || permissions.includes(item.permission)) && (!item.roles || item.roles.includes(role) || profile?.is_superuser);
  }) })).filter((group) => group.items.length), [activeMembership, profile?.is_superuser]);

  function handleSearch(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const q = search.trim();
    if (q) router.push(`/work-queue?q=${encodeURIComponent(q)}`);
  }

  return <div className="app-frame">
    <aside className={`sidebar ${mobileNavOpen ? "sidebar-open" : ""}`}>
      <div className="sidebar-brand"><Link className="brand-lockup" href="/dashboard"><span className="brand-mark">E</span><span>ENIGMA</span></Link><button className="mobile-close" onClick={() => setMobileNavOpen(false)} aria-label="Close navigation">×</button></div>
      <div className="property-chip"><span className="property-chip-icon">⌂</span><div><span className="property-chip-label">ACTIVE PROPERTY</span><strong>{activeMembership?.property_name || propertyId}</strong><small>{activeMembership?.role.replaceAll("_", " ")}</small></div><span className="property-chip-caret">⌄</span></div>
      <nav className="side-nav" aria-label="Main navigation">{visibleGroups.map((group) => <div className="nav-group" key={group.label}><div className="nav-group-label">{group.label}</div>{group.items.map((item) => <Link key={item.href} href={item.href} onClick={() => setMobileNavOpen(false)} className={`nav-item ${pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(`${item.href}/`)) ? "nav-item-active" : ""}`}><span className="nav-icon">{item.icon}</span><span>{item.title}</span>{item.href === "/work-queue" && <span className="nav-badge">!</span>}</Link>)}</div>)}</nav>
      <div className="sidebar-bottom"><div className="help-card"><div className="help-card-icon">?</div><div><strong>Need a hand?</strong><span>Visit the help center</span></div><span className="help-arrow">↗</span></div><div className="sidebar-version">ENIGMA OPERATIONS <span>v1.0</span></div></div>
    </aside>
    {mobileNavOpen && <button className="mobile-scrim" onClick={() => setMobileNavOpen(false)} aria-label="Close navigation" />}
    <div className="main-column">
      <header className="topbar"><div className="topbar-left"><button className="mobile-menu" aria-label="Open navigation" onClick={() => setMobileNavOpen(true)}>☰</button><div className="breadcrumbs"><span>Workspace</span><span className="crumb-slash">/</span><strong>{pageTitle}</strong></div></div>
        <form className="top-search" onSubmit={handleSearch}><span>⌕</span><input aria-label="Search records" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search anything…" /><kbd>⌘ K</kbd></form>
        <div className="topbar-actions"><Link href="/notifications" className="icon-button alert-button" aria-label="Alerts"><span>♧</span><i /></Link><div className="user-menu-wrap"><button className="user-trigger" onClick={() => setMenuOpen((open) => !open)}><span className="avatar">{profile?.email.slice(0, 1).toUpperCase()}</span><span className="user-trigger-name">{profile?.email.split("@")[0]}</span><span className="caret">⌄</span></button>{menuOpen && <div className="user-menu"><div className="user-menu-email">{profile?.email}</div><button onClick={() => { setMenuOpen(false); router.push("/settings"); }}>Profile & settings</button><button onClick={() => { setMenuOpen(false); router.push("/select-property"); }}>Switch property</button><button className="danger-menu-item" onClick={() => { signOut(); router.replace("/login"); }}>Sign out</button></div>}</div></div>
      </header>
      <div className="property-context-bar"><span className="context-dot" />Working in <strong>{activeMembership?.property_name || propertyId}</strong><span className="context-divider">·</span><span>{activeMembership?.role.replaceAll("_", " ")}</span><button onClick={() => router.push("/select-property")}>Switch property</button></div>
      <main className="page-content">{children}</main>
    </div>
  </div>;
}
