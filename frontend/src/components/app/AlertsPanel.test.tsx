import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { Alert } from "@/lib/api/types";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, params }: { children: ReactNode; params: { id: string } }) =>
    <a href={`/runs/${params.id}`}>{children}</a>,
}));

import { AlertsPanel } from "./AlertsPanel";

const open: Alert = {
  id: "alert-1", kind: "repeated_run_failure", severity: "warning", status: "open",
  title: "Repeated job failures", run_id: "run-1", symptom: "Two jobs failed.",
  fingerprint: "safe", created_at: "2026-10-26T12:00:00Z", resolved_at: null,
  trace_id: "abc123",
};

describe("user-impact alerts panel", () => {
  it("shows loading, empty, and API error separately", () => {
    const { rerender } = render(<AlertsPanel data={undefined} loading error={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading alerts");
    rerender(<AlertsPanel data={{ items: [], total: 0 }} loading={false} error={null} />);
    expect(screen.getByText("No open alerts.")).toBeInTheDocument();
    rerender(<AlertsPanel data={undefined} loading={false} error={new Error("private API error")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Alerts unavailable");
    expect(screen.queryByText("private API error")).not.toBeInTheDocument();
  });

  it("links an open alert to run logs and exposes resolved history", () => {
    render(<AlertsPanel data={{ items: [open, {
      ...open, id: "alert-2", status: "resolved", resolved_at: "2026-10-26T12:05:00Z",
    }], total: 2 }} loading={false} error={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Repeated job failures");
    expect(screen.getByText("Trace: abc123")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Inspect run and timestamped logs" }))
      .toHaveAttribute("href", "/runs/run-1");
    expect(screen.getByText("Resolved alerts (1)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Inspect run" })).toHaveAttribute("href", "/runs/run-1");
  });
});
