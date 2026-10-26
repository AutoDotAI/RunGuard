import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigate = vi.hoisted(() => vi.fn());

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...await importOriginal<typeof import("@tanstack/react-router")>(),
  useNavigate: () => navigate,
}));

import { Route } from "@/routes/logout";

const sessionKey = "runguard.auth.session";

describe("logout route", () => {
  beforeEach(() => {
    navigate.mockReset();
    window.sessionStorage.clear();
  });

  it("clears the stored session and query cache before returning to login", async () => {
    const client = new QueryClient();
    client.setQueryData(["workers"], { items: [{ id: "old-account-worker" }] });
    window.sessionStorage.setItem(sessionKey, "old-account-session");
    const Logout = Route.options.component!;
    render(<QueryClientProvider client={client}><Logout /></QueryClientProvider>);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: "/login", replace: true }));
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expect(client.getQueryData(["workers"])).toBeUndefined();
  });
});
