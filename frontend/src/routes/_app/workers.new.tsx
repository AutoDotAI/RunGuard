import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { PageHeader } from "@/components/app/bits";
import { WorkerForm } from "@/components/app/WorkerForm";
import { api } from "@/lib/api/client";

export const Route = createFileRoute("/_app/workers/new")({
  head: () => ({
    meta: [
      { title: "New worker — RunGuard" },
      { name: "description", content: "Create a Website Monitor or AI URL Summarizer configuration." },
      { property: "og:title", content: "New worker — RunGuard" },
      { property: "og:description", content: "Create a new worker configuration." },
    ],
  }),
  component: NewWorker,
});

function NewWorker() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  return (
    <>
      <PageHeader title="New worker" sub="Save a reusable job configuration" />
      <WorkerForm
        submitLabel="Create worker"
        onSubmit={async (v) => {
          const w = await api.createWorker(v);
          qc.invalidateQueries();
          toast.success("Worker created");
          navigate({ to: "/workers/$id", params: { id: w.id } });
        }}
      />
    </>
  );
}
