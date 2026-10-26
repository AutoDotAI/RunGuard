import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DashboardSummary, Run, Worker } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  listWorkers: vi.fn(),
  getWorker: vi.fn(),
  listRuns: vi.fn(),
  getRun: vi.fn(),
  getRunLogs: vi.fn(),
  retryRun: vi.fn(),
  getDashboardSummary: vi.fn(),
}));

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    Link: ({
      children,
      to,
      params,
    }: {
      children: ReactNode;
      to: string;
      params?: { id?: string };
    }) => <a href={params?.id ? to.replace("$id", params.id) : to}>{children}</a>,
    useNavigate: () => mocks.navigate,
  };
});
vi.mock("@/lib/api/client", () => ({ api: mocks }));

import { Dashboard } from "@/routes/_app/dashboard";
import { Workers } from "@/routes/_app/workers.index";
import { WorkerDetailView } from "@/routes/_app/workers.$id.index";
import { RunDetailView } from "@/routes/_app/runs.$id";
import { WorkerForm } from "@/components/app/WorkerForm";
import { ApiError } from "@/lib/api/types";

const now = new Date().toISOString();
const worker: Worker = {
  id: "worker-1",
  name: "Docs check",
  kind: "website_monitor",
  url: "https://example.com/",
  created_at: now,
  updated_at: now,
  last_run: null,
};
const failedRun: Run = {
  id: "run-1",
  worker_id: worker.id,
  worker_name: worker.name,
  kind: worker.kind,
  status: "failed",
  url: worker.url,
  created_at: now,
  started_at: now,
  finished_at: now,
  duration_ms: 50,
  result: { http_status: 503, response_time_ms: 50, healthy: false },
  error: { code: "HTTP_STATUS_ERROR", message: "Website responded with HTTP 503." },
  retry_of_run_id: null,
};

function mount(element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
  return { ...view, client };
}

afterEach(() => {
  vi.resetAllMocks();
});

describe("P0 screens with the typed mock adapter", () => {
  it("shows a useful empty dashboard and a failed-run count from adapter data", async () => {
    const empty: DashboardSummary = {
      window: "24h",
      total: 0,
      succeeded: 0,
      failed: 0,
      success_rate: null,
      avg_duration_ms: null,
      recent_runs: [],
    };
    mocks.getDashboardSummary.mockResolvedValueOnce(empty);
    const view = mount(<Dashboard />);
    expect(await screen.findByText(/No runs yet/)).toBeInTheDocument();
    expect(screen.getAllByText("N/A")).toHaveLength(2);
    view.unmount();

    mocks.getDashboardSummary.mockResolvedValueOnce({
      ...empty,
      total: 1,
      failed: 1,
      success_rate: 0,
      avg_duration_ms: 50,
      recent_runs: [failedRun],
    });
    mount(<Dashboard />);
    expect(await screen.findByText(/1 failed run in the last 24 hours/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View details/i })).toHaveAttribute(
      "href",
      "/runs/run-1",
    );
  });

  it("shows a worker-list empty action and a request failure distinctly", async () => {
    mocks.listWorkers.mockResolvedValueOnce({ items: [], total: 0, limit: 20, offset: 0 });
    const view = mount(<Workers />);
    expect(await screen.findByRole("link", { name: /Create your first worker/i })).toHaveAttribute(
      "href",
      "/workers/new",
    );
    view.unmount();

    mocks.listWorkers.mockRejectedValueOnce(new Error("Workers unavailable"));
    mount(<Workers />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Workers unavailable");
  });

  it("disables Run Now for an active worker and reports a history-fetch failure", async () => {
    mocks.getWorker.mockResolvedValue({
      ...worker,
      last_run: { id: "run-active", status: "queued", created_at: now },
    });
    mocks.listRuns.mockRejectedValue(new Error("History unavailable"));
    mount(<WorkerDetailView id={worker.id} />);
    expect(await screen.findByRole("button", { name: /Run in progress/i })).toBeDisabled();
    expect(await screen.findByRole("alert")).toHaveTextContent("History unavailable");
  });

  it.each(["succeeded", "failed"] as const)(
    "re-enables Run Now when a queued run becomes %s in refreshed history",
    async (status) => {
      // getWorker deliberately stays stale to reproduce the original failure.
      mocks.getWorker.mockResolvedValue({
        ...worker,
        last_run: { id: "run-active", status: "queued", created_at: now },
      });
      mocks.listRuns.mockResolvedValueOnce({
        items: [{ ...failedRun, id: "run-active", status: "queued" }],
        total: 1,
        limit: 10,
        offset: 0,
      });
      const view = mount(<WorkerDetailView id={worker.id} />);
      expect(await screen.findByRole("button", { name: /Run in progress/i })).toBeDisabled();
      await waitFor(() => expect(mocks.listRuns).toHaveBeenCalledTimes(1));

      mocks.listRuns.mockResolvedValue({
        items: [{ ...failedRun, id: "run-active", status }],
        total: 1,
        limit: 10,
        offset: 0,
      });
      await view.client.invalidateQueries({ queryKey: ["runs", worker.id, 1] });
      expect(await screen.findByRole("button", { name: "Run now" })).toBeEnabled();
    },
  );

  it("keeps a failed run inspectable while manual retry routes to a distinct run", async () => {
    mocks.getRun.mockResolvedValue(failedRun);
    mocks.getRunLogs.mockResolvedValue({
      items: [{ id: 1, timestamp: now, level: "ERROR", event: "RUN_FAILED", message: "HTTP 503" }],
      next_after_id: 1,
    });
    mocks.retryRun.mockResolvedValue({
      run_id: "run-2",
      worker_id: worker.id,
      status: "queued",
      created_at: now,
    });
    mount(<RunDetailView id={failedRun.id} />);
    expect(await screen.findByText("HTTP_STATUS_ERROR")).toBeInTheDocument();
    expect(await screen.findByText("RUN_FAILED")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith({ to: "/runs/$id", params: { id: "run-2" } }),
    );
    expect(mocks.retryRun).toHaveBeenCalledWith("run-1");
    expect(screen.getByText("HTTP_STATUS_ERROR")).toBeInTheDocument();
  });

  it("validates worker URLs, locks the type on edit, and shows API errors", async () => {
    const save = vi
      .fn()
      .mockRejectedValue(new ApiError(409, "WORKER_LIMIT_REACHED", "Worker limit reached"));
    mount(
      <WorkerForm
        editing
        initial={{ name: worker.name, kind: worker.kind, url: worker.url }}
        submitLabel="Save changes"
        onSubmit={save}
      />,
    );
    expect(screen.getByRole("button", { name: /AI URL Summarizer/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "ftp://example.com/" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByText("Only http(s) URLs are allowed.")).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: worker.url } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Worker limit reached");
  });
});
