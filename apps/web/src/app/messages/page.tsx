"use client";

import { useEffect, useState } from "react";
import { ApprovalPanel, type DryRunPreview } from "@/components/ApprovalPanel";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { ACTIVE_RUN_STATES, api, call, statusLabel } from "@/lib/client";
import { useResource } from "@/lib/useResource";

function Conversation({ id }: { id: string }) {
  const { data, error, loading, reload } = useResource(
    () =>
      call(
        api.GET("/api/conversations/{conversation_id}", {
          params: { path: { conversation_id: id } },
        }),
      ),
    id,
    3000,
  );
  const [text, setText] = useState("");
  const [slot, setSlot] = useState<string | null>(null);
  const body = data?.reply?.body ?? "";
  useEffect(() => setText(body), [body]);

  if (loading) return <Loading label="Loading conversation" />;
  if (error || !data) return <ErrorNote message={error ?? "Conversation not found."} />;

  const pending = data.pending_approval as { dry_run_preview: DryRunPreview } | null | undefined;
  const messages = (data.messages ?? []) as { message_id: string; sender: string; body: string }[];
  const slots = (data.proposed_slots ?? []) as { start: string }[];

  return (
    <div>
      <section className="panel">
        <h2>Conversation</h2>
        <p className="muted small" role="status">
          {statusLabel(data.status)}
          {data.intent && ` · Intent: ${String(data.intent.intent).replaceAll("_", " ")}`}
        </p>
        {messages.map((message) => (
          <div key={message.message_id} className={`bubble bubble-${message.sender}`}>
            {message.body}
          </div>
        ))}
      </section>
      {ACTIVE_RUN_STATES.has(data.status) && <p role="status">Working on a reply…</p>}
      {pending && (
        <>
          <section className="panel">
            <label>
              Suggested reply
              <textarea rows={6} value={text} onChange={(event) => setText(event.target.value)} />
            </label>
            {slots.length > 0 && (
              <fieldset>
                <legend>Interview slot to hold</legend>
                {slots.map((item) => (
                  <label key={item.start}>
                    <input
                      type="radio"
                      name="slot"
                      style={{ width: "auto" }}
                      checked={slot === item.start}
                      onChange={() => setSlot(item.start)}
                    />{" "}
                    {new Date(item.start).toLocaleString()}
                  </label>
                ))}
              </fieldset>
            )}
          </section>
          <ApprovalPanel
            threadId={data.thread_id}
            preview={pending.dry_run_preview}
            approveLabel="Approve and send"
            editedText={text !== body ? text : null}
            selectedSlot={slot}
            onDecided={() => void reload()}
          />
        </>
      )}
    </div>
  );
}

export default function MessagesPage() {
  const { data, error, loading } = useResource(
    () => call(api.GET("/api/conversations")),
    "conversations",
    5000,
  );
  const [selected, setSelected] = useState<string>();
  const current = selected ?? data?.[0]?.conversation_id;

  return (
    <>
      <div className="page-head">
        <h1>Messages</h1>
      </div>
      {loading && <Loading label="Loading conversations" />}
      {error && <ErrorNote message={error} />}
      {data?.length === 0 && (
        <Empty title="No client replies yet">
          <p className="muted">Replies appear here once a client responds to a proposal.</p>
        </Empty>
      )}
      {data && data.length > 0 && (
        <div className="thread-layout">
          <ul className="thread-list">
            {data.map((item) => (
              <li key={item.conversation_id}>
                <button
                  aria-current={item.conversation_id === current}
                  onClick={() => setSelected(item.conversation_id)}
                >
                  {item.conversation_id}
                  <br />
                  <span className="muted small">{statusLabel(item.status)}</span>
                </button>
              </li>
            ))}
          </ul>
          {current && <Conversation key={current} id={current} />}
        </div>
      )}
    </>
  );
}
