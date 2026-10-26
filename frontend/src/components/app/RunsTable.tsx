import { Link } from "@tanstack/react-router";
import type { Run } from "@/lib/api/types";
import { KindLabel, StatusBadge, fmtDuration, fmtTime, shortId } from "./bits";

export function RunsTable({ runs, showWorker = true, empty }: { runs: Run[]; showWorker?: boolean; empty: string }) {
  if (!runs.length) return <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">{empty}</div>;
  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <table className="w-full text-sm">
        <thead className="border-b bg-muted/60 text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Run</th>
            {showWorker && <th className="px-3 py-2 font-medium">Worker</th>}
            {showWorker && <th className="px-3 py-2 font-medium">Type</th>}
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Started</th>
            <th className="px-3 py-2 font-medium">Duration</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-b last:border-0 hover:bg-muted/40">
              <td className="px-3 py-2 font-mono text-xs">
                {shortId(r.id)}
                {r.retry_of_run_id && <span className="ml-1.5 text-muted-foreground">↻</span>}
              </td>
              {showWorker && <td className="max-w-48 truncate px-3 py-2 font-medium">{r.worker_name}</td>}
              {showWorker && <td className="px-3 py-2"><KindLabel kind={r.kind} /></td>}
              <td className="px-3 py-2"><StatusBadge status={r.status} /></td>
              <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted-foreground">{fmtTime(r.started_at ?? r.created_at)}</td>
              <td className="px-3 py-2 font-mono text-xs">{fmtDuration(r.duration_ms)}</td>
              <td className="px-3 py-2 text-right">
                <Link to="/runs/$id" params={{ id: r.id }} className="text-sm font-medium underline-offset-4 hover:underline">
                  View details
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
