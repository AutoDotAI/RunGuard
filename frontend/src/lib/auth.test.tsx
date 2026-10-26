import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { auth, AuthProvider, useSession } from "./auth";
import { Route as ProtectedRoute } from "@/routes/_app/route";

const user = { id: "00000000-0000-4000-8000-000000000001", email: "person@example.com" };
const sessionKey = "runguard.auth.session";

function jwt(exp: number, sub = user.id) {
  const encoded = (value: object) => btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encoded({ alg: "HS256", typ: "JWT" })}.${encoded({ sub, exp })}.test-signature`;
}

function tokenResponse(token = jwt(Math.floor(Date.now() / 1000) + 3600)) {
  return { access_token: token, token_type: "bearer", user };
}

function http(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function SessionEmail() {
  return <span>{useSession()?.user.email ?? "signed out"}</span>;
}

function expectLoginRedirect() {
  try {
    ProtectedRoute.options.beforeLoad?.({} as never);
    throw new Error("Protected route did not redirect");
  } catch (error) {
    expect(error).toMatchObject({ options: { to: "/login" } });
  }
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("local API authentication", () => {
  it("registers once and uses the returned token without a second login", async () => {
    const fetchMock = vi.fn().mockResolvedValue(http(201, tokenResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const result = await auth.signUp(user.email, "CorrectHorse123!");
    expect(result.user).toEqual(user);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/auth/register");
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toEqual({ email: user.email, password: "CorrectHorse123!" });
    expect(auth.getAccessToken()).toBe(result.access_token);
    expect(window.sessionStorage.getItem(sessionKey)).not.toContain("CorrectHorse123!");
    expect(window.localStorage.length).toBe(0);
  });

  it("logs in and shows the returned email through the mounted provider", async () => {
    const fetchMock = vi.fn().mockResolvedValue(http(200, tokenResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<AuthProvider><SessionEmail /></AuthProvider>);
    expect(screen.getByText("signed out")).toBeTruthy();
    await auth.signIn(user.email, "CorrectHorse123!");
    await waitFor(() => expect(screen.getByText(user.email)).toBeTruthy());
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/auth/login");
    view.unmount();
  });

  it("shows duplicate and invalid-credential errors without retaining a session", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(http(409, { error: { code: "ACCOUNT_EXISTS", message: "An account with this email already exists" } }))
      .mockResolvedValueOnce(http(401, { error: { code: "INVALID_CREDENTIALS", message: "Invalid email or password" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(auth.signUp(user.email, "CorrectHorse123!")).rejects.toThrow("already exists");
    await expect(auth.signIn(user.email, "wrong-password")).rejects.toThrow("Invalid email or password");
    expect(auth.getSession()).toBeNull();
  });

  it("reports an unreachable API and leaves no session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));
    await expect(auth.signIn(user.email, "CorrectHorse123!")).rejects.toThrow("Could not reach the RunGuard API");
    expect(auth.getSession()).toBeNull();
  });

  it("retains a valid session across a remount, then clears an expired token", async () => {
    window.sessionStorage.setItem(sessionKey, JSON.stringify(tokenResponse()));
    const view = render(<AuthProvider><SessionEmail /></AuthProvider>);
    await waitFor(() => expect(screen.getByText(user.email)).toBeTruthy());
    view.unmount();
    expect(auth.getSession()?.user.email).toBe(user.email);
    window.sessionStorage.setItem(sessionKey, JSON.stringify(tokenResponse(jwt(Math.floor(Date.now() / 1000) - 1))));
    expect(auth.getSession()).toBeNull();
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expectLoginRedirect();
  });

  it("removes legacy plaintext mock users and rejects a fake mock session", async () => {
    window.localStorage.setItem("runguard.mockauth.users", JSON.stringify({ [user.email]: { password: "plaintext" } }));
    window.localStorage.setItem("runguard.mockauth.session", "mock.session");
    window.sessionStorage.setItem(sessionKey, JSON.stringify({ user, access_token: "mock.token" }));
    expect(auth.getSession()).toBeNull();
    expect(window.localStorage.getItem("runguard.mockauth.users")).toBeNull();
    expect(window.localStorage.getItem("runguard.mockauth.session")).toBeNull();
  });

  it("logs out by clearing its session and notifying the provider", async () => {
    window.sessionStorage.setItem(sessionKey, JSON.stringify(tokenResponse()));
    const view = render(<AuthProvider><SessionEmail /></AuthProvider>);
    await waitFor(() => expect(screen.getByText(user.email)).toBeTruthy());
    await auth.signOut();
    await waitFor(() => expect(screen.getByText("signed out")).toBeTruthy());
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expectLoginRedirect();
    view.unmount();
  });
});
