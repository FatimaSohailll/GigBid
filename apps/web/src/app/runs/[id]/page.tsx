"use client";

import { useParams } from "next/navigation";
import { RunTimeline } from "@/components/RunTimeline";
import { ErrorNote, Loading } from "@/components/States";
import { api, call, statusLabel } from "@/lib/client";
import { useResource } from "@/lib/useResource";

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const threadId = decodeURIComponent(id);
  const { data, error, loading } = useResource(
    () => call(api.GET("/api/runs/{thread_id}", { params: { path: { thread_id: threadId } } })),
    threadId,
    2500,
  );
  if (loading) return <Loading label="Loading run" />;
  if (error || !data) return <ErrorNote message={error ?? "Run not found."} />;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Agent run: {data.ref_id}</h1>
          <p className="muted">
            {statusLabel(data.status)} · {data.tokens.toLocaleString("en-US")} tokens
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
        <p className="small">
          Trace id: <code>{data.trace_id}</code>
        </p>
      </section>
    </>
  );
}
