import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Run } from "@/lib/api/types";

const api = vi.hoisted(() => ({ getRun: vi.fn(), getRunLogs: vi.fn(), retryRun: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api }));
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, Link: ({ children }: { children: ReactNode }) => <a>{children}</a>, useNavigate: () => vi.fn() };
});

import { RunDetailView } from "@/routes/_app/runs.$id";

const queued: Run = {
  id: "run-poll", worker_id: "worker-1", worker_name: "Poll check", kind: "website_monitor",
  status: "queued", url: "https://example.com/", created_at: "2026-01-01T00:00:00Z",
  started_at: null, finished_at: null, duration_ms: null, result: null, error: null,
  retry_of_run_id: null,
};

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><RunDetailView id={queued.id} /></QueryClientProvider>);
}

async function tick(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe("active run polling", () => {
  it("polls status and log cursor, deduplicates rows, then stops after completion and unmount", async () => {
    vi.useFakeTimers();
    api.getRun.mockResolvedValueOnce(queued).mockResolvedValueOnce(queued).mockResolvedValue({
      ...queued, status: "succeeded", started_at: queued.created_at, finished_at: "2026-01-01T00:00:02Z",
      duration_ms: 2000, result: { http_status: 200, response_time_ms: 20, healthy: true },
    });
    api.getRunLogs.mockResolvedValueOnce({
      items: [{ id: 1, timestamp: queued.created_at, level: "INFO", event: "START", message: "First" }], next_after_id: 1,
    }).mockResolvedValueOnce({
      items: [{ id: 2, timestamp: "2026-01-01T00:00:01Z", level: "INFO", event: "CHECK", message: "Second" }], next_after_id: 2,
    }).mockResolvedValue({ items: [], next_after_id: 2 });

    const view = mount();
    await tick(0);
    expect(screen.getByText("START")).toBeInTheDocument();
    expect(api.getRunLogs).toHaveBeenCalledWith(queued.id, 0);
    await tick(2500);
    expect(api.getRun).toHaveBeenCalledTimes(2);
    expect(api.getRunLogs).toHaveBeenCalledWith(queued.id, 1);
    expect(screen.getByText("CHECK")).toBeInTheDocument();
    await tick(2500);
    await tick(100);
    expect(api.getRun).toHaveBeenCalledTimes(3);
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    const statusCalls = api.getRun.mock.calls.length;
    const logCalls = api.getRunLogs.mock.calls.length;
    await tick(10000);
    expect(api.getRun).toHaveBeenCalledTimes(statusCalls);
    expect(api.getRunLogs).toHaveBeenCalledTimes(logCalls);
    expect(screen.getAllByText("START")).toHaveLength(1);
    view.unmount();
    await tick(10000);
    expect(api.getRun).toHaveBeenCalledTimes(statusCalls);
  });

  it("backs off failed active requests rather than retrying in a tight loop", async () => {
    vi.useFakeTimers();
    api.getRun.mockRejectedValue(new Error("API down"));
    api.getRunLogs.mockRejectedValue(new Error("Logs down"));
    const view = mount();
    await tick(0);
    const calls = api.getRun.mock.calls.length;
    expect(calls).toBe(1);
    await tick(1000);
    expect(api.getRun).toHaveBeenCalledTimes(calls);
    await tick(1500);
    expect(api.getRun.mock.calls.length).toBeLessThanOrEqual(2);
    view.unmount();
  });
});
