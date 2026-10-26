import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { AuthForm } from "@/components/app/AuthForm";
import { auth } from "@/lib/auth";

export const Route = createFileRoute("/login")({
  head: () => ({
    meta: [
      { title: "Sign in — RunGuard" },
      { name: "description", content: "Sign in to RunGuard to run and inspect your website monitors and URL summarizers." },
      { property: "og:title", content: "Sign in — RunGuard" },
      { property: "og:description", content: "Sign in to RunGuard to run and inspect your jobs." },
    ],
  }),
  component: Login,
});

function Login() {
  const navigate = useNavigate();
  return (
    <AuthForm
      mode="login"
      onSubmit={async (e, p) => {
        await auth.signIn(e, p);
        navigate({ to: "/dashboard" });
      }}
    />
  );
}
