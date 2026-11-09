"use client";

import Link from "next/link";
import { useState } from "react";
import { Empty, ErrorNote, FitMeter, Loading, RiskBadges } from "@/components/States";
import { ACTIVE_RUN_STATES, api, call, money, statusLabel } from "@/lib/client";
import { useResource } from "@/lib/useResource";

const FILTERS = [
  ["all", "All"],
  ["bid", "Bid"],
  ["watch", "Watch"],
  ["skip", "Skip"],
] as const;
type Filter = (typeof FILTERS)[number][0];

export default function InboxPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const [checking, setChecking] = useState(false);
  const { data, error, loading, reload } = useResource(
    () =>
      call(
        api.GET("/api/jobs", {
          params: { query: filter === "all" ? {} : { action: filter } },
        }),
      ),
    filter,
    3000,
  );

  async function checkForJobs() {
    setChecking(true);
    await call(api.POST("/api/jobs/refresh")).catch(() => undefined);
    setChecking(false);
  }

  async function act(path: "/api/jobs/{job_id}/analyze" | "/api/jobs/{job_id}/skip", job: string) {
    await call(api.POST(path, { params: { path: { job_id: job } } }));
    await reload();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Job inbox</h1>
          <p className="muted">Ranked by how well each job fits your portfolio.</p>
        </div>
        <button className="primary" onClick={checkForJobs} disabled={checking}>
          Check for new jobs
        </button>
      </div>
      <div className="filters" role="group" aria-label="Filter by recommendation">
        {FILTERS.map(([value, label]) => (
          <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>
            {label}
          </button>
        ))}
      </div>
      {loading && <Loading label="Loading jobs" />}
      {error && <ErrorNote message={error} />}
      {data?.length === 0 && (
        <Empty title="No jobs yet">
          <p className="muted">
            Add a saved search in <Link href="/settings">Settings</Link>, then check for new jobs.
          </p>
        </Empty>
      )}
      <ul className="job-list">
        {data?.map((job) => {
          const active = job.run_status != null && ACTIVE_RUN_STATES.has(job.run_status);
          return (
            <li key={job.job_id} className="job-row">
              <div>
                <Link className="job-title" href={`/jobs/${job.job_id}`}>
                  {job.title}
                </Link>
                <div className="job-meta">
                  <FitMeter value={job.fit} />
                  <RiskBadges flags={job.risk_flags} score={job.risk} />
                  {job.action && <span className="action-pill">{job.action}</span>}
                  <span>{money(job.price)}</span>
                  <span>{job.connects_cost} Connects</span>
                  {job.run_status && <span>{statusLabel(job.run_status)}</span>}
                </div>
              </div>
              <div className="actions">
                {job.run_status === "awaiting_approval" && (
                  <Link className="link-button" href={`/studio/${job.job_id}`}>
                    Review proposal
                  </Link>
                )}
                {job.run_status == null && (
                  <button onClick={() => act("/api/jobs/{job_id}/analyze", job.job_id)}>
                    Analyse
                  </button>
                )}
                {active && job.thread_id && (
                  <Link href={`/runs/${encodeURIComponent(job.thread_id)}`}>Watch progress</Link>
                )}
                <button
                  className="ghost"
                  aria-label={`Skip ${job.title}`}
                  onClick={() => act("/api/jobs/{job_id}/skip", job.job_id)}
                >
                  Skip
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
