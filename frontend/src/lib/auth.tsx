import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export interface Session {
  user: { id: string; email: string };
  access_token: string;
}

export interface AuthAdapter {
  getSession(): Session | null;
  getAccessToken(): string | null;
  signUp(email: string, password: string): Promise<Session>;
  signIn(email: string, password: string): Promise<Session>;
  signOut(): Promise<void>;
  subscribe(cb: () => void): () => void;
}

const SESSION_KEY = "runguard.auth.session";
const LEGACY_KEYS = ["runguard.mockauth.users", "runguard.mockauth.session"];
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function clearLegacyMockAuth() {
  if (typeof window === "undefined") return;
  for (const key of LEGACY_KEYS) window.localStorage.removeItem(key);
}

function clearSession() {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(SESSION_KEY);
  emit();
}

function tokenIsCurrent(token: string, userId: string): boolean {
  // This is only a browser UX check. FastAPI verifies the JWT signature.
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return false;
    const base64 = parts[1]!.replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "="))) as {
      exp?: unknown;
      sub?: unknown;
    };
    return typeof payload.exp === "number" && payload.exp > Date.now() / 1000 && payload.sub === userId;
  } catch {
    return false;
  }
}

function readSession(): Session | null {
  if (typeof window === "undefined") return null;
  clearLegacyMockAuth();
  const raw = window.sessionStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<Session>;
    if (
      typeof value.access_token !== "string" ||
      typeof value.user?.id !== "string" ||
      typeof value.user.email !== "string" ||
      !tokenIsCurrent(value.access_token, value.user.id)
    ) {
      clearSession();
      return null;
    }
    return { access_token: value.access_token, user: value.user as Session["user"] };
  } catch {
    clearSession();
    return null;
  }
}

function storeSession(session: Session) {
  if (!tokenIsCurrent(session.access_token, session.user.id)) {
    throw new Error("The server returned an invalid or expired session. Please try again.");
  }
  window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  emit();
}

type AuthErrorBody = { error?: { code?: string; message?: string } };

async function requestSession(path: "/register" | "/login", email: string, password: string): Promise<Session> {
  clearLegacyMockAuth();
  clearSession();
  const configuredOrigin = import.meta.env["VITE_API_BASE_URL"]?.trim().replace(/\/$/, "") ?? "";
  let response: Response;
  try {
    response = await fetch(`${configuredOrigin}/api/v1/auth${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  } catch {
    throw new Error("Could not reach the RunGuard API. Please try again.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as AuthErrorBody | null;
    const message = body?.error?.message;
    if (response.status === 409 && body?.error?.code === "ACCOUNT_EXISTS") {
      throw new Error(message || "An account with this email already exists.");
    }
    if (response.status === 401 && body?.error?.code === "INVALID_CREDENTIALS") {
      throw new Error(message || "Invalid email or password.");
    }
    throw new Error(message || `Authentication failed (HTTP ${response.status}).`);
  }
  const body = await response.json().catch(() => null) as (Partial<Session> & { token_type?: string }) | null;
  if (
    body?.token_type !== "bearer" ||
    typeof body?.access_token !== "string" ||
    typeof body?.user?.id !== "string" ||
    typeof body?.user.email !== "string"
  ) {
    throw new Error("The server returned an invalid session. Please try again.");
  }
  const session: Session = { access_token: body.access_token, user: body.user as Session["user"] };
  storeSession(session);
  return session;
}

export const auth: AuthAdapter = {
  getSession: readSession,
  getAccessToken: () => readSession()?.access_token ?? null,
  signUp: (email, password) => requestSession("/register", email, password),
  signIn: (email, password) => requestSession("/login", email, password),
  async signOut() {
    clearLegacyMockAuth();
    clearSession();
  },
  subscribe(cb) {
    listeners.add(cb);
    return () => listeners.delete(cb);
  },
};

const AuthCtx = createContext<Session | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => {
    setSession(auth.getSession());
    return auth.subscribe(() => setSession(auth.getSession()));
  }, []);
  return <AuthCtx.Provider value={session}>{children}</AuthCtx.Provider>;
}

export const useSession = () => useContext(AuthCtx);
