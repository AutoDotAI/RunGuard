import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorState, PageHeader, fmtDuration, isActive } from "@/components/app/bits";
import { RunsTable } from "@/components/app/RunsTable";
import { api } from "@/lib/api/client";

export const Route = createFileRoute("/_app/dashboard")({
  head: () => ({
    meta: [
      { title: "Dashboard — RunGuard" },
      { name: "description", content: "Execution health, success rate and recent runs for your RunGuard workers." },
      { property: "og:title", content: "Dashboard — RunGuard" },
      { property: "og:description", content: "Execution health and recent runs." },
    ],
  }),
  component: Dashboard,
});

function Stat({ label, value, hint, tone }: { label: string; value: string; hint: string; tone?: "success" | "danger" | undefined }) {
  return (
    <div className="rounded-md border bg-card p-4">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`mt-2 font-mono text-3xl font-semibold ${tone === "success" ? "text-success" : tone === "danger" ? "text-destructive" : ""}`}>{value}</div>
      <div className="mt-1 text-xs text-muted-foreground">{hint}</div>
    </div>
  );
}

function Dashboard() {
  const q = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.getDashboardSummary(),
    refetchInterval: (query) => (query.state.data?.recent_runs.some((r) => isActive(r.status)) ? 2500 : false),
  });
  const d = q.data;
  return (
    <>
      <PageHeader
        title="Dashboard"
        sub="Execution health across your workers"
        actions={
          <>
            <Button variant="outline" asChild><Link to="/workers">View workers</Link></Button>
            <Button asChild><Link to="/workers/new"><Plus /> New worker</Link></Button>
          </>
        }
      />
      {q.error && <ErrorState error={q.error} />}
      {d && d.failed > 0 && (
        <div role="status" className="mb-6 flex items-center gap-2 rounded-md border border-destructive/30 bg-danger-soft px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="size-4" /> {d.failed} failed run{d.failed > 1 ? "s" : ""} in the last 24 hours. Open a failed run to inspect logs and retry.
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Executions" value={d ? String(d.total) : "…"} hint="Last 24 hours" />
        <Stat label="Succeeded" value={d ? String(d.succeeded) : "…"} hint="Last 24 hours" tone="success" />
        <Stat label="Failed" value={d ? String(d.failed) : "…"} hint="Last 24 hours" tone={d?.failed ? "danger" : undefined} />
        <Stat label="Success rate" value={d ? (d.success_rate == null ? "N/A" : `${Math.round(d.success_rate * 100)}%`) : "…"} hint="Completed runs, 24h" />
        <Stat label="Avg duration" value={d ? (d.avg_duration_ms == null ? "N/A" : fmtDuration(d.avg_duration_ms)) : "…"} hint="Completed runs, 24h" />
      </div>
      <h2 className="mb-3 mt-8 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Recent executions</h2>
      {d ? <RunsTable runs={d.recent_runs} empty="No runs yet. Create a worker and press Run." /> : !q.error && <div className="h-32 animate-pulse rounded-md bg-muted" />}
    </>
  );
}
