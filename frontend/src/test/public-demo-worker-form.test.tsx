import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("public demo worker form", () => {
  it("shows the server host restriction and labels live summaries unavailable", async () => {
    vi.stubEnv("VITE_ALLOWED_HOSTS", "example.com,www.python.org");
    vi.stubEnv("VITE_SUMMARIZER_AVAILABLE", "false");
    vi.resetModules();
    const { WorkerForm } = await import("@/components/app/WorkerForm");
    render(<WorkerForm onSubmit={vi.fn()} submitLabel="Create worker" />);
    expect(screen.getByText(/This demo accepts only these public hosts: example.com,www.python.org/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /AI URL Summarizer/ })).toBeDisabled();
    expect(screen.getByText("Live summaries are unavailable in this demo.")).toBeInTheDocument();
  });
});
