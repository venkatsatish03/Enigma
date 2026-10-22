"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/context/session";
import { apiRequest, ApiError, defaultLanding } from "@/lib/api";

export default function SetPasswordPage() {
  const router = useRouter(); const { completeSignIn } = useSession();
  const [token, setToken] = useState(""); const [password, setPassword] = useState(""); const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useEffect(() => {
    const setupToken = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (!setupToken) return;
    setToken(setupToken);
    window.history.replaceState({}, "", "/set-password");
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    if (!token) { setError("The setup link is missing or expired. Start again with Google sign-up."); return; }
    if (password !== confirm) { setError("The passwords do not match."); return; }
    setBusy(true);
    try {
      const result = await apiRequest<{ access_token: string }>("/auth/password/setup", { method: "POST", body: JSON.stringify({ token, password }) });
      const profile = await completeSignIn(result.access_token);
      router.replace(defaultLanding(profile));
    } catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not create the password. Try the Google sign-up again."); }
    finally { setBusy(false); }
  }
  return <main className="auth-screen"><div className="auth-brand"><div className="brand-mark">E</div><span>ENIGMA</span></div><section className="auth-card">
    <div className="eyebrow">GOOGLE ACCOUNT VERIFIED</div><h1>Create your ENIGMA password</h1><p className="muted">Your Google account is verified. Set a password to sign in to ENIGMA with your email and password.</p>
    <form className="auth-form" onSubmit={submit}><label className="field-label" htmlFor="password">Password</label><input id="password" className="input" type="password" autoComplete="new-password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} required />
      <label className="field-label auth-label-spaced" htmlFor="confirm">Confirm password</label><input id="confirm" className="input" type="password" autoComplete="new-password" minLength={12} value={confirm} onChange={(event) => setConfirm(event.target.value)} required />
      {error && <div className="inline-error" role="alert">{error}</div>}<button className="button button-primary button-wide" disabled={busy}>{busy ? "Creating account…" : "Create password and continue"}</button>
    </form>
  </section><p className="auth-legal">ENIGMA · Secure property operations</p></main>;
}
