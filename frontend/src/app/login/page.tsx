"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/context/session";
import { ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const { profile, propertyId, signIn } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (profile) router.replace(profile.memberships.length > 1 && !propertyId ? "/select-property" : "/dashboard");
  }, [profile, propertyId, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(email.trim(), password);
      const nextProfile = await import("@/lib/api").then((module) => module.getProfile());
      router.replace(nextProfile.memberships.length > 1 ? "/select-property" : "/dashboard");
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
          <label className="field-label" htmlFor="email">Work email</label>
          <input id="email" className="input" type="email" autoComplete="username" placeholder="you@company.com" value={email} onChange={(event) => setEmail(event.target.value)} required />
          <div className="field-row"><label className="field-label" htmlFor="password">Password</label><span className="field-hint">Use your ENIGMA account</span></div>
          <div className="password-wrap"><input id="password" className="input" type={showPassword ? "text" : "password"} autoComplete="current-password" placeholder="Enter your password" value={password} onChange={(event) => setPassword(event.target.value)} required /><button className="password-toggle" type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? "Hide" : "Show"}</button></div>
          {error && <div className="inline-error" role="alert">{error}</div>}
          <button className="button button-primary button-wide" type="submit" disabled={busy}>{busy ? <><span className="spinner" />Signing in…</> : <>Sign in <span aria-hidden="true">→</span></>}</button>
        </form>
        <div className="auth-footer"><span className="secure-dot" />Your property data is protected by role-based access.</div>
      </section>
      <p className="auth-legal">ENIGMA · Secure property operations</p>
    </main>
  );
}
