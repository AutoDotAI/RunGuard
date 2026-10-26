import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ navigate: vi.fn(), createWorker: vi.fn(), getWorker: vi.fn(), updateWorker: vi.fn() }));
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...await importOriginal<typeof import("@tanstack/react-router")>(),
  useNavigate: () => mocks.navigate,
}));
vi.mock("@/lib/api/client", () => ({ api: { createWorker: mocks.createWorker, getWorker: mocks.getWorker, updateWorker: mocks.updateWorker } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { Route } from "@/routes/_app/workers.new";
import { Route as EditRoute } from "@/routes/_app/workers.$id.edit";
import { toast } from "sonner";

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const NewWorker = Route.options.component!;
  render(<QueryClientProvider client={client}><NewWorker /></QueryClientProvider>);
}

describe("create worker route", () => {
  it("navigates to the saved worker only after a successful API response", async () => {
    mocks.createWorker.mockResolvedValueOnce({ id: "worker-new" });
    mount();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New check" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "https://example.com/" } });
    fireEvent.click(screen.getByRole("button", { name: "Create worker" }));
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith({ to: "/workers/$id", params: { id: "worker-new" } }));
    expect(mocks.createWorker).toHaveBeenCalledWith({ name: "New check", kind: "website_monitor", url: "https://example.com/" });
    expect(toast.success).toHaveBeenCalledWith("Worker created");
  });

  it("shows API rejection and stays on the form", async () => {
    mocks.navigate.mockClear();
    mocks.createWorker.mockRejectedValueOnce(new Error("Cannot save worker"));
    mount();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New check" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "https://example.com/" } });
    fireEvent.click(screen.getByRole("button", { name: "Create worker" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot save worker");
    expect(mocks.navigate).not.toHaveBeenCalled();
  });
});

describe("edit worker route", () => {
  it("keeps kind immutable and navigates only after the updated worker is saved", async () => {
    vi.spyOn(EditRoute, "useParams").mockReturnValue({ id: "worker-1" } as never);
    mocks.getWorker.mockResolvedValueOnce({
      id: "worker-1", name: "Old check", kind: "website_monitor", url: "https://example.com/",
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", last_run: null,
    });
    mocks.updateWorker.mockResolvedValueOnce({ id: "worker-1" });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const EditWorker = EditRoute.options.component!;
    render(<QueryClientProvider client={client}><EditWorker /></QueryClientProvider>);
    expect(await screen.findByDisplayValue("Old check")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /AI URL Summarizer/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Updated check" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(mocks.updateWorker).toHaveBeenCalledWith("worker-1", {
      name: "Updated check", url: "https://example.com/",
    }));
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/workers/$id", params: { id: "worker-1" } });
  });
});
