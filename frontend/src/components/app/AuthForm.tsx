import { Link } from "@tanstack/react-router";
import { Activity } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function AuthForm({ mode, onSubmit }: { mode: "login" | "signup"; onSubmit: (email: string, password: string) => Promise<void> }) {
  const [ready, setReady] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setReady(true), []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!/^\S+@\S+\.\S+$/.test(email)) return setError("Enter a valid email address.");
    if (password.length < 8) return setError("Password must be at least 8 characters.");
    setBusy(true);
    try {
      await onSubmit(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const isLogin = mode === "login";
  // Do not expose a submit button in SSR HTML before React can handle it.
  // Otherwise a quick browser interaction can submit the empty native form.
  if (!ready) return <p role="status" className="grid min-h-screen place-items-center text-sm text-muted-foreground">Loading account form…</p>;
  return (
    <div className="grid min-h-screen place-items-center bg-sidebar px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2 font-mono text-sm font-semibold text-sidebar-accent-foreground">
          <Activity className="size-4 text-sidebar-primary" /> runguard
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-lg border bg-card p-6 shadow-lg" noValidate>
          <div>
            <h1 className="text-xl font-semibold">{isLogin ? "Sign in" : "Create account"}</h1>
            <p className="mt-1 text-sm text-muted-foreground">Run, inspect and retry your background jobs.</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete={isLogin ? "current-password" : "new-password"} value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Please wait…" : isLogin ? "Sign in" : "Create account"}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            {isLogin ? "No account? " : "Already registered? "}
            <Link to={isLogin ? "/signup" : "/login"} className="font-medium text-foreground underline underline-offset-4">
              {isLogin ? "Sign up" : "Sign in"}
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
