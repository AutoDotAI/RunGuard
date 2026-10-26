import { Globe, Sparkles } from "lucide-react";
import type { RunStatus, WorkerKind } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const statusStyle: Record<RunStatus, string> = {
  queued: "bg-muted text-muted-foreground border-border",
  running: "bg-info-soft text-info border-info/30",
  succeeded: "bg-success-soft text-success border-success/30",
  failed: "bg-danger-soft text-destructive border-destructive/30",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 font-mono text-xs", statusStyle[status])}>
      <span className={cn("size-1.5 rounded-full bg-current", (status === "running" || status === "queued") && "pulse-dot")} />
      {status}
    </span>
  );
}

export function KindLabel({ kind }: { kind: WorkerKind }) {
  const Icon = kind === "website_monitor" ? Globe : Sparkles;
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <Icon className="size-3.5" />
      {kind === "website_monitor" ? "Website Monitor" : "URL Summarizer"}
    </span>
  );
}

export const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
export const fmtDuration = (ms: number | null) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`);
export const shortId = (id: string) => id.slice(0, 8);
export const isActive = (s: RunStatus) => s === "queued" || s === "running";

export function PageHeader({ title, sub, actions }: { title: string; sub?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b pb-5">
      <div className="min-w-0">
        <h1 className="truncate text-2xl font-semibold tracking-tight">{title}</h1>
        {sub && <div className="mt-1 text-sm text-muted-foreground">{sub}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/30 bg-danger-soft p-4 text-sm text-destructive">
      {error instanceof Error ? error.message : "Something went wrong."}
    </div>
  );
}
