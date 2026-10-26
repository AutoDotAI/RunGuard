import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { AuthForm } from "@/components/app/AuthForm";
import { auth } from "@/lib/auth";

export const Route = createFileRoute("/signup")({
  head: () => ({
    meta: [
      { title: "Create account — RunGuard" },
      { name: "description", content: "Create a RunGuard account to execute and observe background jobs." },
      { property: "og:title", content: "Create account — RunGuard" },
      { property: "og:description", content: "Create a RunGuard account to execute and observe background jobs." },
    ],
  }),
  component: Signup,
});

function Signup() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return (
    <AuthForm
      mode="signup"
      onSubmit={async (e, p) => {
        await auth.signUp(e, p);
        queryClient.clear();
        navigate({ to: "/dashboard" });
      }}
    />
  );
}
