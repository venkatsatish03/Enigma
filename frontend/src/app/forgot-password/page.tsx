"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { apiRequest, ApiError } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await apiRequest("/auth/password/forgot", { method: "POST", body: JSON.stringify({ email }) });
      setSent(true);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not request a password reset.");
    } finally { setBusy(false); }
  }

  return <main className="auth-screen"><div className="auth-brand"><div className="brand-mark">E</div><span>ENIGMA</span></div><section className="auth-card">
    <div className="eyebrow">ACCOUNT RECOVERY</div><h1>Reset your password</h1>
    <p className="muted">Enter your account email and we’ll send a one-time reset link if it matches an active account.</p>
    {sent ? <div className="inline-success">If an active account matches that email, password reset instructions will be sent.</div> : <form className="auth-form" onSubmit={submit}>
      <label className="field-label" htmlFor="email">Email</label><input id="email" className="input" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
      {error && <div className="inline-error" role="alert">{error}</div>}<button className="button button-primary button-wide" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
    </form>}
    <p className="auth-signup"><Link className="auth-link" href="/login">Back to sign in</Link></p>
  </section><p className="auth-legal">ENIGMA · Secure property operations</p></main>;
}
