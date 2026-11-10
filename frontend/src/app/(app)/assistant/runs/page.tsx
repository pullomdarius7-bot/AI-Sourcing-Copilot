"use client";

import Link from "next/link";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { useFetch } from "@/lib/api";
import type { AgentRun } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const PAGE = 50;

export default function RunsPage() {
  const [status, setStatus] = useState("");
  const [mode, setMode] = useState("");
  const [offset, setOffset] = useState(0);
  const query = new URLSearchParams({ limit: String(PAGE), offset: String(offset), ...(status && { status }), ...(mode && { mode }) });
  const runs = useFetch<{ items: AgentRun[]; total: number }>(`agent/runs?${query}`);
  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs text-muted-foreground"><Link href="/assistant" className="hover:underline">Assistant</Link> / Runs</p>
        <h1 className="text-2xl font-semibold tracking-tight">Agent runs</h1>
        <p className="text-sm text-muted-foreground">Every answer in the organization, with its plan, tools, model calls and safety checks.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Select aria-label="Outcome" className="w-44" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
          <option value="">Any outcome</option>
          {["answered", "fallback", "refused", "clarify", "error"].map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <Select aria-label="Mode" className="w-44" value={mode} onChange={(e) => { setMode(e.target.value); setOffset(0); }}>
          <option value="">Any mode</option>
          <option value="deterministic">deterministic</option>
          <option value="llm">model</option>
        </Select>
      </div>
      {runs.error && <Alert variant="error">{runs.error.message}</Alert>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 pr-3 font-medium">When</th>
              <th className="py-2 pr-3 font-medium">Who</th>
              <th className="py-2 pr-3 font-medium">Question</th>
              <th className="py-2 pr-3 font-medium">Outcome</th>
              <th className="py-2 pr-3 font-medium">Mode</th>
              <th className="py-2 pr-3 text-right font-medium">Sources</th>
              <th className="py-2 text-right font-medium">ms</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {runs.data?.items.map((r) => (
              <tr key={r.id}>
                <td className="py-2 pr-3 whitespace-nowrap">{formatDate(r.created_at)}</td>
                <td className="py-2 pr-3">{r.user_email}</td>
                <td className="py-2 pr-3"><Link href={`/assistant/runs/${r.id}`} className="text-primary hover:underline">{r.question.slice(0, 90)}</Link></td>
                <td className="py-2 pr-3"><Badge variant={r.status === "answered" ? "neutral" : r.status === "error" ? "destructive" : "caution"}>{r.status}</Badge></td>
                <td className="py-2 pr-3">{r.mode}{r.provider ? ` (${r.provider})` : ""}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{r.source_ids.length}</td>
                <td className="py-2 text-right tabular-nums">{r.latency_ms}</td>
              </tr>
            ))}
            {runs.data?.items.length === 0 && <tr><td colSpan={7} className="py-6 text-center text-muted-foreground">No runs.</td></tr>}
          </tbody>
        </table>
      </div>
      {runs.data && runs.data.total > PAGE && (
        <div className="flex items-center gap-2 text-sm">
          <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
          <span>{offset + 1}–{Math.min(offset + PAGE, runs.data.total)} of {runs.data.total}</span>
          <Button size="sm" variant="outline" disabled={offset + PAGE >= runs.data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
        </div>
      )}
    </div>
  );
}
