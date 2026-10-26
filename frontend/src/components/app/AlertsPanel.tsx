import { Link } from "@tanstack/react-router";
import type { AlertPage } from "@/lib/api/types";
import { fmtTime } from "./bits";

export function AlertsPanel({ data, loading, error }: {
  data: AlertPage | undefined;
  loading: boolean;
  error?: Error | null;
}) {
  const open = data?.items.filter((alert) => alert.status === "open") ?? [];
  const resolved = data?.items.filter((alert) => alert.status === "resolved") ?? [];
  return (
    <section aria-labelledby="alerts-heading" className="mb-6 rounded-md border bg-card p-4">
      <h2 id="alerts-heading" className="text-sm font-semibold uppercase tracking-wide">User-impact alerts</h2>
      {import.meta.env["VITE_DEMO_MODE"] === "true" && (
        <p className="mt-2 text-xs font-semibold text-muted-foreground">Local demo · injected worker pause</p>
      )}
      {loading && <p role="status" className="mt-2 text-sm text-muted-foreground">Loading alerts…</p>}
      {error && <p role="alert" className="mt-2 text-sm text-destructive">Alerts unavailable. Try refreshing the page.</p>}
      {!loading && !error && open.length === 0 && <p className="mt-2 text-sm text-muted-foreground">No open alerts.</p>}
      {!error && open.map((alert) => (
        <article key={alert.id} role="alert" className="mt-3 rounded-md border border-destructive/30 bg-danger-soft p-3 text-sm">
          <div className="font-semibold text-destructive">{alert.title}</div>
          <p className="mt-1">{alert.symptom}</p>
          <p className="mt-1 text-xs text-muted-foreground">Opened {fmtTime(alert.created_at)}</p>
          <Link to="/runs/$id" params={{ id: alert.run_id }} className="mt-2 inline-block font-medium underline">
            Inspect run and timestamped logs
          </Link>
          {alert.trace_id && <p className="mt-1 break-all font-mono text-xs">Trace: {alert.trace_id}</p>}
        </article>
      ))}
      {!error && resolved.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer">Resolved alerts ({resolved.length})</summary>
          <ul className="mt-2 space-y-2">
            {resolved.map((alert) => (
              <li key={alert.id} className="rounded-md border p-2">
                {alert.title} · resolved {alert.resolved_at ? fmtTime(alert.resolved_at) : "unknown"} ·{" "}
                <Link to="/runs/$id" params={{ id: alert.run_id }} className="underline">Inspect run</Link>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
