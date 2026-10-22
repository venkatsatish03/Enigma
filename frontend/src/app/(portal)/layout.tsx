"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { PortalShell } from "@/components/portal-shell";
import { useSession } from "@/context/session";

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter(); const { ready, profile } = useSession();
  useEffect(() => { if (ready && !profile) router.replace("/login"); }, [ready, profile, router]);
  if (!ready || !profile) return <div className="center-loading"><span className="spinner spinner-dark" />Loading your portal…</div>;
  return <PortalShell>{children}</PortalShell>;
}
