import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { auth } from "@/lib/auth";
import { Route as ProtectedRoute } from "@/routes/_app/route";
import { api } from "./client";
import { ApiError, type DashboardSummary, type RunLogPage, type Worker } from "./types";

const user = { id: "00000000-0000-4000-8000-000000000001", email: "person@example.com" };
const jwt = () => {
  const payload = btoa(JSON.stringify({ sub: user.id, exp: Math.floor(Date.now() / 1000) + 3600 }))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `header.${payload}.signature`;
};
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
});
const worker: Worker = {
  id: "worker-1", name: "Docs", kind: "website_monitor", url: "https://example.com/",
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", last_run: null,
};
const summary: DashboardSummary = {
  window: "24h", total: 0, succeeded: 0, failed: 0,
  success_rate: null, avg_duration_ms: null, recent_runs: [],
};

async function signIn() {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, {
    access_token: jwt(), token_type: "bearer", user,
  })));
  await auth.signIn(user.email, "CorrectHorse123!");
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("FastAPI data client", () => {
  it("requires a session before any protected fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.listWorkers()).rejects.toMatchObject({ status: 401, code: "UNAUTHORIZED" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the configured API origin, bearer token, exact worker methods, bodies, and 204 archive", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "http://127.0.0.1:8000/");
    await signIn();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json(200, { items: [], total: 0, limit: 7, offset: 14 }))
      .mockResolvedValueOnce(json(200, worker))
      .mockResolvedValueOnce(json(201, worker))
      .mockResolvedValueOnce(json(200, worker))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await api.listWorkers({ limit: 7, offset: 14 })).items).toEqual([]);
    expect(await api.getWorker(worker.id)).toEqual(worker);
    expect(await api.createWorker({ name: worker.name, kind: worker.kind, url: worker.url })).toEqual(worker);
    expect(await api.updateWorker(worker.id, { name: "New", url: worker.url })).toEqual(worker);
    expect(await api.archiveWorker(worker.id)).toBeUndefined();

    const [list, get, create, update, archive] = fetchMock.mock.calls;
    expect(fetchMock).toHaveBeenCalledTimes(5);
    if (!list || !get || !create || !update || !archive) throw new Error("Expected five API calls");
    expect(list[0]).toBe("http://127.0.0.1:8000/api/v1/workers?limit=7&offset=14");
    expect(get[0]).toBe("http://127.0.0.1:8000/api/v1/workers/worker-1");
    expect(create[0]).toBe("http://127.0.0.1:8000/api/v1/workers");
    expect(update[0]).toBe("http://127.0.0.1:8000/api/v1/workers/worker-1");
    expect(archive[0]).toBe("http://127.0.0.1:8000/api/v1/workers/worker-1");
    expect([list[1].method, get[1].method, create[1].method, update[1].method, archive[1].method])
      .toEqual(["GET", "GET", "POST", "PATCH", "DELETE"]);
    expect(JSON.parse(create[1].body)).toEqual({ name: worker.name, kind: worker.kind, url: worker.url });
    expect(JSON.parse(update[1].body)).toEqual({ name: "New", url: worker.url });
    expect(list[1].body).toBeUndefined();
    for (const [, init] of fetchMock.mock.calls) {
      expect(init.headers.Authorization).toBe(`Bearer ${auth.getAccessToken()}`);
      expect(JSON.stringify(init)).not.toContain("CorrectHorse123!");
    }
  });

  it("maps run, log cursor, retry, and 24-hour summary operations with 202 bodies", async () => {
    await signIn();
    const enqueued = { run_id: "run-1", worker_id: worker.id, status: "queued", created_at: worker.created_at };
    const logs: RunLogPage = {
      items: [{ id: 9, timestamp: worker.created_at, level: "INFO", event: "RUN_QUEUED", message: "Queued" }],
      next_after_id: 9,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json(202, enqueued))
      .mockResolvedValueOnce(json(200, { items: [], total: 0, limit: 5, offset: 5 }))
      .mockResolvedValueOnce(json(200, { id: "run-1" }))
      .mockResolvedValueOnce(json(200, logs))
      .mockResolvedValueOnce(json(200, { items: [], next_after_id: 9 }))
      .mockResolvedValueOnce(json(202, enqueued))
      .mockResolvedValueOnce(json(200, summary));
    vi.stubGlobal("fetch", fetchMock);

    expect(await api.runWorker(worker.id)).toEqual(enqueued);
    expect((await api.listRuns({ worker_id: worker.id, status: "failed", page: 2, page_size: 5 })).items).toEqual([]);
    expect((await api.getRun("run-1")).id).toBe("run-1");
    expect((await api.getRunLogs("run-1", 0)).items).toEqual(logs.items);
    expect(await api.getRunLogs("run-1", logs.next_after_id)).toEqual({ items: [], next_after_id: 9 });
    expect(await api.retryRun("run-1")).toEqual(enqueued);
    expect(await api.getDashboardSummary()).toEqual(summary);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/workers/worker-1/runs",
      "/api/v1/runs?worker_id=worker-1&status=failed&page=2&page_size=5",
      "/api/v1/runs/run-1",
      "/api/v1/runs/run-1/logs?after_id=0",
      "/api/v1/runs/run-1/logs?after_id=9",
      "/api/v1/runs/run-1/retry",
      "/api/v1/dashboard/summary?window=24h",
    ]);
    expect(fetchMock.mock.calls.map(([, init]) => init.method)).toEqual([
      "POST", "GET", "GET", "GET", "GET", "POST", "GET",
    ]);
    expect(fetchMock.mock.calls.every(([, init]) => init.body === undefined)).toBe(true);
  });

  it("clears the rejected session on 401 and preserves it for other API errors", async () => {
    await signIn();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json(403, { error: { code: "FORBIDDEN", message: "Not allowed", details: { reason: "policy" } } }))
      .mockResolvedValueOnce(json(404, { error: { code: "NOT_FOUND", message: "Missing" } }))
      .mockResolvedValueOnce(json(409, { error: { code: "RUN_ALREADY_ACTIVE", message: "Busy", details: { run_id: "run-1" } } }))
      .mockResolvedValueOnce(json(422, { error: { code: "VALIDATION_ERROR", message: "Bad URL" } }))
      .mockResolvedValueOnce(json(401, { error: { code: "INVALID_TOKEN", message: "Token expired" } }));
    vi.stubGlobal("fetch", fetchMock);
    for (const [status, code] of [[403, "FORBIDDEN"], [404, "NOT_FOUND"], [409, "RUN_ALREADY_ACTIVE"], [422, "VALIDATION_ERROR"]] as const) {
      await expect(api.getWorker(worker.id)).rejects.toMatchObject({ status, code });
      expect(auth.getSession()).not.toBeNull();
    }
    await expect(api.getWorker(worker.id)).rejects.toMatchObject({ status: 401, code: "INVALID_TOKEN", message: "Token expired" });
    expect(auth.getSession()).toBeNull();
    try {
      ProtectedRoute.options.beforeLoad?.({} as never);
      throw new Error("Protected route did not redirect");
    } catch (error) {
      expect(error).toMatchObject({ options: { to: "/login" } });
    }
  });

  it("turns unavailable, malformed error, and invalid success responses into safe ApiErrors", async () => {
    await signIn();
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error("secret.internal.hostname"))
      .mockResolvedValueOnce(new Response("not-json", { status: 500 }))
      .mockResolvedValueOnce(new Response("not-json", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.listWorkers()).rejects.toMatchObject({ status: 0, code: "NETWORK_ERROR" });
    await expect(api.listWorkers()).rejects.toMatchObject({ status: 500, code: "HTTP_ERROR" });
    await expect(api.listWorkers()).rejects.toMatchObject({ status: 502, code: "INVALID_RESPONSE" });
    expect(auth.getSession()).not.toBeNull();
  });
});
