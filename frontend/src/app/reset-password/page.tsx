"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiRequest, ApiError } from "@/lib/api";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const resetToken = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (!resetToken) return;
    setToken(resetToken);
    window.history.replaceState({}, "", "/reset-password");
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    if (!token) { setError("This reset link is missing its token. Request a new link."); return; }
    if (password !== confirm) { setError("The passwords do not match."); return; }
    setBusy(true);
    try { await apiRequest("/auth/password/reset", { method: "POST", body: JSON.stringify({ token, password }) }); router.replace("/login?password_reset=success"); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not reset the password. Request a new link."); }
    finally { setBusy(false); }
  }

  return <main className="auth-screen"><div className="auth-brand"><div className="brand-mark">E</div><span>ENIGMA</span></div><section className="auth-card">
    <div className="eyebrow">ACCOUNT RECOVERY</div><h1>Choose a new password</h1><p className="muted">Use at least 12 characters.</p>
    <form className="auth-form" onSubmit={submit}><label className="field-label" htmlFor="password">New password</label><input id="password" className="input" type="password" autoComplete="new-password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} required />
      <label className="field-label auth-label-spaced" htmlFor="confirm">Confirm password</label><input id="confirm" className="input" type="password" autoComplete="new-password" minLength={12} value={confirm} onChange={(event) => setConfirm(event.target.value)} required />
      {error && <div className="inline-error" role="alert">{error}</div>}<button className="button button-primary button-wide" disabled={busy}>{busy ? "Saving…" : "Update password"}</button>
    </form><p className="auth-signup"><Link className="auth-link" href="/login">Back to sign in</Link></p>
  </section><p className="auth-legal">ENIGMA · Secure property operations</p></main>;
}
