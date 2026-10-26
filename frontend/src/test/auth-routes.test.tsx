import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ navigate: vi.fn(), signIn: vi.fn(), signUp: vi.fn() }));
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...await importOriginal<typeof import("@tanstack/react-router")>(),
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
  useNavigate: () => mocks.navigate,
}));
vi.mock("@/lib/auth", () => ({ auth: { signIn: mocks.signIn, signUp: mocks.signUp } }));

import { Route as LoginRoute } from "@/routes/login";
import { Route as SignupRoute } from "@/routes/signup";

function mount(route: typeof LoginRoute | typeof SignupRoute) {
  const client = new QueryClient();
  client.setQueryData(["workers"], { items: [{ id: "old-account-worker" }] });
  const Page = route.options.component!;
  render(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
  return client;
}

function submit(label: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "CorrectHorse123!" } });
  fireEvent.click(screen.getByRole("button", { name: label }));
}

beforeEach(() => vi.resetAllMocks());

describe("auth routes", () => {
  it.each([
    ["login", LoginRoute, "Sign in", mocks.signIn],
    ["signup", SignupRoute, "Create account", mocks.signUp],
  ] as const)("%s clears old account data before entering the app", async (_name, route, label, authCall) => {
    authCall.mockResolvedValueOnce({ user: { id: "new-user", email: "new@example.com" } });
    const client = mount(route);
    submit(label);
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith({ to: "/dashboard" }));
    expect(authCall).toHaveBeenCalledWith("new@example.com", "CorrectHorse123!");
    expect(client.getQueryData(["workers"])).toBeUndefined();
  });
});
