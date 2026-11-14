"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { ErrorNote } from "@/components/States";
import { api, call, type Schemas } from "@/lib/client";
import { useResource } from "@/lib/useResource";

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
  const [repository, setRepository] = useState("");
  // The server decides the marketplace; an own profile needs live Upwork mode.
  const status = useResource(() => call(api.GET("/api/status")), "status");
  const live = status.data?.marketplace_mode === "upwork_live";

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
      if (repository) {
        await call(api.POST("/api/portfolio/github", { body: { repository } }));
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
              <option value="own" disabled={!live}>
                {live ? "My own profile (starts empty)" : "My own profile (needs live Upwork mode)"}
              </option>
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
            <label className={live ? "muted" : undefined}>
              <input type="radio" checked={!live} readOnly disabled={live} style={{ width: "auto" }} />{" "}
              GigSim simulator
            </label>
            <label className={live ? undefined : "muted"}>
              <input type="radio" checked={live} readOnly disabled={!live} style={{ width: "auto" }} />{" "}
              Upwork, your own account
            </label>
            {live ? (
              <p className="small">
                Sign in on Upwork&apos;s own page. Every proposal then needs your approval twice
                before it is sent.{" "}
                <button
                  type="button"
                  onClick={() =>
                    void guarded(async () => {
                      const { authorize_url } = await call(api.POST("/api/upwork/connect"));
                      window.location.assign(authorize_url);
                    })
                  }
                >
                  Connect Upwork
                </button>{" "}
                You can also do this later in Settings.
              </p>
            ) : (
              <p className="muted small">This server runs the simulator. Upwork needs live mode.</p>
            )}
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
          <label>
            Import a GitHub repository (owner/name, optional)
            <input
              value={repository}
              onChange={(e) => setRepository(e.target.value)}
              pattern="[\w.-]+/[\w.-]+"
              placeholder="octocat/hello-world"
            />
          </label>
          {persona === "own" && (
            <p className="small">
              Your own profile starts empty: GigBid can only cite work you add here or in Settings.
            </p>
          )}
          <button className="primary" disabled={busy}>
            Go to my inbox
          </button>
        </form>
      )}
    </div>
  );
}
