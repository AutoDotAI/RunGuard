import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ErrorState, KindLabel, PageHeader, StatusBadge, fmtDuration, fmtTime, isActive } from "@/components/app/bits";
import { api } from "@/lib/api/client";
import type { MonitorResult, Run, RunLog, SummarizerResult } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_app/runs/$id")({
  head: () => ({
    meta: [
      { title: "Run details — RunGuard" },
      { name: "description", content: "Status, timestamped logs, results and errors for a single run." },
      { property: "og:title", content: "Run details — RunGuard" },
      { property: "og:description", content: "Status, logs and results for a run." },
    ],
  }),
  component: RunDetail,
});

const levelTone = { INFO: "text-terminal-foreground/70", WARN: "text-warning", ERROR: "text-destructive" } as const;

function useRunLogs(id: string, active: boolean) {
  const [logs, setLogs] = useState<RunLog[]>([]);
  const after = useRef(0);
  useEffect(() => {
    setLogs([]);
    after.current = 0;
  }, [id]);
  const q = useQuery({
    queryKey: ["logs", id],
    queryFn: async () => {
      const res = await api.getRunLogs(id, after.current);
      if (res.items.length) {
        after.current = res.next_after_id;
        setLogs((prev) => [...prev, ...res.items.filter((l) => !prev.some((p) => p.id === l.id))]);
      }
      return res.next_after_id;
    },
    refetchInterval: (query) => active ? Math.min(2500 * 2 ** query.state.fetchFailureCount, 15000) : false,
    retry: 2,
    retryDelay: (n) => Math.min(2000 * 2 ** n, 15000),
  });
  return { logs, error: q.error };
}

function RunDetail() {
  const { id } = Route.useParams();
  return <RunDetailView id={id} />;
}

export function RunDetailView({ id }: { id: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["run", id],
    queryFn: () => api.getRun(id),
    refetchInterval: (query) => (query.state.data && !isActive(query.state.data.status) ? false : Math.min(2500 * 2 ** query.state.fetchFailureCount, 15000)),
    retry: 2,
    retryDelay: (n) => Math.min(2000 * 2 ** n, 15000),
  });
  const r = q.error ? undefined : q.data;
  const active = !r || isActive(r.status);
  const { logs, error: logsError } = useRunLogs(id, active);
  // one final logs fetch once terminal
  useEffect(() => {
    if (r && !isActive(r.status)) qc.invalidateQueries({ queryKey: ["logs", id] });
  }, [r?.status, id, qc]);

  const retry = useMutation({
    mutationFn: () => api.retryRun(id),
    onSuccess: (res) => {
      qc.invalidateQueries();
      toast.success("Retry queued");
      navigate({ to: "/runs/$id", params: { id: res.run_id } });
    },
    onError: (e) => toast.error(e.message),
  });

  if (q.error) return <ErrorState error={q.error} />;
  if (!r) return <p role="status" className="text-sm text-muted-foreground">Loading run details…</p>;

  return (
    <>
      <PageHeader
        title={r.worker_name}
        sub={<span className="flex flex-wrap items-center gap-3"><KindLabel kind={r.kind} /><span className="font-mono text-xs">run {r.id}</span></span>}
        actions={
          <>
            <StatusBadge status={r.status} />
            {r.status === "failed" && <Button onClick={() => retry.mutate()} disabled={retry.isPending}><RotateCcw /> {retry.isPending ? "Retrying…" : "Retry"}</Button>}
          </>
        }
      />
      {retry.error && <div className="mb-4"><ErrorState error={retry.error} /></div>}
      <div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]">
        <div className="space-y-6">
          <Meta r={r} />
          {r.error && (
            <section className="rounded-md border border-destructive/30 bg-danger-soft p-4">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-destructive">Failure</h2>
              <p className="mt-2 font-mono text-sm text-destructive">{r.error.code}</p>
              <p className="mt-1 text-sm">{r.error.message}</p>
            </section>
          )}
          {r.result && <Result r={r} />}
        </div>
        <section className="overflow-hidden rounded-md border bg-terminal text-terminal-foreground">
          <div className="flex items-center justify-between border-b border-sidebar-border px-4 py-2 text-xs">
            <span className="font-mono uppercase tracking-wide">Step logs</span>
            {active && <span className="flex items-center gap-1.5 font-mono text-info"><span className="pulse-dot size-1.5 rounded-full bg-current" />polling</span>}
          </div>
          {logsError && <div className="m-3"><ErrorState error={logsError} /></div>}
          <ol className="max-h-[32rem] overflow-auto p-3 font-mono text-xs leading-relaxed">
            {logs.length === 0 && !logsError && <li className="text-terminal-foreground/50">Waiting for logs…</li>}
            {logs.map((l) => (
              <li key={l.id} className="grid grid-cols-[auto_auto_1fr] gap-x-3 py-0.5">
                <span className="text-terminal-foreground/50">{l.timestamp}</span>
                <span className={cn("w-10", levelTone[l.level])}>{l.level}</span>
                <span><span className="text-signal">{l.event}</span> {l.message}</span>
              </li>
            ))}
          </ol>
        </section>
      </div>
    </>
  );
}

