import { useState, type FormEvent } from "react";
import { Globe, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, type WorkerInput, type WorkerKind } from "@/lib/api/types";
import { validateName, validateUrl } from "@/lib/api/validation";
import { cn } from "@/lib/utils";

const kinds: { kind: WorkerKind; title: string; icon: typeof Globe; desc: string }[] = [
  { kind: "website_monitor", title: "Website Monitor", icon: Globe, desc: "HTTP check. Success = status 200–399. Timeout 10 s." },
  { kind: "url_summarizer", title: "AI URL Summarizer", icon: Sparkles, desc: "Concise neutral summary: heading + 3–6 bullets. Model set by server." },
];
const allowedHosts = import.meta.env["VITE_ALLOWED_HOSTS"]?.trim();
const summarizerAvailable = import.meta.env["VITE_SUMMARIZER_AVAILABLE"] !== "false";

export function WorkerForm({ initial, editing, onSubmit, submitLabel }: { initial?: WorkerInput; editing?: boolean; onSubmit: (v: WorkerInput) => Promise<void>; submitLabel: string }) {
  const [v, setV] = useState<WorkerInput>(initial ?? { name: "", kind: "website_monitor", url: "" });
  const [errs, setErrs] = useState<{ name?: string | null; url?: string | null; form?: string }>({});
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const name = validateName(v.name);
    const url = validateUrl(v.url);
    setErrs({ name, url });
    if (name || url) return;
    setBusy(true);
    try {
      await onSubmit(v);
    } catch (err) {
      if (err instanceof ApiError && err.details) setErrs({ name: err.details['name'] as string, url: err.details['url'] as string, form: err.message });
      else setErrs({ form: err instanceof Error ? err.message : "Request failed" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate className="max-w-2xl space-y-6">
      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium">Type {editing && <span className="font-normal text-muted-foreground">(cannot be changed)</span>}</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {kinds.map(({ kind, title, icon: Icon, desc }) => (
            <button
              type="button"
              key={kind}
              disabled={editing || (kind === "url_summarizer" && !summarizerAvailable)}
              aria-pressed={v.kind === kind}
              onClick={() => setV({ ...v, kind })}
              className={cn(
                "rounded-md border bg-card p-4 text-left transition-colors disabled:cursor-not-allowed",
                v.kind === kind ? "border-signal ring-2 ring-signal/30" : "hover:border-foreground/30 disabled:opacity-50",
              )}
            >
              <div className="flex items-center gap-2 font-medium"><Icon className="size-4" />{title}</div>
              <p className="mt-1 text-xs text-muted-foreground">{kind === "url_summarizer" && !summarizerAvailable ? "Live summaries are unavailable in this demo." : desc}</p>
            </button>
          ))}
        </div>
      </fieldset>
      <div className="space-y-1.5">
        <Label htmlFor="name">Name</Label>
        <Input id="name" maxLength={80} value={v.name} aria-invalid={!!errs.name} onChange={(e) => setV({ ...v, name: e.target.value })} placeholder="Docs uptime check" />
        {errs.name && <p className="text-sm text-destructive">{errs.name}</p>}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="url">URL</Label>
        <Input id="url" className="font-mono" value={v.url} aria-invalid={!!errs.url} onChange={(e) => setV({ ...v, url: e.target.value })} placeholder="https://www.python.org/" />
        {errs.url ? <p className="text-sm text-destructive">{errs.url}</p> : <p className="text-xs text-muted-foreground">{allowedHosts ? `This demo accepts only these public hosts: ${allowedHosts}.` : "Public http(s) address only."} {editing && "Edits apply to future runs only."}</p>}
      </div>
      {errs.form && !errs.name && !errs.url && <p role="alert" className="text-sm text-destructive">{errs.form}</p>}
      <Button type="submit" disabled={busy}>{busy ? "Saving…" : submitLabel}</Button>
    </form>
  );
}
