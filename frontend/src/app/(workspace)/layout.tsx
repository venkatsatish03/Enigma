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
    else if (profile.memberships.length > 1 && !propertyId) router.replace("/select-property");
    else if (!profile.memberships.length) router.replace("/select-property");
  }, [ready, profile, propertyId, router]);

  if (!ready || !profile || !propertyId) return <div className="center-loading"><span className="spinner spinner-dark" />Loading your workspace…</div>;
  return <AppShell>{children}</AppShell>;
}
