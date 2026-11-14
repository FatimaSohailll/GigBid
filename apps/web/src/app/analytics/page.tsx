"use client";

import { Empty, ErrorNote, Loading } from "@/components/States";
import { api, call, usd } from "@/lib/client";
import { useResource } from "@/lib/useResource";

const percent = (value: number) => `${Math.round(value * 100)}%`;

export default function AnalyticsPage() {
  const { data, error, loading } = useResource(
    () => call(api.GET("/api/analytics")),
    "analytics",
    15000,
  );
  if (loading) return <Loading label="Loading analytics" />;
  if (error || !data) return <ErrorNote message={error ?? "Analytics could not be loaded."} />;

  const submitted = data.by_category.reduce((sum, row) => sum + row.submitted, 0);
  const replied = data.by_category.reduce((sum, row) => sum + row.replied, 0);
  const hired = data.by_category.reduce((sum, row) => sum + row.hired, 0);
  const connects = data.by_category.reduce((sum, row) => sum + row.connects_spent, 0);
  const tiles = [
    ["Reply rate", submitted ? percent(replied / submitted) : "None yet"],
    ["Hire rate", submitted ? percent(hired / submitted) : "None yet"],
    ["Connects spent", connects.toLocaleString("en-US")],
    ["Tokens per run", Math.round(data.mean_tokens_per_run).toLocaleString("en-US")],
    ["p95 run time", `${data.p95_latency_seconds.toFixed(1)}s`],
    ["Model cost per run", usd(data.mean_cost_per_run_usd)],
    ["Model cost per proposal", usd(data.mean_cost_per_proposal_usd)],
  ];

  return (
    <>
      <div className="page-head">
        <h1>Analytics</h1>
      </div>
      <div className="tiles">
        {tiles.map(([label, value]) => (
          <div key={label} className="tile">
            <div className="tile-label">{label}</div>
            <div className="tile-value">{value}</div>
          </div>
        ))}
      </div>
      {data.by_category.length === 0 ? (
        <Empty title="No submitted proposals yet" />
      ) : (
        <>
          <section className="panel">
            <h2>Reply rate by category</h2>
            {data.by_category.map((row) => (
              <div
                key={row.category}
                className="bar-row"
                title={`${row.replied} replies from ${row.submitted} proposals`}
              >
                <span>{row.category.replaceAll("_", " ")}</span>
                <span className="bar-track">
                  <span className="bar-fill" style={{ width: percent(row.reply_rate) }} />
                </span>
                <span>{percent(row.reply_rate)}</span>
              </div>
            ))}
          </section>
          <section className="panel table-scroll">
            <h2>Connects spent versus outcomes</h2>
            <table>
              <thead>
                <tr>
                  <th scope="col">Category</th>
                  <th scope="col">Submitted</th>
                  <th scope="col">Replied</th>
                  <th scope="col">Hired</th>
                  <th scope="col">Connects</th>
                </tr>
              </thead>
              <tbody>
                {data.by_category.map((row) => (
                  <tr key={row.category}>
                    <td>{row.category.replaceAll("_", " ")}</td>
                    <td>{row.submitted}</td>
                    <td>{row.replied}</td>
                    <td>{row.hired}</td>
                    <td>{row.connects_spent}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
      <section className="panel">
        <h2>Top rejection reasons</h2>
        {data.top_rejection_reasons.length === 0 ? (
          <p className="muted">No rejections yet.</p>
        ) : (
          <ul>
            {data.top_rejection_reasons.map((row) => (
              <li key={row.reason}>
                {row.reason} ({row.count})
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
