import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { api } from "./client";
import { ApiError } from "./types";

export function useRunWorker() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: (workerId: string) => api.runWorker(workerId),
    onSuccess: (res) => {
      qc.invalidateQueries();
      toast.success("Run queued");
      navigate({ to: "/runs/$id", params: { id: res.run_id } });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === "RUN_ALREADY_ACTIVE" && typeof err.details?.['run_id'] === "string") {
        const id = err.details['run_id'] as string;
        toast.error(err.message, { action: { label: "Open run", onClick: () => navigate({ to: "/runs/$id", params: { id } }) } });
      } else toast.error(err instanceof Error ? err.message : "Could not start run");
    },
  });
}
