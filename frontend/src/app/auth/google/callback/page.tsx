"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/context/session";
import { apiRequest, defaultLanding } from "@/lib/api";

export default function GoogleCallbackPage() {
  const router = useRouter(); const { completeSignIn } = useSession(); const [message, setMessage] = useState("Completing secure sign-in…");
  useEffect(() => {
    let active = true;
    async function finish() {
      const code = new URLSearchParams(window.location.hash.slice(1)).get("code");
      if (!code) { router.replace("/login?auth_error=google_failed"); return; }
      window.history.replaceState({}, "", "/auth/google/callback");
      try {
        const result = await apiRequest<{ access_token: string }>("/auth/google/exchange", { method: "POST", body: JSON.stringify({ code }) });
        const profile = await completeSignIn(result.access_token);
        if (!active) return;
        router.replace(defaultLanding(profile));
      } catch { if (active) { setMessage("Google sign-in could not be completed. Please try again."); window.setTimeout(() => router.replace("/login?auth_error=google_failed"), 1800); } }
    }
    void finish(); return () => { active = false; };
  }, [completeSignIn, router]);
  return <main className="center-loading"><span className="spinner spinner-dark" />{message}</main>;
}
