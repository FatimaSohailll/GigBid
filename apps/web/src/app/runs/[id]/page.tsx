"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { RunTimeline } from "@/components/RunTimeline";
import { ErrorNote, Loading } from "@/components/States";
import { api, call, statusLabel, usd } from "@/lib/client";
import { useResource } from "@/lib/useResource";

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const threadId = decodeURIComponent(id);
  const path = { params: { path: { thread_id: threadId } } };
  const { data, error, loading } = useResource(
    () => call(api.GET("/api/runs/{thread_id}", path)),
    threadId,
    2500,
  );
  const usage = useResource(
    () => call(api.GET("/api/runs/{thread_id}/usage", path)),
    `usage:${threadId}`,
    5000,
  );
  const trace = useResource(
    () => call(api.GET("/api/runs/{thread_id}/trace", path)),
    `trace:${threadId}`,
  );
  const history = useResource(
    () => call(api.GET("/api/runs/{thread_id}/checkpoints", path)),
    `checkpoints:${threadId}`,
  );
  const [replayNote, setReplayNote] = useState<string>();

  if (loading) return <Loading label="Loading run" />;
  if (error || !data) return <ErrorNote message={error ?? "Run not found."} />;

  async function replay(checkpointId: string, node: string) {
    setReplayNote(undefined);
    try {
      await call(
        api.POST("/api/runs/{thread_id}/replay", {
          ...path,
          body: { checkpoint_id: checkpointId },
        }),
      );
      setReplayNote(`Replaying from ${node}. Progress appears above.`);
    } catch (problem) {
      setReplayNote(problem instanceof Error ? problem.message : "The replay could not start.");
    }
  }

  const rows = usage.data ?? [];
  const totalCost = rows.some((row) => row.cost_usd == null)
    ? null
    : rows.reduce((sum, row) => sum + (row.cost_usd ?? 0), 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Agent run: {data.ref_id}</h1>
          <p className="muted">
            {statusLabel(data.status)} · {data.tokens.toLocaleString("en-US")} tokens ·{" "}
            {usd(data.cost_usd ?? totalCost)}
          </p>
        </div>
      </div>
      {data.attention_reason && <ErrorNote message={data.attention_reason} />}
      <section className="panel">
        <h2>Progress</h2>
        <RunTimeline threadId={threadId} />
      </section>

      <section className="panel">
        <h2>Full trace</h2>
        {trace.data?.url ? (
          <p>
            <a href={trace.data.url} target="_blank" rel="noreferrer">
              Open the full trace in LangSmith
            </a>
          </p>
        ) : (
          <p className="muted small">{trace.data?.reason ?? "Looking for the trace…"}</p>
        )}
        <p className="small">
          Trace id: <code>{data.trace_id}</code>
        </p>
      </section>

      <section className="panel table-scroll">
        <h2>Cost by step</h2>
        {rows.length === 0 ? (
          <p className="muted small">No model calls recorded yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">Step</th>
                <th scope="col">Model</th>
                <th scope="col">Calls</th>
                <th scope="col">Tokens in / out</th>
                <th scope="col">Time</th>
                <th scope="col">Cost</th>
              </tr>
            </thead>
            <tbody>
              {rows
                .filter((row) => row.calls > 0)
                .map((row) => (
                  <tr key={row.node}>
                    <td>{row.node.replaceAll("_", " ")}</td>
                    <td className="small">{row.models.join(", ") || "None"}</td>
                    <td>{row.calls}</td>
                    <td>
                      {row.input_tokens.toLocaleString("en-US")} /{" "}
                      {row.output_tokens.toLocaleString("en-US")}
                    </td>
                    <td>{row.latency_seconds.toFixed(1)}s</td>
                    <td>{usd(row.cost_usd)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2>Replay</h2>
        <p className="muted small">
          Run this job again from an earlier step. Steps that send are not offered, and a replay
          that reaches an approval waits for you, as the first run did.
        </p>
        {replayNote && <p className="small" role="status">{replayNote}</p>}
        {(history.data ?? []).length === 0 ? (
          <p className="muted small">No saved steps yet.</p>
        ) : (
          <ul className="comments">
            {(history.data ?? []).map((point) => {
              const node = point.next_nodes.join(", ").replaceAll("_", " ");
              return (
                <li key={point.checkpoint_id}>
                  Step {point.step}: before <strong>{node}</strong>{" "}
                  {point.replayable ? (
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => void replay(point.checkpoint_id, node)}
                    >
                      Replay from here
                    </button>
                  ) : (
                    <span className="muted small">(sends; not replayable)</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
