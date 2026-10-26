import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { auth } from "@/lib/auth";

export const Route = createFileRoute("/logout")({
  ssr: false,
  head: () => ({ meta: [{ title: "Signing out — RunGuard" }, { name: "description", content: "Signing out of RunGuard." }, { property: "og:title", content: "RunGuard" }, { property: "og:description", content: "Signing out of RunGuard." }] }),
  component: Logout,
});

function Logout() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  useEffect(() => {
    auth.signOut().then(() => {
      qc.clear();
      navigate({ to: "/login", replace: true });
    });
  }, [navigate, qc]);
  return <p className="p-8 text-sm text-muted-foreground">Signing out…</p>;
}
