"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { ErrorNote } from "@/components/States";
import { api, call } from "@/lib/client";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await call(api.POST("/api/auth/login", { body: { email, password } }));
      window.location.href = "/inbox";
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Sign-in failed.");
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <h1>Sign in to GigBid</h1>
      <p className="muted">Your copilot finds and drafts. You decide what gets sent.</p>
      <form onSubmit={submit}>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <ErrorNote message={error} />}
        <button className="primary" disabled={busy}>
          Sign in
        </button>
      </form>
      <p className="small">
        New here? <Link href="/onboarding">Create an account</Link>
      </p>
    </div>
  );
}
