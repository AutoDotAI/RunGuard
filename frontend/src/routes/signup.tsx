import { createFileRoute, useNavigate } from "@tanstack/react-router";
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
  return (
    <AuthForm
      mode="signup"
      onSubmit={async (e, p) => {
        await auth.signUp(e, p);
        await auth.signIn(e, p);
        navigate({ to: "/dashboard" });
      }}
    />
  );
}
