import { createFileRoute, Link, Outlet, redirect, useRouterState } from "@tanstack/react-router";
import { Activity, LayoutDashboard, LogOut, Plus, Boxes } from "lucide-react";
import { auth, useSession } from "@/lib/auth";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_app")({
  ssr: false,
  beforeLoad: () => {
    if (!auth.getSession()) throw redirect({ to: "/login" });
  },
  component: AppShell,
});

const nav = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/workers", label: "Workers", icon: Boxes },
] as const;

function AppShell() {
  const session = useSession();
  const path = useRouterState({ select: (s) => s.location.pathname });
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-row items-center gap-1 border-sidebar-border bg-sidebar px-3 py-2 text-sidebar-foreground md:w-56 md:flex-col md:items-stretch md:border-r md:px-3 md:py-5">
        <Link to="/dashboard" className="mr-3 flex items-center gap-2 px-2 font-mono text-sm font-semibold text-sidebar-accent-foreground md:mb-6 md:mr-0">
          <Activity className="size-4 text-sidebar-primary" /> runguard
        </Link>
        {nav.map(({ to, label, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              path.startsWith(to) && "bg-sidebar-accent text-sidebar-accent-foreground",
            )}
          >
            <Icon className="size-4" /> <span className="hidden sm:inline">{label}</span>
          </Link>
        ))}
        <Link to="/workers/new" className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-sidebar-primary hover:bg-sidebar-accent">
          <Plus className="size-4" /> <span className="hidden sm:inline">New worker</span>
        </Link>
        <div className="ml-auto md:ml-0 md:mt-auto md:border-t md:border-sidebar-border md:pt-4">
          <div className="hidden truncate px-2 pb-2 font-mono text-xs text-sidebar-foreground/70 md:block">{session?.user.email}</div>
          <Link to="/logout" className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent">
            <LogOut className="size-4" /> <span className="hidden sm:inline">Log out</span>
          </Link>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-10 md:py-8">
        <div className="mx-auto max-w-6xl">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
