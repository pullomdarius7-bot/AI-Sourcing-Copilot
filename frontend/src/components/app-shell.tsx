"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Activity, Building2, ClipboardCheck, FileText, LayoutDashboard, ListChecks, MessageSquare, Scale, ScrollText, Send, ShieldCheck, Upload, Users } from "lucide-react";
import { api, useFetch } from "@/lib/api";
import type { Me } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { MeProvider } from "@/components/me-context";

const NAV = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard, permission: null },
  { href: "/assistant", label: "Assistant", icon: MessageSquare, permission: "agent:use" },
  { href: "/documents", label: "Documents", icon: FileText, permission: "documents:read" },
  { href: "/upload", label: "Upload", icon: Upload, permission: "documents:upload" },
  { href: "/requirements", label: "Requirements", icon: ListChecks, permission: "requirements:read" },
  { href: "/review", label: "Review queue", icon: ClipboardCheck, permission: "requirements:review" },
  { href: "/suppliers", label: "Suppliers", icon: Building2, permission: "suppliers:read" },
  { href: "/rfqs", label: "RFQs", icon: Send, permission: "rfqs:read" },
  { href: "/reference", label: "Rates & units", icon: Scale, permission: "comparisons:read" },
  { href: "/team", label: "Team", icon: Users, permission: "users:read" },
  { href: "/jobs", label: "Jobs", icon: Activity, permission: "jobs:read" },
  { href: "/audit", label: "Audit log", icon: ScrollText, permission: "audit:read" },
  { href: "/security", label: "Security", icon: ShieldCheck, permission: null },
];

function TrialBadge({ balance, endsAt }: { balance: number; endsAt: string }) {
  const days = Math.ceil((new Date(endsAt).getTime() - Date.now()) / 86_400_000);
  if (days <= 0 || balance <= 0) return <Badge variant="caution" className="mr-auto">Free trial ended</Badge>;
  return (
    <Badge variant="default" className="mr-auto">
      Free trial: {balance} {balance === 1 ? "analysis" : "analyses"} left · {days} {days === 1 ? "day" : "days"}
    </Badge>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { data: me, error, loading } = useFetch<Me>("auth/me");
  const pathname = usePathname();
  const router = useRouter();
  const [leaving, setLeaving] = useState(false);
  const needsOnboarding = me !== null && !me.onboarding_completed;

  useEffect(() => {
    if (needsOnboarding) router.replace("/onboarding");
  }, [needsOnboarding, router]);

  async function signOut() {
    setLeaving(true);
    try {
      await api("auth/logout", { method: "POST" });
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  if (loading || needsOnboarding) return <div className="p-8 text-sm text-muted-foreground">Loading your workspace…</div>;
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
            {NAV.filter((n) => (!n.permission || me.permissions.includes(n.permission)) && !(n.href === "/team" && me.account_type === "solo_buyer")).map((n) => {
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
            {me.trial_ends_at && <TrialBadge balance={me.credit_balance} endsAt={me.trial_ends_at} />}
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
