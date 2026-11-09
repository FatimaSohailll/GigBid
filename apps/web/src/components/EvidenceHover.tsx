"use client";

import { useId, useState } from "react";
import type { Schemas } from "@/lib/client";

type Claim = Schemas["ProposalClaim"];
type Evidence = Schemas["EvidenceItem"];

/** A claim whose evidence appears on hover, on keyboard focus, or on tap. */
export function EvidenceHover({ claim, evidence }: { claim: Claim; evidence: Evidence[] }) {
  const [pinned, setPinned] = useState(false);
  const tooltip = useId();
  return (
    <span className="claim-wrap" data-open={pinned || undefined}>
      {/* A span, because browsers will not let a real button flow inside a sentence. */}
      <span
        role="button"
        tabIndex={0}
        className={`claim claim-${claim.support ?? "unknown"}`}
        aria-describedby={tooltip}
        aria-expanded={pinned}
        onClick={() => setPinned(!pinned)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setPinned(!pinned);
          }
        }}
      >
        {claim.text}
      </span>
      <span id={tooltip} role="tooltip" className="evidence-card">
        <strong>{(claim.support ?? "unchecked").replaceAll("_", " ")}</strong>
        {claim.explanation && <span className="muted">{claim.explanation}</span>}
        {evidence.map((item) => (
          <span key={item.evidence_id} className="evidence-item">
            <span className="evidence-id">
              {item.type.replaceAll("_", " ")} · {item.source}
            </span>
            {item.excerpt.slice(0, 260)}
            {item.excerpt.length > 260 && "…"}
          </span>
        ))}
        {evidence.length === 0 && <span className="muted">No evidence attached.</span>}
      </span>
    </span>
  );
}

/** Shows text with each verified claim marked up in place. */
export function ClaimedText({
  text,
  claims,
  evidenceFor,
}: {
  text: string;
  claims: Claim[];
  evidenceFor: (claim: Claim) => Evidence[];
}) {
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  const located = claims
    .map((claim) => ({ claim, at: text.indexOf(claim.text) }))
    .filter((item) => item.at >= 0)
    .sort((a, b) => a.at - b.at);
  for (const { claim, at } of located) {
    if (at < cursor) continue;
    parts.push(text.slice(cursor, at));
    parts.push(<EvidenceHover key={claim.claim_id} claim={claim} evidence={evidenceFor(claim)} />);
    cursor = at + claim.text.length;
  }
  parts.push(text.slice(cursor));
  return <p className="letter">{parts}</p>;
}
