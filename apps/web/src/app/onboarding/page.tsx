"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { ErrorNote } from "@/components/States";
import { api, call, type Schemas } from "@/lib/client";

type Persona = Schemas["SignupIn"]["persona"];

export default function OnboardingPage() {
  const [step, setStep] = useState(1);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [persona, setPersona] = useState<Persona>("ai_ml_engineer");
  const [floor, setFloor] = useState(400);
  const [budget, setBudget] = useState(60);
  const [query, setQuery] = useState("");
  const [skills, setSkills] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");

  async function guarded(action: () => Promise<void>) {
    setBusy(true);
    setError(undefined);
    try {
      await action();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const createAccount = (event: FormEvent) => {
    event.preventDefault();
    void guarded(async () => {
      await call(api.POST("/api/auth/signup", { body: { email, password, persona } }));
      setStep(2);
    });
  };

  const savePreferences = (event: FormEvent) => {
    event.preventDefault();
    void guarded(async () => {
      await call(
        api.PUT("/api/settings", {
          body: { floor_usd: floor, weekly_connects_budget: budget, autonomy: "manual" },
        }),
      );
      await call(
        api.POST("/api/searches", {
          body: {
            query: query || null,
            skills: skills.split(",").map((s) => s.trim()).filter(Boolean),
            interval_minutes: 60,
          },
        }),
      );
      setStep(3);
    });
  };

  const finish = (event: FormEvent) => {
    event.preventDefault();
    void guarded(async () => {
      if (file) {
        const form = new FormData();
        form.append("file", file);
        form.append("title", title || file.name);
        const response = await fetch("/api/portfolio/uploads", { method: "POST", body: form });
        if (!response.ok) throw new Error("That file could not be uploaded.");
      }
      window.location.href = "/inbox";
    });
  };

  return (
    <div className="panel">
      <h1>Set up GigBid</h1>
      <p className="steps">Step {step} of 3</p>
      {error && <ErrorNote message={error} />}

      {step === 1 && (
        <form onSubmit={createAccount}>
          <label>
            Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label>
            Password (at least 10 characters)
            <input
              type="password"
              minLength={10}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <label>
            Profile
            <select value={persona} onChange={(e) => setPersona(e.target.value as Persona)}>
              <option value="ai_ml_engineer">AI/ML engineer</option>
              <option value="fullstack_developer">Full-stack developer</option>
              <option value="data_analyst">Data analyst</option>
              <option disabled>My own profile (needs live mode)</option>
            </select>
          </label>
          <button className="primary" disabled={busy}>
            Create account
          </button>
          <p className="small">
            Already registered? <Link href="/login">Sign in</Link>
          </p>
        </form>
      )}

      {step === 2 && (
        <form onSubmit={savePreferences}>
          <fieldset className="panel">
            <legend>Marketplace</legend>
            <label>
              <input type="radio" checked readOnly style={{ width: "auto" }} /> GigSim simulator
            </label>
            <label className="muted">
              <input type="radio" disabled style={{ width: "auto" }} /> Upwork (not available yet)
            </label>
          </fieldset>
          <label>
            Lowest project price you accept (USD)
            <input
              type="number"
              min={0}
              value={floor}
              onChange={(e) => setFloor(Number(e.target.value))}
            />
          </label>
          <label>
            Weekly Connects budget
            <input
              type="number"
              min={0}
              value={budget}
              onChange={(e) => setBudget(Number(e.target.value))}
            />
          </label>
          <label>
            Search for jobs about
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label>
            Required skills, separated by commas
            <input value={skills} onChange={(e) => setSkills(e.target.value)} />
          </label>
          <button className="primary" disabled={busy}>
            Save preferences
          </button>
        </form>
      )}

      {step === 3 && (
        <form onSubmit={finish}>
          <label>
            Add a portfolio file (PDF, Markdown or text, optional)
            <input
              type="file"
              accept=".pdf,.md,.txt"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <label>
            Title for this work
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <button className="primary" disabled={busy}>
            Go to my inbox
          </button>
        </form>
      )}
    </div>
  );
}
