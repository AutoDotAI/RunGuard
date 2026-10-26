import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ErrorState, PageHeader } from "@/components/app/bits";
import { WorkerForm } from "@/components/app/WorkerForm";
import { api } from "@/lib/api/client";

export const Route = createFileRoute("/_app/workers/$id/edit")({
  head: () => ({
    meta: [
      { title: "Edit worker — RunGuard" },
      { name: "description", content: "Update a worker's name or URL for future runs." },
      { property: "og:title", content: "Edit worker — RunGuard" },
      { property: "og:description", content: "Update a worker configuration." },
    ],
  }),
  component: EditWorker,
});

function EditWorker() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["worker", id], queryFn: () => api.getWorker(id) });
  return (
    <>
      <PageHeader title="Edit worker" sub={q.error ? undefined : q.data?.name} />
      {q.error && <ErrorState error={q.error} />}
      {q.isPending && <p role="status" className="text-sm text-muted-foreground">Loading worker…</p>}
      {q.data && !q.error && (
        <>
        <p className="mb-4 text-sm text-muted-foreground">Changes affect future runs only. Past runs keep their saved configuration.</p>
        <WorkerForm
          editing
          initial={{ name: q.data.name, kind: q.data.kind, url: q.data.url }}
          submitLabel="Save changes"
          onSubmit={async (v) => {
            await api.updateWorker(id, { name: v.name, url: v.url });
            qc.invalidateQueries();
            toast.success("Saved — applies to future runs");
            navigate({ to: "/workers/$id", params: { id } });
          }}
        />
        </>
      )}
    </>
  );
}
