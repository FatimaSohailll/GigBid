"use client";

import { useState } from "react";
import { api, call } from "@/lib/client";

export interface DryRunPreview {
  connects_cost: number;
  connects_balance: number;
  connects_balance_after: number;
  can_execute: boolean;
  blockers: string[];
  payload: Record<string, unknown>;
}

interface Props {
  threadId: string;
  preview: DryRunPreview;
  approveLabel: string;
  editedText: string | null;
  selectedSlot?: string | null;
  onDecided: () => void;
}

/** The only place a human can release a proposal or message. */
export function ApprovalPanel({
  threadId,
  preview,
  approveLabel,
  editedText,
  selectedSlot,
  onDecided,
}: Props) {
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function decide(body: {
    action: "approve" | "edit" | "reject";
    edited_text?: string;
    reason?: string;
    selected_slot?: string;
  }) {
    setBusy(true);
    setError(undefined);
    try {
      await call(
        api.POST("/api/approvals/{thread_id}", {
          params: { path: { thread_id: threadId } },
          body,
        }),
      );
      onDecided();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "The decision could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  const edited = editedText !== null;
  return (
    <section className="panel approval" aria-label="Approval">
      <h2>What will be sent</h2>
      <dl className="facts">
        <div>
          <dt>Connects cost</dt>
          <dd>{preview.connects_cost}</dd>
        </div>
        <div>
          <dt>Balance after</dt>
          <dd>{preview.connects_balance_after}</dd>
        </div>
      </dl>
      {preview.blockers.length > 0 && (
        <ul className="blockers" role="alert">
          {preview.blockers.map((blocker) => (
            <li key={blocker}>⚠ {blocker}</li>
          ))}
        </ul>
      )}
      <p className="muted small">Nothing is sent until you approve.</p>
      {error && (
        <p className="state-error" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        {edited ? (
          <button
            className="primary"
            disabled={busy}
            onClick={() => decide({ action: "edit", edited_text: editedText })}
          >
            Save edits and re-check
          </button>
        ) : (
          <button
            className="primary"
            disabled={busy || !preview.can_execute}
            onClick={() =>
              decide({ action: "approve", ...(selectedSlot ? { selected_slot: selectedSlot } : {}) })
            }
          >
            {approveLabel}
          </button>
        )}
        <button className="ghost" disabled={busy} onClick={() => setRejecting(!rejecting)}>
          Reject…
        </button>
      </div>
      {rejecting && (
        <form
          className="reject"
          onSubmit={(event) => {
            event.preventDefault();
            void decide({ action: "reject", reason });
          }}
        >
          <label>
            Reason for rejecting
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} required rows={2} />
          </label>
          <button className="danger" disabled={busy || !reason.trim()}>
            Confirm rejection
          </button>
        </form>
      )}
    </section>
  );
}
