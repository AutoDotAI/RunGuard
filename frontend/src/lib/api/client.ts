// The only app-data transport. The server owns authentication and record ownership.
import { apiBaseUrl, auth } from "@/lib/auth";
import { ApiError, type ApiClient } from "./types";

type Query = Record<string, string | number | undefined>;

function pathWithQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

function resourceId(id: string): string {
  return encodeURIComponent(id);
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const token = auth.getAccessToken();
  if (!token) throw new ApiError(401, "UNAUTHORIZED", "Sign in required.");

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1${pathWithQuery(path, query)}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Could not reach the RunGuard API. Please try again.");
  }

  if (response.status === 401 && auth.getAccessToken() === token) {
    await auth.signOut();
  }

  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    const error = data && typeof data === "object" && "error" in data ? data.error : null;
    const fields = error && typeof error === "object" ? error : null;
    const code = fields && "code" in fields && typeof fields.code === "string" ? fields.code : "HTTP_ERROR";
    const message = fields && "message" in fields && typeof fields.message === "string"
      ? fields.message : response.status === 401 ? "Your session has expired. Please sign in again."
        : `The request failed (HTTP ${response.status}).`;
    const details = fields && "details" in fields && fields.details && typeof fields.details === "object" && !Array.isArray(fields.details)
      ? fields.details as Record<string, unknown> : undefined;
    throw new ApiError(response.status, code, message, details);
  }

  if (response.status === 204) return undefined as T;
  try {
    const data: unknown = await response.json();
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      throw new Error("Invalid response shape");
    }
    return data as T;
  } catch {
    throw new ApiError(502, "INVALID_RESPONSE", "The RunGuard API returned an invalid response.");
  }
}

export const api: ApiClient = {
  listWorkers: (pagination = {}) => request("GET", "/workers", undefined, pagination),
  getWorker: (id) => request("GET", `/workers/${resourceId(id)}`),
  createWorker: (input) => request("POST", "/workers", input),
  updateWorker: (id, input) => request("PATCH", `/workers/${resourceId(id)}`, input),
  archiveWorker: (id) => request("DELETE", `/workers/${resourceId(id)}`),
  runWorker: (id) => request("POST", `/workers/${resourceId(id)}/runs`),
  listRuns: (filters = {}) => request("GET", "/runs", undefined, filters),
  getRun: (id) => request("GET", `/runs/${resourceId(id)}`),
  getRunLogs: (id, afterId = 0) => request("GET", `/runs/${resourceId(id)}/logs`, undefined, { after_id: afterId }),
  retryRun: (id) => request("POST", `/runs/${resourceId(id)}/retry`),
  getDashboardSummary: () => request("GET", "/dashboard/summary", undefined, { window: "24h" }),
  getAlerts: () => request("GET", "/alerts"),
};
