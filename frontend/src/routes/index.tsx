import { createFileRoute, redirect } from "@tanstack/react-router";
import { auth } from "@/lib/auth";

export const Route = createFileRoute("/")({
  ssr: false,
  head: () => ({
    meta: [
      { title: "RunGuard — Run and observe background jobs" },
      { name: "description", content: "RunGuard runs website monitors and AI URL summaries on demand, with status, logs and manual retry." },
      { property: "og:title", content: "RunGuard — Run and observe background jobs" },
      { property: "og:description", content: "Run website monitors and AI URL summaries on demand, with logs and retry." },
    ],
  }),
  beforeLoad: () => {
    throw redirect({ to: auth.getSession() ? "/dashboard" : "/login" });
  },
});
