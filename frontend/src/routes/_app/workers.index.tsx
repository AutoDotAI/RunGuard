import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { ErrorState, KindLabel, PageHeader, StatusBadge, fmtTime, isActive } from "@/components/app/bits";
import { api } from "@/lib/api/client";
import { useRunWorker } from "@/lib/api/useRunWorker";
import type { Worker } from "@/lib/api/types";

export const Route = createFileRoute("/_app/workers/")({
  head: () => ({
    meta: [
      { title: "Workers — RunGuard" },
      { name: "description", content: "Your saved Website Monitor and AI URL Summarizer configurations." },
      { property: "og:title", content: "Workers — RunGuard" },
      { property: "og:description", content: "Your saved worker configurations." },
    ],
  }),
  component: Workers,
});

export function ArchiveButton({ worker, onDone }: { worker: Worker; onDone?: () => void }) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => api.archiveWorker(worker.id),
    onSuccess: () => {
      qc.invalidateQueries();
      toast.success("Worker archived");
      onDone?.();
    },
    onError: (e) => toast.error(e.message),
  });
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="sm" className="text-destructive" disabled={m.isPending}>Archive</Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Archive “{worker.name}”?</AlertDialogTitle>
          <AlertDialogDescription>It disappears from your list and can no longer be run. Past runs and logs stay inspectable.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={() => m.mutate()}>Archive</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function Workers() {
  const q = useQuery({
    queryKey: ["workers"],
    queryFn: () => api.listWorkers(),
    refetchInterval: (query) => (query.state.data?.items.some((w) => w.last_run && isActive(w.last_run.status)) ? 2500 : false),
  });
  const run = useRunWorker();
  const data = q.error ? undefined : q.data;
  return (
    <>
      <PageHeader title="Workers" sub={data ? `${data.total} of 20 saved` : undefined} actions={<Button asChild><Link to="/workers/new"><Plus /> New worker</Link></Button>} />
      {q.error && <ErrorState error={q.error} />}
      {q.isLoading && <div className="h-40 animate-pulse rounded-md bg-muted" />}
      {data && data.items.length === 0 && (
        <div className="rounded-md border border-dashed p-12 text-center">
          <p className="text-sm text-muted-foreground">No workers yet.</p>
          <Button asChild className="mt-4"><Link to="/workers/new"><Plus /> Create your first worker</Link></Button>
        </div>
      )}
      <div className="grid gap-3">
        {data?.items.map((w) => (
          <div key={w.id} className="flex flex-wrap items-center gap-4 rounded-md border bg-card p-4">
            <div className="min-w-0 flex-1">
              <Link to="/workers/$id" params={{ id: w.id }} className="font-medium hover:underline">{w.name}</Link>
              <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
                <KindLabel kind={w.kind} />
                <span className="truncate font-mono text-xs text-muted-foreground">{w.url}</span>
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {w.last_run ? <><StatusBadge status={w.last_run.status} /><span className="font-mono">{fmtTime(w.last_run.created_at)}</span></> : "Never run"}
            </div>
            <div className="flex items-center gap-1">
              <Button size="sm" disabled={run.isPending || (!!w.last_run && isActive(w.last_run.status))} onClick={() => run.mutate(w.id)}><Play /> Run</Button>
              <Button variant="ghost" size="sm" asChild><Link to="/workers/$id" params={{ id: w.id }}>Details</Link></Button>
              <Button variant="ghost" size="sm" asChild><Link to="/workers/$id/edit" params={{ id: w.id }}>Edit</Link></Button>
              <ArchiveButton worker={w} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
