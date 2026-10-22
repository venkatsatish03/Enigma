"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { useSession } from "@/context/session";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { ready, profile, propertyId } = useSession();

  useEffect(() => {
    if (!ready) return;
    if (!profile) router.replace("/login");
    else {
      const workspaceMemberships = profile.memberships.filter((membership) => membership.role !== "resident");
      if (!workspaceMemberships.length) router.replace("/tenant");
      else if (workspaceMemberships.length > 1 && !workspaceMemberships.some((membership) => membership.property_id === propertyId)) router.replace("/select-property");
    }
  }, [ready, profile, propertyId, router]);

  if (!ready || !profile || !propertyId) return <div className="center-loading"><span className="spinner spinner-dark" />Loading your workspace…</div>;
  return <AppShell>{children}</AppShell>;
}
