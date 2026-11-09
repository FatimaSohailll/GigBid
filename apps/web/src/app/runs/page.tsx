"use client";

import Link from "next/link";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { api, call, statusLabel } from "@/lib/client";
import { useResource } from "@/lib/useResource";

export default function RunsPage() {
  const { data, error, loading } = useResource(() => call(api.GET("/api/runs")), "runs", 5000);
  return (
    <>
      <div className="page-head">
        <h1>Agent runs</h1>
      </div>
      {loading && <Loading label="Loading runs" />}
      {error && <ErrorNote message={error} />}
      {data?.length === 0 && <Empty title="No runs yet" />}
      {data && data.length > 0 && (
        <div className="panel table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">Type</th>
                <th scope="col">Status</th>
                <th scope="col">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {data.map((run) => (
                <tr key={run.thread_id}>
                  <td>
                    <Link href={`/runs/${encodeURIComponent(run.thread_id)}`}>{run.ref_id}</Link>
                  </td>
                  <td>{run.kind}</td>
                  <td>{statusLabel(run.status)}</td>
                  <td>{run.tokens.toLocaleString("en-US")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
