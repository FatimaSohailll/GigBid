"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ErrorNote, FitMeter, Loading, RiskBadges } from "@/components/States";
import { ACTIVE_RUN_STATES, api, call, money, statusLabel } from "@/lib/client";
import { useResource } from "@/lib/useResource";

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading } = useResource(
    () => call(api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id } } })),
    id,
    4000,
  );
  if (loading) return <Loading label="Loading job" />;
  if (error || !data) return <ErrorNote message={error ?? "Job not found."} />;

  const { job, job_spec: spec, risk_report: risk, fit_report: fit, decision, price_quote: price } =
    data;
  const evidence = new Map(data.evidence?.map((item) => [item.evidence_id, item]));
  const running = job.run_status != null && ACTIVE_RUN_STATES.has(job.run_status);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{job.title}</h1>
          <p className="muted">
            {job.category.replaceAll("_", " ")} · {money(job.budget_min)} to {money(job.budget_max)}{" "}
            · {job.connects_cost} Connects
            {job.run_status && ` · ${statusLabel(job.run_status)}`}
          </p>
        </div>
        <div className="actions">
          {job.run_status === "awaiting_approval" && (
            <Link className="link-button" href={`/studio/${job.job_id}`}>
              Review proposal
            </Link>
          )}
          {job.thread_id && (
            <Link href={`/runs/${encodeURIComponent(job.thread_id)}`}>View run</Link>
          )}
        </div>
      </div>
      {running && <p role="status">Analysis in progress…</p>}

      <section className="panel">
        <h2>Bid or skip</h2>
        {decision ? (
          <>
            <p>
              <span className="action-pill">{decision.action}</span>
            </p>
            <ul>
              {decision.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </>
        ) : (
          <p className="muted">No decision yet.</p>
        )}
      </section>

      <div className="grid-2">
        <div>
          <section className="panel">
            <h2>Requirements and evidence</h2>
            <FitMeter value={fit?.score} />
            {fit?.matches?.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Requirement</th>
                      <th scope="col">Status</th>
                      <th scope="col">Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fit.matches.map((match) => (
                      <tr key={match.requirement}>
                        <td>{match.requirement}</td>
                        <td>{match.status}</td>
                        <td>
                          {match.evidence_ids?.length ? (
                            match.evidence_ids.map((item) => (
                              <div key={item} className="small">
                                <strong>{evidence.get(item)?.source ?? item}</strong>
                                <div className="muted">
                                  {evidence.get(item)?.excerpt.slice(0, 140)}
                                </div>
                              </div>
                            ))
                          ) : (
                            <span className="muted">{match.reason || "No evidence"}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">No requirement mapping yet.</p>
            )}
          </section>
          <section className="panel">
            <h2>Job post</h2>
            <p style={{ whiteSpace: "pre-wrap" }}>{data.description}</p>
          </section>
        </div>
        <div>
          <section className="panel">
            <h2>Risk</h2>
            <RiskBadges flags={risk?.flags ?? []} score={risk?.score} />
            {risk && <p>{risk.explanation}</p>}
            {risk?.injected_text?.map((text) => (
              <p key={text} className="small state-error">
                Ignored injected text: “{text}”
              </p>
            ))}
          </section>
          <section className="panel">
            <h2>Price</h2>
            <p>
              <strong>{money(price?.amount)}</strong>
            </p>
            <p className="small">{price?.rationale ?? "Not priced yet."}</p>
          </section>
          <section className="panel">
            <h2>Job specification</h2>
            {spec ? (
              <>
                <p className="small">Skills: {spec.required_skills?.join(", ") || "None listed"}</p>
                <p className="small">Deliverables: {spec.deliverables?.join("; ") || "None"}</p>
                <p className="small">Timeline: {spec.timeline_days ?? "Not stated"} days</p>
                <p className="small">Hidden needs: {spec.hidden_needs?.join("; ") || "None"}</p>
              </>
            ) : (
              <p className="muted">Not extracted yet.</p>
            )}
          </section>
        </div>
      </div>
    </>
  );
}
