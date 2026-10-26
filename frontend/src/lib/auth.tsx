// Auth adapter. Currently a local mock; swap for Supabase Auth by implementing
// the same AuthAdapter interface (signUp/signIn/signOut/getSession/getAccessToken).
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export interface Session {
  user: { id: string; email: string };
  access_token: string;
}

export interface AuthAdapter {
  getSession(): Session | null;
  signUp(email: string, password: string): Promise<{ needsConfirmation: boolean }>;
  signIn(email: string, password: string): Promise<Session>;
  signOut(): Promise<void>;
  subscribe(cb: () => void): () => void;
}

const USERS = "runguard.mockauth.users";
const SESSION = "runguard.mockauth.session";
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const readUsers = (): Record<string, { id: string; password: string }> => JSON.parse(localStorage.getItem(USERS) ?? "{}");

export const mockAuth: AuthAdapter = {
  getSession() {
    if (typeof window === "undefined") return null;
    const raw = localStorage.getItem(SESSION);
    return raw ? JSON.parse(raw) : null;
  },
  async signUp(email, password) {
    const users = readUsers();
    const key = email.trim().toLowerCase();
    if (users[key]) throw new Error("An account with this email already exists.");
    users[key] = { id: crypto.randomUUID(), password };
    localStorage.setItem(USERS, JSON.stringify(users));
    return { needsConfirmation: false };
  },
  async signIn(email, password) {
    const key = email.trim().toLowerCase();
    const u = readUsers()[key];
    if (!u || u.password !== password) throw new Error("Invalid email or password.");
    const session: Session = { user: { id: u.id, email: key }, access_token: `mock.${u.id}` };
    localStorage.setItem(SESSION, JSON.stringify(session));
    emit();
    return session;
  },
  async signOut() {
    localStorage.removeItem(SESSION);
    emit();
  },
  subscribe(cb) {
    listeners.add(cb);
    return () => listeners.delete(cb);
  },
};

export const auth: AuthAdapter = mockAuth;

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
