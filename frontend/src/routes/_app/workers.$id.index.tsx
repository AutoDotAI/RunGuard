import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Pencil, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorState, KindLabel, PageHeader, fmtTime, isActive } from "@/components/app/bits";
import { RunsTable } from "@/components/app/RunsTable";
import { api } from "@/lib/api/client";
import { useRunWorker } from "@/lib/api/useRunWorker";
import { ArchiveButton } from "./workers.index";

export const Route = createFileRoute("/_app/workers/$id/")({
  head: () => ({
    meta: [
      { title: "Worker details — RunGuard" },
      { name: "description", content: "Worker configuration and execution history." },
      { property: "og:title", content: "Worker details — RunGuard" },
      { property: "og:description", content: "Worker configuration and execution history." },
    ],
  }),
  component: WorkerDetail,
});

const PAGE = 10;

function WorkerDetail() {
  const { id } = Route.useParams();
  return <WorkerDetailView id={id} />;
}

export function WorkerDetailView({ id }: { id: string }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const w = useQuery({
    queryKey: ["worker", id],
    queryFn: () => api.getWorker(id),
    refetchInterval: (q) => (q.state.data?.last_run && isActive(q.state.data.last_run.status) ? 2500 : false),
  });
  const runs = useQuery({
    queryKey: ["runs", id, page],
    queryFn: () => api.listRuns({ worker_id: id, page, page_size: PAGE }),
    refetchInterval: (q) => (q.state.data?.items.some((r) => isActive(r.status)) ? 2500 : false),
  });
  const run = useRunWorker();
  const visibleLatest = runs.error ? undefined : runs.data?.items.find((r) => r.id === w.data?.last_run?.id);
  const active = (visibleLatest ? isActive(visibleLatest.status) : !!w.data?.last_run && isActive(w.data.last_run.status)) ||
    (!runs.error && (runs.data?.items.some((r) => isActive(r.status)) ?? false));
  const pages = runs.data && !runs.error ? Math.max(1, Math.ceil(runs.data.total / PAGE)) : 1;

  if (w.error) return <ErrorState error={w.error} />;
  const d = w.data;
  return (
    <>
      <PageHeader
        title={d?.name ?? "…"}
        sub={d && <KindLabel kind={d.kind} />}
        actions={
          d && (
            <>
              <ArchiveButton worker={d} onDone={() => navigate({ to: "/workers" })} />
              <Button variant="outline" asChild><Link to="/workers/$id/edit" params={{ id }}><Pencil /> Edit</Link></Button>
              <Button disabled={active || run.isPending} onClick={() => run.mutate(id)}><Play /> {active ? "Run in progress" : "Run now"}</Button>
            </>
          )
        }
      />
      {d && (
        <dl className="mb-8 grid gap-4 rounded-md border bg-card p-4 text-sm sm:grid-cols-3">
          <div className="sm:col-span-3"><dt className="text-xs uppercase text-muted-foreground">URL</dt><dd className="mt-1 break-all font-mono">{d.url}</dd></div>
          <div><dt className="text-xs uppercase text-muted-foreground">Created</dt><dd className="mt-1 font-mono text-xs">{fmtTime(d.created_at)}</dd></div>
          <div><dt className="text-xs uppercase text-muted-foreground">Updated</dt><dd className="mt-1 font-mono text-xs">{fmtTime(d.updated_at)}</dd></div>
          <div><dt className="text-xs uppercase text-muted-foreground">ID</dt><dd className="mt-1 font-mono text-xs">{d.id}</dd></div>
        </dl>
      )}
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Execution history</h2>
      {runs.isPending && <p role="status" className="text-sm text-muted-foreground">Loading execution history…</p>}
      {runs.error && <ErrorState error={runs.error} />}
      {runs.data && !runs.error && <RunsTable runs={runs.data.items} showWorker={false} empty="This worker hasn't run yet. Run it now to see its history." />}
      {!runs.error && pages > 1 && (
        <div className="mt-3 flex items-center justify-end gap-2 text-sm">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
          <span className="font-mono text-xs text-muted-foreground">{page} / {pages}</span>
          <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
        </div>
      )}
    </>
  );
}
