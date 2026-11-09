import createClient from "openapi-fetch";
import type { components, paths } from "./api/schema";

export const api = createClient<paths>({ baseUrl: "" });
export type Schemas = components["schemas"];

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function errorMessage(error: unknown): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg).join(" ");
  return "Something went wrong. Please try again.";
}

/** Unwraps a generated client call, and sends signed-out users to the sign-in page. */
export async function call<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await request;
  if (response.ok) return data as T;
  if (response.status === 401 && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
  throw new ApiError(errorMessage(error), response.status);
}

export const ACTIVE_RUN_STATES = new Set(["queued", "running", "resuming", "waiting_for_service"]);

export function statusLabel(status: string): string {
  const label = status.replaceAll("_", " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function money(value: number | null | undefined): string {
  return value == null ? "Not priced" : `$${Math.round(value).toLocaleString("en-US")}`;
}
