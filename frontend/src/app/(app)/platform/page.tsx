"use client";

import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";

type Check = { name: string; status: "ok" | "warn" | "fail"; detail: string };
type Health = { status: Check["status"]; checked_at: string; checks: Check[] };
type Overview = Record<string, number | Record<string, number>>;
type Org = {
  id: string; name: string; account_type: string; plan: string | null; subscription_status: string; credit_balance: number;
  trial_ends_at: string | null; created_at: string; users?: number; signup_promo: string | null; stripe_customer_id: string | null;
  billing_exempt?: boolean;
};
type OrgDetail = Org & {
  users: { id: string; email: string; full_name: string; role: string; is_active: boolean; mfa_enabled: boolean; last_login_at: string | null }[];
  credit_transactions: { id: number; kind: string; delta: number; balance_after: number; note: string | null; created_at: string }[];
};
type Invite = {
  id: string; domain: string; email: string; company_name: string; source_url: string; status: string; status_note: string | null;
  request_count: number; sent_count: number; last_sent_at: string | null; updated_at: string;
};
type Job = { id: string; kind: string; status: string; attempts: number; last_error: string | null; organization_name: string | null; created_at: string };
type AuditRow = { id: number; actor_email: string; action: string; target_type: string; target_id: string; meta: Record<string, unknown>; created_at: string };

const TABS = ["Health", "Accounts", "Invitations", "Opt-outs", "Jobs", "Audit"] as const;
const CHECK_VARIANT = { ok: "default", warn: "caution", fail: "destructive" } as const;

/** Every fix asks for a reason, which is kept in the platform audit log. */
function useAction(onDone: () => void) {
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  async function run(path: string, body: Record<string, unknown>, prompt: string, done: string) {
    const note = window.prompt(`${prompt}\n\nReason (kept in the audit log):`);
    if (!note || note.trim().length < 3) return;
    try {
      const out = await api<Record<string, unknown>>(path, { body: { ...body, note: note.trim() } });
      setMessage({ kind: "success", text: out && typeof out.skipped === "string" ? `Not sent: ${out.skipped}` : done });
      onDone();
    } catch (err) {
      setMessage({ kind: "error", text: errorMessage(err) });
    }
  }
  const banner = message && <Alert variant={message.kind} className="mb-4">{message.text}</Alert>;
  return { run, banner };
}