function Meta({ r }: { r: Run }) {
  const rows: [string, React.ReactNode][] = [
    ["Worker", <span>{r.worker_name}</span>],
    ["URL used", <span className="break-all">{r.url}</span>],
    ["Created", fmtTime(r.created_at)],
    ["Started", fmtTime(r.started_at)],
    ["Finished", fmtTime(r.finished_at)],
    ["Elapsed", fmtDuration(r.duration_ms)],
  ];
  if (r.retry_of_run_id) rows.push(["Retry of", <Link to="/runs/$id" params={{ id: r.retry_of_run_id }} className="underline-offset-4 hover:underline">{r.retry_of_run_id.slice(0, 8)}</Link>]);
  return (
    <dl className="divide-y rounded-md border bg-card text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[7rem_1fr] gap-2 px-4 py-2">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 font-mono text-xs leading-5">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Result({ r }: { r: Run }) {
  if (r.kind === "website_monitor") {
    const m = r.result as MonitorResult;
    return (
      <section className="rounded-md border bg-card p-4">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Result</h2>
        <div className="mt-3 grid grid-cols-3 gap-3 font-mono">
          <div><div className="text-xs text-muted-foreground">HTTP</div><div className={cn("text-2xl font-semibold", m.healthy ? "text-success" : "text-destructive")}>{m.http_status}</div></div>
          <div><div className="text-xs text-muted-foreground">Response</div><div className="text-2xl font-semibold">{m.response_time_ms}<span className="text-sm"> ms</span></div></div>
          <div><div className="text-xs text-muted-foreground">Conclusion</div><div className="text-sm font-semibold leading-8">{m.healthy ? "Healthy" : "Unhealthy"}</div></div>
        </div>
      </section>
    );
  }
  const s = r.result as SummarizerResult;
  const lines = s.summary.split("\n");
  return (
    <section className="rounded-md border bg-card p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Summary</h2>
      {s.page_title && <p className="mt-2 text-sm text-muted-foreground">{s.page_title}</p>}
      <div className="mt-3 space-y-1 text-sm">
        {lines.map((l, i) =>
          l.startsWith("## ") ? <h3 key={i} className="text-base font-semibold">{l.slice(3)}</h3> : l.startsWith("- ") ? <p key={i} className="pl-4 before:-ml-3 before:mr-2 before:content-['•']">{l.slice(2)}</p> : <p key={i}>{l}</p>,
        )}
      </div>
      <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t pt-3 font-mono text-xs text-muted-foreground">
        <span>model: {s.model}</span>
        <a href={s.source_url} target="_blank" rel="noreferrer" className="break-all underline-offset-4 hover:underline">source: {s.source_url}</a>
        {s.input_was_truncated && <span className="text-warning">input truncated</span>}
      </div>
    </section>
  );
}
