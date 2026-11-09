"use client";

import Link from "next/link";
import { ErrorNote, Loading } from "@/components/States";
import { api, call } from "@/lib/client";
import { useResource } from "@/lib/useResource";

const COLUMNS = ["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"];

interface Card {
  key: string;
  title: string;
  status: string;
  href: string;
}

export default function PipelinePage() {
  const { data, error, loading } = useResource(
    async (): Promise<Card[]> => {
      const [cards, approvals] = await Promise.all([
        call(api.GET("/api/pipeline")),
        call(api.GET("/api/approvals")),
      ]);
      const drafted = approvals
        .filter((item) => item.kind === "bid")
        .map((item) => ({
          key: item.thread_id,
          title: item.ref_id,
          status: "drafted",
          href: `/studio/${item.ref_id}`,
        }));
      return [
        ...drafted,
        ...cards.map((card) => ({
          key: card.proposal_id,
          title: card.title,
          status: card.status,
          href: `/jobs/${card.job_id}`,
        })),
      ];
    },
    "pipeline",
    5000,
  );

  if (loading) return <Loading label="Loading pipeline" />;
  if (error || !data) return <ErrorNote message={error ?? "The pipeline could not be loaded."} />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Pipeline</h1>
          <p className="muted">Cards move as the marketplace reports changes.</p>
        </div>
      </div>
      <div className="kanban">
        {COLUMNS.map((column) => {
          const cards = data.filter((card) => card.status === column);
          return (
            <section key={column} className="column" aria-label={column}>
              <h2>
                {column[0].toUpperCase() + column.slice(1)} ({cards.length})
              </h2>
              {cards.map((card) => (
                <div key={card.key} className="card">
                  <Link href={card.href}>{card.title}</Link>
                </div>
              ))}
            </section>
          );
        })}
      </div>
    </>
  );
}
