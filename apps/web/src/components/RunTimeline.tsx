"use client";

import { useEffect, useState } from "react";
import { ACTIVE_RUN_STATES, api, call, statusLabel } from "@/lib/client";

interface TimelineEvent {
  id: string;
  type: string;
  node?: string;
  status?: string;
  server?: string;
  at?: string;
}

const NODE_NAMES: Record<string, string> = {
  scout: "Scout received the job",
  analyst: "Job Analyst read the post",
  risk_assessor: "Risk Assessor checked the client",
  fit_matcher: "Fit Matcher mapped the evidence",
  reconcile: "Requirements reconciled",
  decide: "Bid decision made",
  pricing: "Pricing Strategist set a price",
  writer: "Writer drafted the proposal",
  verify_claims: "Claims checked against evidence",
  critic: "Critic reviewed the draft",
  compliance: "Compliance Guard checked policy",
  prepare_approval: "Preview prepared for approval",
  submit: "Proposal submitted",
  learn: "Learning signals recorded",
  load: "Client messages read",
  classify: "Client intent classified",
  find_slots: "Interview slots found",
  draft: "Reply drafted",
  send: "Message sent",
};

function describe(event: TimelineEvent): string {
  if (event.type === "node") return NODE_NAMES[event.node ?? ""] ?? event.node ?? "Step finished";
  if (event.type === "run_start") return "Run started";
  if (event.type === "waiting_for_service") return "Waiting for the marketplace to return";
  return `Stopped: ${statusLabel(event.status ?? "finished")}`;
}

/** Live node-by-node progress. The server can replay, so events are de-duplicated by id. */
export function RunTimeline({ threadId }: { threadId: string }) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [live, setLive] = useState(false);

  useEffect(() => {
    let source: EventSource | undefined;
    const open = () => {
      if (source) return;
      source = new EventSource(`/api/runs/${encodeURIComponent(threadId)}/stream`);
      setLive(true);
      source.onmessage = (message) => {
        const event: TimelineEvent = { ...JSON.parse(message.data), id: message.lastEventId };
        setEvents((current) =>
          current.some((item) => item.id === event.id) ? current : [...current, event],
        );
      };
      // The server closes the stream after the newest stop. Without closing here too, the
      // browser would reconnect forever. The watcher below reopens it if the run resumes.
      source.onerror = () => {
        source?.close();
        source = undefined;
        setLive(false);
      };
    };
    open();
    // A thread can resume after a stop, for example after an approval.
    const watcher = setInterval(async () => {
      const run = await call(
        api.GET("/api/runs/{thread_id}", { params: { path: { thread_id: threadId } } }),
      ).catch(() => undefined);
      if (run && ACTIVE_RUN_STATES.has(run.status)) open();
    }, 2500);
    return () => {
      clearInterval(watcher);
      source?.close();
    };
  }, [threadId]);

  return (
    <div>
      <p className="muted small" role="status">
        {live ? "Live" : "Waiting for activity"}
      </p>
      {events.length === 0 ? (
        <p className="muted">No progress has been recorded yet.</p>
      ) : (
        <ol className="timeline">
          {events.map((event) => (
            <li key={event.id} className={`timeline-${event.type}`}>
              {describe(event)}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
