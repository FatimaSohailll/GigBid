"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApprovalPanel, type DryRunPreview } from "@/components/ApprovalPanel";
import { ClaimedText } from "@/components/EvidenceHover";
import { ErrorNote, Loading } from "@/components/States";
import { ACTIVE_RUN_STATES, api, call, money, statusLabel, type Schemas } from "@/lib/client";
import { useResource } from "@/lib/useResource";

export default function StudioPage() {
  const { id } = useParams<{ id: string }>();
  const [polling, setPolling] = useState(false);
  const { data, error, loading, reload } = useResource(
    () => call(api.GET("/api/proposals/{job_id}", { params: { path: { job_id: id } } })),
    id,
    polling ? 1000 : undefined,
  );
  const [text, setText] = useState("");
  const [note, setNote] = useState<string>();
  const [regenerating, setRegenerating] = useState<number>();

  const original = data?.draft?.cover_letter ?? "";
  const version = data?.draft?.version;
  useEffect(() => setText(original), [original, version]);
  useEffect(() => {
    if (data) setPolling(ACTIVE_RUN_STATES.has(data.status));
  }, [data]);

  if (loading) return <Loading label="Loading proposal" />;
  if (error || !data) return <ErrorNote message={error ?? "Proposal not found."} />;

  const draft = data.draft;
  const pending = data.pending_approval as
    | { dry_run_preview: DryRunPreview; price_rationale: string; stage?: string }
    | null
    | undefined;
  // The second approval: Upwork has prepared the proposal and shows what sending will cost.
  const onPlatform = pending?.stage === "platform_confirmation";
  const evidence = new Map(data.evidence?.map((item) => [item.evidence_id, item]));
  const evidenceFor = (claim: Schemas["ProposalClaim"]) =>
    (claim.evidence_ids ?? []).flatMap((item) => evidence.get(item) ?? []);
  const paragraphs = text.split(/\n{2,}/).filter((part) => part.trim());
  const critique = data.critiques?.at(-1);
  const compliance = data.compliance?.at(-1);

  async function regenerate(index: number) {
    setRegenerating(index);
    setNote(undefined);
    try {
      const result = await call(
        api.POST("/api/proposals/{job_id}/regenerate", {
          params: { path: { job_id: id } },
          body: { paragraph: paragraphs[index] },
        }),
      );
      if (!result.text) {
        setNote("No supported version could be written, so the paragraph was left unchanged.");
        return;
      }
      setText(paragraphs.map((part, at) => (at === index ? result.text : part)).join("\n\n"));
      if (result.removed_claims.length) {
        setNote(`${result.removed_claims.length} unsupported claim(s) were removed.`);
      }
    } catch (problem) {
      setNote(problem instanceof Error ? problem.message : "Regeneration failed.");
    } finally {
      setRegenerating(undefined);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Proposal studio</h1>
          <p className="muted" role="status">
            {statusLabel(data.status)}
            {draft && ` · Draft ${draft.version} · Bid ${money(draft.bid_amount)}`}
          </p>
        </div>
      </div>
      {!draft && <p className="muted">No draft has been written for this job.</p>}
      {draft && (
        <div className="grid-2">
          <div>
            <section className="panel">
              <h2>Verified draft</h2>
              <p className="muted small">Hover over, focus or tap a claim to see its evidence.</p>
              <ClaimedText
                text={draft.cover_letter}
                claims={draft.claims ?? []}
                evidenceFor={evidenceFor}
              />
            </section>
            <section className="panel">
              <label>
                Proposal text
                <textarea
                  rows={10}
                  value={text}
                  disabled={!pending}
                  onChange={(event) => setText(event.target.value)}
                />
              </label>
              {pending && (
                <div className="paragraphs">
                  {paragraphs.map((_, index) => (
                    <button
                      key={index}
                      className="ghost"
                      disabled={regenerating !== undefined}
                      onClick={() => regenerate(index)}
                    >
                      Regenerate paragraph {index + 1}
                    </button>
                  ))}
                </div>
              )}
              {note && (
                <p className="small" role="status">
                  {note}
                </p>
              )}
            </section>
            {draft.screening_answers && draft.screening_answers.length > 0 && (
              <section className="panel">
                <h2>Screening answers</h2>
                {draft.screening_answers.map((item) => (
                  <p key={item.question}>
                    <strong>{item.question}</strong>
                    <br />
                    {item.answer}
                  </p>
                ))}
              </section>
            )}
          </div>
          <div>
            {pending ? (
              <ApprovalPanel
                threadId={data.thread_id}
                preview={pending.dry_run_preview}
                approveLabel={
                  onPlatform
                    ? "Send on Upwork"
                    : pending.dry_run_preview.requires_platform_confirmation
                      ? "Approve and prepare on Upwork"
                      : "Approve and submit"
                }
                editedText={!onPlatform && text !== original ? text : null}
                onDecided={() => {
                  setPolling(true);
                  void reload();
                }}
              />
            ) : (
              <section className="panel">
                <h2>{statusLabel(data.status)}</h2>
                <p className="muted">This proposal is not waiting for a decision.</p>
              </section>
            )}
            {pending && (
              <section className="panel">
                <h2>Price rationale</h2>
                <p className="small">{pending.price_rationale}</p>
              </section>
            )}
            <section className="panel">
              <h2>Critic</h2>
              {critique?.reasons?.length ? (
                <ul className="comments">
                  {critique.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">No comments.</p>
              )}
            </section>
            <section className="panel">
              <h2>Compliance</h2>
              {compliance?.reason && <p className="small">{compliance.reason}</p>}
              {compliance?.violations?.length ? (
                <ul className="comments">
                  {compliance.violations.map((item) => (
                    <li key={item.excerpt} className={compliance.action === "block" ? "block" : ""}>
                      {item.rule_id}: {item.explanation}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">No policy problems found.</p>
              )}
            </section>
          </div>
        </div>
      )}
    </>
  );
}