function HealthTab() {
  const health = useFetch<Health>("platform/health");
  const overview = useFetch<Overview>("platform/overview");
  const o = overview.data;
  const stat = (label: string, value: unknown) => (
    <Card key={label}>
      <CardHeader className="pb-2"><CardDescription>{label}</CardDescription><CardTitle className="text-2xl">{String(value ?? "–")}</CardTitle></CardHeader>
    </Card>
  );
  return (
    <div className="space-y-6">
      {o && (
        <div className="grid gap-3 sm:grid-cols-4">
          {stat("Accounts", o.organizations)}
          {stat("Users", o.users)}
          {stat("Sign-ups (7 days)", o.signups_7d)}
          {stat("Active trials", o.trials_active)}
          {stat("Sourcing requests (7 days)", o.sourcing_requests_7d)}
          {stat("Credits spent (7 days)", o.credits_spent_7d)}
          {stat("Paying (active)", (o.subscriptions as Record<string, number>)?.active ?? 0)}
          {stat("Failed jobs", (o.jobs as Record<string, number>)?.dead ?? 0)}
        </div>
      )}
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>System health</CardTitle>
            <CardDescription>{health.data ? `Checked ${formatDate(health.data.checked_at)}` : "Checking…"}</CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => { health.reload(); overview.reload(); }}>Run checks again</Button>
        </CardHeader>
        <CardContent>
          {health.error && <Alert variant="error">{health.error.message}</Alert>}
          <ul className="divide-y">
            {health.data?.checks.map((c) => (
              <li key={c.name} className="flex items-start gap-3 py-2 text-sm">
                <Badge variant={CHECK_VARIANT[c.status]} className="w-14 justify-center">{c.status.toUpperCase()}</Badge>
                <span className="w-44 shrink-0 font-medium">{c.name}</span>
                <span className="text-muted-foreground">{c.detail}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function OrgPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const detail = useFetch<OrgDetail>(`platform/organizations/${id}`);
  const { run, banner } = useAction(detail.reload);
  const [delta, setDelta] = useState("");
  const d = detail.data;
  if (!d) return <p className="text-sm text-muted-foreground">Loading…</p>;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle>{d.name}</CardTitle>
          <CardDescription>
            {d.account_type} · {d.billing_exempt ? "complimentary" : `${d.plan ?? "no plan"} (${d.subscription_status})`} · {d.credit_balance} credits
            {d.trial_ends_at && ` · trial ends ${formatDate(d.trial_ends_at)}`}{d.stripe_customer_id && ` · Stripe ${d.stripe_customer_id}`}
          </CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {banner}
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="delta">Add or remove credits</Label>
            <Input id="delta" type="number" className="w-36" placeholder="e.g. 10 or -5" value={delta} onChange={(e) => setDelta(e.target.value)} />
          </div>
          <Button size="sm" disabled={!Number(delta)} onClick={() => run(`platform/organizations/${id}/credits`, { delta: Number(delta) },
            `Change ${d.name}'s balance by ${delta} credits?`, "Credits updated")}>Apply</Button>
          <Button size="sm" variant="outline" onClick={() => run(`platform/organizations/${id}/extend-trial`, { days: 7 },
            `Extend ${d.name}'s free trial by 7 days?`, "Trial extended by 7 days")}>Extend trial 7 days</Button>
          <Button size="sm" variant="outline" onClick={() => run(`platform/organizations/${id}/complimentary`, { exempt: !d.billing_exempt },
            d.billing_exempt ? `Make ${d.name} pay normally again?` : `Make ${d.name} complimentary (free, unlimited AI)?`,
            d.billing_exempt ? "Account pays normally again" : "Account is now complimentary")}>
            {d.billing_exempt ? "Remove complimentary" : "Make complimentary"}
          </Button>
        </div>
        <div>
          <p className="mb-1 text-sm font-medium">Users</p>
          <ul className="divide-y text-sm">
            {d.users.map((u) => (
              <li key={u.id} className="flex flex-wrap items-center gap-2 py-2">
                <span className="font-medium">{u.full_name}</span>
                <span className="text-muted-foreground">{u.email} · {u.role} · 2FA {u.mfa_enabled ? "on" : "off"} · last sign-in {formatDate(u.last_login_at)}</span>
                {!u.is_active && <Badge variant="destructive">deactivated</Badge>}
                <Button size="sm" variant="outline" className="ml-auto" onClick={() => run(`platform/users/${u.id}/active`, { active: !u.is_active },
                  `${u.is_active ? "Deactivate and sign out" : "Reactivate"} ${u.email}?`, u.is_active ? "User deactivated" : "User reactivated")}>
                  {u.is_active ? "Deactivate" : "Reactivate"}
                </Button>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-1 text-sm font-medium">Recent credit activity</p>
          <ul className="divide-y text-sm">
            {d.credit_transactions.map((t) => (
              <li key={t.id} className="flex gap-3 py-1">
                <span className="w-36 text-muted-foreground">{formatDate(t.created_at)}</span>
                <span className={cn("w-14 text-right font-mono", t.delta < 0 && "text-destructive")}>{t.delta > 0 ? `+${t.delta}` : t.delta}</span>
                <span className="w-24">{t.kind}</span>
                <span className="text-muted-foreground">{t.note}</span>
              </li>
            ))}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}

function AccountsTab() {
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const orgs = useFetch<Org[]>(`platform/organizations?limit=100${search ? `&q=${encodeURIComponent(search)}` : ""}`);
  return (
    <div className="space-y-4">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setSearch(q); }}>
        <Input aria-label="Search accounts" placeholder="Business name, user email or Stripe customer id" value={q} onChange={(e) => setQ(e.target.value)} />
        <Button type="submit">Search</Button>
      </form>
      {open && <OrgPanel id={open} onClose={() => { setOpen(null); orgs.reload(); }} />}
      <ul className="divide-y rounded-md border bg-card text-sm">
        {orgs.data?.map((o) => (
          <li key={o.id}>
            <button className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left hover:bg-secondary" onClick={() => setOpen(o.id)}>
              <span className="font-medium">{o.name}</span>
              <Badge>{o.account_type}</Badge>
              <span className="text-muted-foreground">{o.plan ?? "no plan"} · {o.subscription_status} · {o.credit_balance} credits · {o.users} user(s)</span>
              <span className="ml-auto text-xs text-muted-foreground">joined {formatDate(o.created_at)}</span>
            </button>
          </li>
        ))}
        {orgs.data?.length === 0 && <li className="px-4 py-3 text-muted-foreground">No accounts match.</li>}
      </ul>
    </div>
  );
}

function InvitesTab() {
  const [status, setStatus] = useState("");
  const invites = useFetch<Invite[]>(`platform/invites?limit=100${status ? `&status=${status}` : ""}`);
  const { run, banner } = useAction(invites.reload);
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">Businesses the AI sourcing engine found and invited. Buyers never see these names; only you can.</p>
      {banner}
      <Select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)} className="w-48">
        <option value="">All statuses</option>
        {["pending", "sent", "held", "joined", "suppressed"].map((s) => <option key={s} value={s}>{s}</option>)}
      </Select>
      <ul className="divide-y rounded-md border bg-card text-sm">
        {invites.data?.map((i) => (
          <li key={i.id} className="flex flex-wrap items-center gap-2 px-4 py-3">
            <div className="min-w-0">
              <p className="font-medium">{i.company_name} <span className="font-normal text-muted-foreground">({i.domain})</span></p>
              <p className="text-xs text-muted-foreground">
                {i.email} · {i.request_count} request(s) · sent {i.sent_count}× {i.last_sent_at && `(last ${formatDate(i.last_sent_at)})`}
                {i.status_note && ` · ${i.status_note}`}
              </p>
            </div>
            <Badge variant={i.status === "suppressed" ? "destructive" : i.status === "held" ? "caution" : "default"}>{i.status}</Badge>
            {!["joined", "suppressed"].includes(i.status) && (
              <span className="ml-auto flex gap-2">
                <Button size="sm" variant="outline" onClick={() => run(`platform/invites/${i.id}/send`, { force: true },
                  `Send the invitation to ${i.email} now, ignoring the cooldown and daily cap?`, "Invitation sent")}>Send now</Button>
                {i.status !== "held" && (
                  <Button size="sm" variant="outline" onClick={() => run(`platform/invites/${i.id}/hold`, {}, `Hold the invitation to ${i.domain}?`, "Invitation held")}>Hold</Button>
                )}
              </span>
            )}
          </li>
        ))}
        {invites.data?.length === 0 && <li className="px-4 py-3 text-muted-foreground">No invitations.</li>}
      </ul>
    </div>
  );
}

function OptOutsTab() {
  const rows = useFetch<{ value: string; reason: string; created_at: string }[]>("platform/suppressions");
  const { run, banner } = useAction(rows.reload);
  return (
    <div className="space-y-4">
      <Alert>Addresses and domains that unsubscribed are never emailed again. Lift one only when that business asks you to, in writing.</Alert>
      {banner}
      <ul className="divide-y rounded-md border bg-card text-sm">
        {rows.data?.map((r) => (
          <li key={r.value} className="flex items-center gap-2 px-4 py-2">
            <span className="font-medium">{r.value}</span>
            <span className="text-muted-foreground">{r.reason} · {formatDate(r.created_at)}</span>
            <Button size="sm" variant="outline" className="ml-auto" onClick={() => run("platform/suppressions/lift", { value: r.value },
              `Allow invitations to ${r.value} again?`, "Opt-out lifted")}>Lift</Button>
          </li>
        ))}
        {rows.data?.length === 0 && <li className="px-4 py-3 text-muted-foreground">Nobody has opted out.</li>}
      </ul>
    </div>
  );
}

function JobsTab() {
  const [status, setStatus] = useState("dead");
  const jobs = useFetch<Job[]>(`platform/jobs?limit=100${status ? `&status=${status}` : ""}`);
  const { run, banner } = useAction(jobs.reload);
  return (
    <div className="space-y-4">
      {banner}
      <Select aria-label="Filter jobs" value={status} onChange={(e) => setStatus(e.target.value)} className="w-48">
        <option value="dead">Failed (dead)</option>
        <option value="queued">Queued</option>
        <option value="running">Running</option>
        <option value="succeeded">Succeeded</option>
        <option value="cancelled">Stopped</option>
        <option value="">All</option>
      </Select>
      <ul className="divide-y rounded-md border bg-card text-sm">
        {jobs.data?.map((j) => (
          <li key={j.id} className="flex flex-wrap items-center gap-2 px-4 py-2">
            <span className="font-mono text-xs">{j.kind}</span>
            <Badge variant={j.status === "dead" ? "destructive" : "neutral"}>{j.status === "cancelled" ? "stopped" : j.status}</Badge>
            <span className="text-muted-foreground">{j.organization_name} · {j.attempts} attempt(s) · {formatDate(j.created_at)}</span>
            {(j.status === "queued" || j.status === "running") && (
              <Button size="sm" variant="outline" className="ml-auto" onClick={() => run(`platform/jobs/${j.id}/cancel`, {},
                `Stop ${j.kind} for ${j.organization_name}? The customer sees "Stopped by the platform team".`, "Job stopped")}>Stop</Button>
            )}
            {(j.status === "dead" || j.status === "cancelled") && (
              <Button size="sm" variant="outline" className="ml-auto" onClick={() => run(`platform/jobs/${j.id}/retry`, {}, `Retry ${j.kind}?`, "Job queued again")}>Retry</Button>
            )}
            {j.last_error && <p className="w-full truncate text-xs text-destructive">{j.last_error}</p>}
          </li>
        ))}
        {jobs.data?.length === 0 && <li className="px-4 py-3 text-muted-foreground">No jobs here.</li>}
      </ul>
    </div>
  );
}

function AuditTab() {
  const rows = useFetch<AuditRow[]>("platform/audit?limit=200");
  return (
    <ul className="divide-y rounded-md border bg-card text-sm">
      {rows.data?.map((r) => (
        <li key={r.id} className="px-4 py-2">
          <p><span className="font-medium">{r.action}</span> <span className="text-muted-foreground">{r.target_type} {r.target_id}</span></p>
          <p className="text-xs text-muted-foreground">{formatDate(r.created_at)} · {r.actor_email} · {typeof r.meta.note === "string" ? r.meta.note : ""}</p>
        </li>
      ))}
      {rows.data?.length === 0 && <li className="px-4 py-3 text-muted-foreground">Nothing has been changed from the back office yet.</li>}
    </ul>
  );
}

export default function PlatformPage() {
  const me = useMe();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Health");
  if (!me.is_platform_admin) return <Alert variant="error">Page not found.</Alert>;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Back office</h1>
        <p className="text-sm text-muted-foreground">Every account on the platform, health checks and manual fixes. Every change is logged with your reason.</p>
      </div>
      <div role="tablist" className="flex flex-wrap gap-1 border-b">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground")}>
            {t}
          </button>
        ))}
      </div>
      {tab === "Health" && <HealthTab />}
      {tab === "Accounts" && <AccountsTab />}
      {tab === "Invitations" && <InvitesTab />}
      {tab === "Opt-outs" && <OptOutsTab />}
      {tab === "Jobs" && <JobsTab />}
      {tab === "Audit" && <AuditTab />}
    </div>
  );
}
