"use client";

import { useState, type FormEvent } from "react";
import { ErrorNote } from "@/components/States";
import { api, call } from "@/lib/client";
import { useResource } from "@/lib/useResource";

const ITEM_TYPES = [
  ["case_study", "Case study"],
  ["cv", "CV"],
  ["proposal", "Past proposal"],
  ["review", "Client review"],
  ["readme", "Project README"],
  ["code", "Code"],
] as const;

/** The user's evidence: what GigBid can cite, how to add more, and how to remove an item. */
export function PortfolioManager() {
  const evidence = useResource(() => call(api.GET("/api/portfolio/evidence")), "evidence");
  const uploads = useResource(() => call(api.GET("/api/portfolio/uploads")), "uploads", 5000);
  const [problem, setProblem] = useState<string>();
  const [note, setNote] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [repository, setRepository] = useState("");

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setProblem(undefined);
    setNote(undefined);
    setBusy(true);
    try {
      // Multipart upload: the generated client sends JSON, so this one call uses fetch directly.
      const response = await fetch("/api/portfolio/uploads", {
        method: "POST",
        body: new FormData(form),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail ?? "The file could not be uploaded.");
      }
      form.reset();
      setNote("Uploaded. GigBid is indexing it; it appears below in a minute.");
      await uploads.reload();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "The file could not be uploaded.");
    } finally {
      setBusy(false);
    }
  }

  async function importRepository(event: FormEvent) {
    event.preventDefault();
    setProblem(undefined);
    setNote(undefined);
    try {
      await call(api.POST("/api/portfolio/github", { body: { repository } }));
      setRepository("");
      setNote("Importing the repository's README and code through GitHub's MCP server.");
      await uploads.reload();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "The repository could not be imported.");
    }
  }

  async function remove(id: string, title: string) {
    if (!window.confirm(`Remove "${title}" from your portfolio? Drafts can no longer cite it.`)) {
      return;
    }
    await call(
      api.DELETE("/api/portfolio/evidence/{item_id}", { params: { path: { item_id: id } } }),
    );
    setNote(`"${title}" is being removed.`);
    await evidence.reload();
  }

  const items = evidence.data ?? [];
  return (
    <>
      {problem && <ErrorNote message={problem} />}
      {note && (
        <p className="small" role="status">
          {note}
        </p>
      )}

      <form onSubmit={upload} aria-label="Upload portfolio item">
        <h3>Upload a file</h3>
        <label>
          File (PDF, Markdown or text)
          <input name="file" type="file" accept=".pdf,.md,.markdown,.txt" required />
        </label>
        <label>
          Title
          <input name="title" required maxLength={200} />
        </label>
        <label>
          Type
          <select name="item_type" defaultValue="case_study">
            {ITEM_TYPES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Skills, separated by commas
          <input name="skills" maxLength={500} placeholder="Python, dbt, Airflow" />
        </label>
        <label>
          Project
          <input name="project" maxLength={128} />
        </label>
        <label>
          Date
          <input name="date" maxLength={32} placeholder="2025-06" />
        </label>
        <label>
          Client industry
          <input name="client_industry" maxLength={64} />
        </label>
        <label>
          Outcome (for past proposals)
          <select name="outcome" defaultValue="">
            <option value="">Not applicable</option>
            <option value="won">Won</option>
            <option value="replied">Got a reply</option>
            <option value="ignored">No reply</option>
          </select>
        </label>
        <button className="primary" disabled={busy}>
          Upload
        </button>
      </form>

      <form onSubmit={importRepository} aria-label="Import a GitHub repository">
        <h3>Import a GitHub repository</h3>
        <label>
          Repository (owner/name)
          <input
            value={repository}
            onChange={(e) => setRepository(e.target.value)}
            pattern="[\w.-]+/[\w.-]+"
            placeholder="octocat/hello-world"
            required
          />
        </label>
        <button>Import</button>
      </form>

      {(uploads.data ?? []).length > 0 && (
        <>
          <h3>Recent imports</h3>
          <ul className="comments">
            {(uploads.data ?? []).slice(0, 5).map((item) => (
              <li key={item.id}>
                {item.filename}: {item.status.replaceAll("_", " ")}
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Evidence GigBid can cite ({items.length})</h3>
      {items.length === 0 ? (
        <p className="muted small">Nothing yet. Upload a file or import a repository.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Title</th>
                <th scope="col">Type</th>
                <th scope="col">Skills</th>
                <th scope="col">Outcome</th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td title={item.source}>{item.title}</td>
                  <td>{item.type.replaceAll("_", " ")}</td>
                  <td className="small">{item.skills.join(", ")}</td>
                  <td>{item.outcome ?? ""}</td>
                  <td>
                    <button
                      type="button"
                      className="ghost"
                      aria-label={`Remove ${item.title}`}
                      onClick={() => void remove(item.id, item.title)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
