"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/context/session";
import { ApiError, defaultLanding } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const { profile, signIn } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (profile) router.replace(defaultLanding(profile));
  }, [profile, router]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const authError = params.get("auth_error");
    if (!authError && params.get("password_reset") === "success") {
      setStatus("Your password was updated. Sign in with your new password.");
      window.history.replaceState({}, "", "/login");
      return;
    }
    if (!authError) return;
    const messages: Record<string, string> = {
      google_cancelled: "Google sign-in was cancelled.",
      google_state: "The Google sign-in session expired. Please try again.",
      google_verification: "Google could not verify this account. Use an active Google account and try again.",
      google_failed: "Google sign-in did not finish. Please try again.",
      google_not_configured: "Google sign-in is not configured yet. Ask an administrator to add the Google OAuth credentials.",
      account_disabled: "This ENIGMA account is disabled. Contact your administrator.",
    };
    setError(messages[authError] ?? "Google sign-in could not be completed.");
    window.history.replaceState({}, "", "/login");
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const nextProfile = await signIn(email.trim(), password);
      router.replace(defaultLanding(nextProfile));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not sign in. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-screen">
      <div className="auth-glow auth-glow-one" /><div className="auth-glow auth-glow-two" />
      <div className="auth-brand"><div className="brand-mark">E</div><span>ENIGMA</span></div>
      <section className="auth-card">
        <div className="eyebrow">PROPERTY OPERATIONS PLATFORM</div>
        <h1>Welcome back</h1>
        <p className="muted">Sign in to continue to your property workspace.</p>
        <form onSubmit={submit} className="auth-form">
          <label className="field-label" htmlFor="email">Email</label>
          <input id="email" className="input" type="email" autoComplete="username" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} required />
          <div className="field-row"><label className="field-label" htmlFor="password">Password</label><a className="field-hint auth-link" href="/forgot-password">Forgot password?</a></div>
          <div className="password-wrap"><input id="password" className="input" type={showPassword ? "text" : "password"} autoComplete="current-password" placeholder="Enter your password" value={password} onChange={(event) => setPassword(event.target.value)} required /><button className="password-toggle" type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? "Hide" : "Show"}</button></div>
          {error && <div className="inline-error" role="alert">{error}</div>}
          {status && <div className="inline-success" role="status">{status}</div>}
          <button className="button button-primary button-wide" type="submit" disabled={busy}>{busy ? <><span className="spinner" />Signing in…</> : <>Sign in <span aria-hidden="true">→</span></>}</button>
        </form>
        <div className="auth-divider"><span>OR</span></div>
        <a className="button button-google button-wide" href="/api/backend/api/v1/auth/google/start?intent=login"><span aria-hidden="true" className="google-mark">G</span>Continue with Google</a>
        <p className="auth-signup">New to ENIGMA? <a className="auth-link" href="/api/backend/api/v1/auth/google/start?intent=signup">Sign up with Google</a></p>
        <div className="auth-footer"><span className="secure-dot" />Your property data is protected by role-based access.</div>
      </section>
      <p className="auth-legal">ENIGMA · Secure property operations</p>
    </main>
  );
}
