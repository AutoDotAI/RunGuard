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

describe("P0 screens with the typed API adapter", () => {
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

  it("shows dashboard loading and a request error without displaying counts", async () => {
    mocks.getDashboardSummary.mockReturnValueOnce(new Promise(() => {}));
    const view = mount(<Dashboard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading dashboard");
    view.unmount();

    mocks.getDashboardSummary.mockRejectedValueOnce(new Error("Dashboard unavailable"));
    mount(<Dashboard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Dashboard unavailable");
    expect(screen.queryByText("Recent executions")).not.toBeInTheDocument();
  });

  it("hides previously loaded dashboard data when a refresh is forbidden", async () => {
    const data: DashboardSummary = {
      window: "24h", total: 9, succeeded: 9, failed: 0,
      success_rate: 1, avg_duration_ms: 50, recent_runs: [],
    };
    mocks.getDashboardSummary.mockResolvedValueOnce(data);
    const view = mount(<Dashboard />);
    await waitFor(() => expect(screen.getAllByText("9")).toHaveLength(2));
    mocks.getDashboardSummary.mockRejectedValue(new ApiError(403, "FORBIDDEN", "Access denied"));
    await view.client.invalidateQueries({ queryKey: ["dashboard"] });
    expect(await screen.findByRole("alert")).toHaveTextContent("Access denied");
    expect(screen.queryAllByText("9")).toHaveLength(0);
    view.unmount();
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

  it("shows worker loading and an active row whose Run action is disabled", async () => {
    mocks.listWorkers.mockReturnValueOnce(new Promise(() => {}));
    const view = mount(<Workers />);
    expect(screen.queryByText("No workers yet.")).not.toBeInTheDocument();
    view.unmount();

    mocks.listWorkers.mockResolvedValueOnce({
      items: [{ ...worker, last_run: { id: "run-active", status: "running", created_at: now } }],
      total: 1, limit: 20, offset: 0,
    });
    mount(<Workers />);
    expect(await screen.findByText("Docs check")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("shows worker-detail loading, empty history, and worker API failure", async () => {
    mocks.getWorker.mockReturnValueOnce(new Promise(() => {}));
    mocks.listRuns.mockReturnValueOnce(new Promise(() => {}));
    const loading = mount(<WorkerDetailView id={worker.id} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading execution history");
    loading.unmount();

    mocks.getWorker.mockResolvedValueOnce(worker);
    mocks.listRuns.mockResolvedValueOnce({ items: [], total: 0, limit: 10, offset: 0 });
    const empty = mount(<WorkerDetailView id={worker.id} />);
    expect(await screen.findByText(/This worker hasn't run yet/)).toBeInTheDocument();
    empty.unmount();

    mocks.getWorker.mockRejectedValueOnce(new Error("Worker unavailable"));
    mocks.listRuns.mockResolvedValueOnce({ items: [], total: 0, limit: 10, offset: 0 });
    mount(<WorkerDetailView id={worker.id} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Worker unavailable");
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

  it("shows ordered monitor logs and result, but no Retry for success", async () => {
    mocks.getRun.mockResolvedValue({ ...failedRun, status: "succeeded", error: null,
      result: { http_status: 200, response_time_ms: 42, healthy: true } });
    mocks.getRunLogs.mockResolvedValue({
      items: [
        { id: 1, timestamp: "2026-01-01T00:00:00Z", level: "INFO", event: "RUN_STARTED", message: "Started" },
        { id: 2, timestamp: "2026-01-01T00:00:01Z", level: "INFO", event: "RUN_SUCCEEDED", message: "Done" },
      ],
      next_after_id: 2,
    });
    mount(<RunDetailView id="run-1" />);
    expect(await screen.findByText("RUN_STARTED")).toBeInTheDocument();
    expect(screen.getByText("RUN_SUCCEEDED")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    const events = screen.getAllByText(/RUN_(STARTED|SUCCEEDED)/).map((node) => node.textContent);
    expect(events).toEqual(["RUN_STARTED", "RUN_SUCCEEDED"]);
  });

  it("shows a summarizer output", async () => {
    mocks.getRun.mockResolvedValue({ ...failedRun, kind: "url_summarizer", status: "succeeded", error: null,
      result: { source_url: worker.url, page_title: "Example", summary: "## Summary\n- One point", model: "test-model", input_was_truncated: true } });
    mocks.getRunLogs.mockResolvedValue({ items: [], next_after_id: 0 });
    mount(<RunDetailView id="run-1" />);
    expect(await screen.findByText("Example")).toBeInTheDocument();
    expect(screen.getByText("One point")).toBeInTheDocument();
    expect(screen.getByText("model: test-model")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
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

  it("requires name and URL, and passes valid create data to the save callback", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    mount(<WorkerForm submitLabel="Create worker" onSubmit={save} />);
    fireEvent.click(screen.getByRole("button", { name: "Create worker" }));
    expect(screen.getByText("Name is required.")).toBeInTheDocument();
    expect(screen.getByText("URL is required.")).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New check" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "https://example.com/" } });
    fireEvent.click(screen.getByRole("button", { name: "Create worker" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ name: "New check", kind: "website_monitor", url: "https://example.com/" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
