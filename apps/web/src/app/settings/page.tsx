"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ErrorNote, Loading } from "@/components/States";
import { api, call, type Schemas } from "@/lib/client";
import { useResource } from "@/lib/useResource";

type Autonomy = Schemas["SettingsIn"]["autonomy"];

export default function SettingsPage() {
  const user = useResource(() => call(api.GET("/api/auth/me")), "me");
  const searches = useResource(() => call(api.GET("/api/searches")), "searches");
  const memory = useResource(() => call(api.GET("/api/memory")), "memory");
  const upwork = useResource(() => call(api.GET("/api/upwork/status")), "upwork");
  const [floor, setFloor] = useState(0);
  const [budget, setBudget] = useState(0);
  const [autonomy, setAutonomy] = useState<Autonomy>("manual");
  const [saved, setSaved] = useState(false);
  const [query, setQuery] = useState("");
  const [interval, setIntervalMinutes] = useState(60);
  const [problem, setProblem] = useState<string>();

  useEffect(() => {
    if (!user.data) return;
    setFloor(user.data.floor_usd);
    setBudget(user.data.weekly_connects_budget);
    setAutonomy(user.data.autonomy as Autonomy);
  }, [user.data]);

  if (user.loading) return <Loading label="Loading settings" />;
  if (user.error) return <ErrorNote message={user.error} />;

  async function save(event: FormEvent) {
    event.preventDefault();
    await call(
      api.PUT("/api/settings", {
        body: { floor_usd: floor, weekly_connects_budget: budget, autonomy },
      }),
    );
    setSaved(true);
  }

  async function addSearch(event: FormEvent) {
    event.preventDefault();
    setProblem(undefined);
    try {
      await call(
        api.POST("/api/searches", {
          body: { query: query || null, skills: [], interval_minutes: interval },
        }),
      );
      setQuery("");
      await searches.reload();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "The search could not be saved.");
    }
  }

  async function removeSearch(id: string) {
    await call(api.DELETE("/api/searches/{search_id}", { params: { path: { search_id: id } } }));
    await searches.reload();
  }

  async function connectUpwork() {
    setProblem(undefined);
    try {
      const { authorize_url } = await call(api.POST("/api/upwork/connect"));
      window.location.assign(authorize_url); // Upwork's own sign-in page
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "Upwork sign-in could not start.");
    }
  }

  async function disconnectUpwork() {
    await call(api.DELETE("/api/upwork/connect"));
    await upwork.reload();
  }

  async function forget(kind: "style" | "outcome", key: string) {
    if (kind === "style") {
      await call(api.DELETE("/api/memory/style/{key}", { params: { path: { key } } }));
    } else {
      await call(api.DELETE("/api/memory/outcome/{key}", { params: { path: { key } } }));
    }
    await memory.reload();
  }

  return (
    <>
      <div className="page-head">
        <h1>Settings</h1>
      </div>
      <section className="panel">
        <h2>Budgets and autonomy</h2>
        <form onSubmit={save}>
          <label>
            Lowest project price you accept (USD)
            <input type="number" min={0} value={floor} onChange={(e) => setFloor(+e.target.value)} />
          </label>
          <label>
            Weekly Connects budget
            <input
              type="number"
              min={0}
              value={budget}
              onChange={(e) => setBudget(+e.target.value)}
            />
          </label>
          <label>
            Autonomy
            <select value={autonomy} onChange={(e) => setAutonomy(e.target.value as Autonomy)}>
              <option value="manual">Show me every job</option>
              <option value="auto_skip_low_fit">Hide jobs GigBid recommends skipping</option>
            </select>
          </label>
          <p className="muted small">GigBid never sends anything without your approval.</p>
          <button className="primary">Save settings</button>
          {saved && <span role="status"> Saved.</span>}
        </form>
      </section>

      <section className="panel">
        <h2>Saved searches</h2>
        <ul className="memory-list">
          {searches.data?.map((search) => (
            <li key={search.id}>
              <span>
                {search.query || "All jobs"}{" "}
                <span className="muted small">every {search.interval_minutes} minutes</span>
              </span>
              <button className="ghost" onClick={() => removeSearch(search.id)}>
                Delete
              </button>
            </li>
          ))}
        </ul>
        <form onSubmit={addSearch} style={{ marginTop: 12 }}>
          <label>
            Search for
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label>
            Run every (minutes)
            <input
              type="number"
              min={1}
              value={interval}
              onChange={(e) => setIntervalMinutes(+e.target.value)}
            />
          </label>
          {problem && <ErrorNote message={problem} />}
          <button>Add search</button>
        </form>
      </section>

      <section className="panel">
        <h2>Connected accounts</h2>
        <p>
          {user.data?.marketplace === "gigsim" ? "GigSim simulator: connected" : user.data?.marketplace}
        </p>
        {!upwork.data?.configured ? (
          <p className="muted small">Upwork: sign-in is not configured on this server.</p>
        ) : upwork.data.connected ? (
          <p className="small">
            Upwork: connected. Every proposal needs your approval twice before it is sent.{" "}
            <button type="button" className="ghost" onClick={() => void disconnectUpwork()}>
              Disconnect
            </button>
          </p>
        ) : (
          <p className="small">
            Upwork: not connected.{" "}
            <button type="button" className="primary" onClick={() => void connectUpwork()}>
              Connect Upwork
            </button>
          </p>
        )}
      </section>

      <section className="panel">
        <h2>What GigBid has learned</h2>
        {memory.data?.style.length === 0 && memory.data.outcome.length === 0 && (
          <p className="muted">Nothing yet. Repeated edits and real outcomes will appear here.</p>
        )}
        <ul className="memory-list">
          {memory.data?.style.map((item) => (
            <li key={item.key}>
              <span>{item.text}</span>
              <button className="ghost" onClick={() => forget("style", item.key)}>
                Forget
              </button>
            </li>
          ))}
          {memory.data?.outcome.map((item) => (
            <li key={item.key}>
              <span>{item.text}</span>
              <button className="ghost" onClick={() => forget("outcome", item.key)}>
                Forget
              </button>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
