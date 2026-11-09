import type { ReactNode } from "react";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <p className="state" role="status">
      {label}…
    </p>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <p className="state state-error" role="alert">
      {message}
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state-empty">
      <p className="state-title">{title}</p>
      {children}
    </div>
  );
}

export function FitMeter({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="muted">Not scored</span>;
  const percent = Math.round(value * 100);
  return (
    <span className="meter" role="img" aria-label={`Fit ${percent} percent`}>
      <span className="meter-track">
        <span className="meter-fill" style={{ width: `${percent}%` }} />
      </span>
      <span className="meter-value">{percent}%</span>
    </span>
  );
}

const SEVERE_FLAGS = new Set([
  "scam",
  "off_platform",
  "free_work",
  "upfront_payment",
  "credential_request",
  "injection",
]);

export function RiskBadges({ flags, score }: { flags: string[]; score?: number | null }) {
  if (score == null && flags.length === 0) return <span className="muted">Not assessed</span>;
  if (flags.length === 0) return <span className="badge badge-good">✓ No flags</span>;
  return (
    <span className="badges">
      {flags.map((flag) => (
        <span key={flag} className={`badge ${SEVERE_FLAGS.has(flag) ? "badge-bad" : "badge-warn"}`}>
          {SEVERE_FLAGS.has(flag) ? "⚠ " : "! "}
          {flag.replaceAll("_", " ")}
        </span>
      ))}
    </span>
  );
}
