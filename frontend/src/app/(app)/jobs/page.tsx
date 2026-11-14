"use client";

import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { AiAuditEntry, JobItem, JobStats } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

const STATUS_VARIANT = { queued: "caution", running: "default", succeeded: "neutral", dead: "destructive", cancelled: "neutral" } as const;
const STATUS_LABEL: Record<string, string> = { dead: "dead-lettered", cancelled: "stopped" };

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl">{value}</CardTitle>
      </CardHeader>
      {hint && <CardContent className="text-xs text-muted-foreground">{hint}</CardContent>}
    </Card>
  );
}

export default function JobsPage() {
  const me = useMe();
  const allowed = me.permissions.includes("jobs:read");
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const stats = useFetch<JobStats>(allowed ? "jobs/stats" : null);
  const jobs = useFetch<JobItem[]>(allowed ? `jobs?limit=50${status ? `&status=${status}` : ""}` : null);
  const audit = useFetch<AiAuditEntry[]>(me.permissions.includes("audit:read") ? "ai/audit?limit=25" : null);
  const providers = useFetch<{ name: string; model: string; external: boolean }[]>(allowed ? "ai/providers" : null);

  useEffect(() => {
    if (!allowed) return;
    const t = setInterval(() => {
      stats.reload();
      jobs.reload();
    }, 5000);
    return () => clearInterval(t);
  }, [allowed, stats.reload, jobs.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!allowed) return <Alert variant="error">Your role cannot view job monitoring.</Alert>;

  async function stop(id: string, kind: string) {
    if (!window.confirm(`Stop this ${kind} job? Anything it has not finished is discarded; Retry starts it again from the beginning.`)) return;
    setError(null);
    try {
      await api(`jobs/${id}/cancel`, { method: "POST" });
      stats.reload();
      jobs.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function retry(id: string) {
    setError(null);
    try {
      await api(`jobs/${id}/retry`, { method: "POST" });
      stats.reload();
      jobs.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const s = stats.data;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Jobs</h1>
        <p className="mt-1 text-sm text-muted-foreground">Background processing queue and dead-letter queue. Refreshes every 5 seconds.</p>
      </div>
      {error && <Alert variant="error">{error}</Alert>}

      {s && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Queued" value={s.queued} hint={s.oldest_queued_age_seconds !== null ? `Oldest waiting ${s.oldest_queued_age_seconds}s` : "Queue is empty"} />
          <Stat label="Running" value={s.running} hint={s.active_workers.length ? `${s.active_workers.length} active worker(s)` : "No worker is busy"} />
          <Stat label="Succeeded (1 h)" value={s.succeeded_last_hour} hint={s.avg_duration_ms_last_hour !== null ? `Avg ${s.avg_duration_ms_last_hour} ms` : undefined} />
          <Stat label="Dead-lettered" value={<span className={s.dead > 0 ? "text-destructive" : ""}>{s.dead}</span>} hint="Retries exhausted or permanent failure" />
        </div>
      )}

      {providers.data && (
        <p className="text-sm text-muted-foreground">
          AI providers:{" "}
          {providers.data.length === 0
            ? "none configured (documents are scanned and parsed; analysis is skipped)"
            : providers.data.map((p) => `${p.name} (${p.model}${p.external ? ", external" : ", self-hosted"})`).join(" → ")}
        </p>
      )}

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle>Jobs</CardTitle>
            <select aria-label="Filter by status" className="h-9 rounded-md border border-input bg-card px-2 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="succeeded">Succeeded</option>
              <option value="dead">Dead-lettered</option>
              <option value="cancelled">Stopped</option>
            </select>
          </div>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">Job</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 font-medium">Attempts</th>
                <th className="py-2 pr-4 font-medium">Created</th>
                <th className="py-2 font-medium">Last error</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {jobs.data?.map((j) => (
                <tr key={j.id}>
                  <td className="py-2 pr-4 font-mono text-xs">{j.kind}</td>
                  <td className="py-2 pr-4">
                    <Badge variant={STATUS_VARIANT[j.status]}>{STATUS_LABEL[j.status] ?? j.status}</Badge>
                  </td>
                  <td className="py-2 pr-4">{j.attempts}/{j.max_attempts}</td>
                  <td className="whitespace-nowrap py-2 pr-4 text-muted-foreground">{formatDate(j.created_at)}</td>
                  <td className="max-w-xs py-2">
                    {j.last_error && (
                      <p className={cn("truncate text-xs", j.status === "cancelled" ? "text-muted-foreground" : "text-destructive")} title={j.last_error}>{j.last_error}</p>
                    )}
                    {me.permissions.includes("jobs:manage") && (j.status === "queued" || j.status === "running") && (
                      <Button size="sm" variant="outline" className="mt-1" onClick={() => stop(j.id, j.kind)}>
                        Stop
                      </Button>
                    )}
                    {me.permissions.includes("jobs:manage") && (j.status === "dead" || j.status === "cancelled") && (
                      <Button size="sm" variant="outline" className="mt-1" onClick={() => retry(j.id)}>
                        Retry
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
              {jobs.data && jobs.data.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-muted-foreground">No jobs.</td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {audit.data && (
        <Card>
          <CardHeader>
            <CardTitle>AI call audit trail</CardTitle>
            <CardDescription>Every model call: provider, model, prompt and response hashes, latency, guardrail flags. Document text is never stored here.</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-2 pr-4 font-medium">When</th>
                  <th className="py-2 pr-4 font-medium">Provider</th>
                  <th className="py-2 pr-4 font-medium">Result</th>
                  <th className="py-2 pr-4 font-medium">Latency</th>
                  <th className="py-2 font-medium">Tokens in/out</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {audit.data.map((a) => (
                  <tr key={a.id}>
                    <td className="whitespace-nowrap py-2 pr-4 text-muted-foreground">{formatDate(a.created_at)}</td>
                    <td className="py-2 pr-4">{a.provider} <span className="text-xs text-muted-foreground">{a.model}</span></td>
                    <td className="py-2 pr-4">
                      <Badge variant={a.status === "ok" ? "default" : "destructive"}>{a.status.replace("_", " ")}</Badge>
                      {a.error && <p className="mt-1 text-xs text-destructive">{a.error}</p>}
                    </td>
                    <td className="py-2 pr-4">{a.latency_ms} ms</td>
                    <td className="py-2">{a.input_tokens ?? "—"} / {a.output_tokens ?? "—"}</td>
                  </tr>
                ))}
                {audit.data.length === 0 && (
                  <tr><td colSpan={5} className="py-6 text-center text-muted-foreground">No AI calls yet.</td></tr>
                )}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
