"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { LayoutDashboard, ScrollText, ShieldCheck, Users } from "lucide-react";
import { api, useFetch } from "@/lib/api";
import type { Me } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { MeProvider } from "@/components/me-context";

const NAV = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard, permission: null },
  { href: "/team", label: "Team", icon: Users, permission: "users:read" },
  { href: "/audit", label: "Audit log", icon: ScrollText, permission: "audit:read" },
  { href: "/security", label: "Security", icon: ShieldCheck, permission: null },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { data: me, error, loading } = useFetch<Me>("auth/me");
  const pathname = usePathname();
  const router = useRouter();
  const [leaving, setLeaving] = useState(false);

  async function signOut() {
    setLeaving(true);
    try {
      await api("auth/logout", { method: "POST" });
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  if (loading) return <div className="p-8 text-sm text-muted-foreground">Loading your workspace…</div>;
  if (error || !me) return <div className="p-8 text-sm text-destructive">{error?.message ?? "Could not load your account."}</div>;

  return (
    <MeProvider value={me}>
      <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]">
        <aside className="border-b bg-card md:min-h-screen md:border-b-0 md:border-r">
          <div className="flex items-center justify-between gap-3 px-5 py-4 md:block">
            <div>
              <p className="text-sm font-semibold">Sourcing Copilot</p>
              <p className="truncate text-xs text-muted-foreground">{me.organization_name}</p>
            </div>
          </div>
          <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:pb-0">
            {NAV.filter((n) => !n.permission || me.permissions.includes(n.permission)).map((n) => {
              const active = pathname.startsWith(n.href);
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-2 rounded-md px-3 py-2 text-sm",
                    active ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-secondary",
                  )}
                >
                  <n.icon className="h-4 w-4" aria-hidden />
                  {n.label}
                </Link>
              );
            })}
          </nav>
        </aside>
        <div className="min-w-0">
          <header className="flex items-center justify-end gap-3 border-b bg-card px-6 py-3">
            <div className="text-right">
              <p className="text-sm font-medium leading-tight">{me.full_name}</p>
              <p className="text-xs text-muted-foreground">{me.email}</p>
            </div>
            <Badge variant="default">{me.role}</Badge>
            <Button variant="outline" size="sm" onClick={signOut} disabled={leaving}>
              Sign out
            </Button>
          </header>
          <main className="mx-auto max-w-5xl p-6">{children}</main>
        </div>
      </div>
    </MeProvider>
  );
}
