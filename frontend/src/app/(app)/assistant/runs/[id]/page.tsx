"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { AssistantMessage } from "@/components/assistant/answer";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useFetch } from "@/lib/api";
import type { AgentRun } from "@/lib/types";
import { formatDate } from "@/lib/utils";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[10rem_1fr] gap-2 py-1 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const run = useFetch<AgentRun>(`agent/runs/${id}`);
  if (run.error) return <Alert variant="error">{run.error.message}</Alert>;
  const r = run.data;
  if (!r) return <p className="text-sm text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs text-muted-foreground"><Link href="/assistant" className="hover:underline">Assistant</Link> / Run trace</p>
        <h1 className="text-2xl font-semibold tracking-tight">Run trace</h1>
        <p className="text-sm text-muted-foreground">Everything that produced this answer: the plan, each tool call, each model call and the safety checks.</p>
      </div>
      <Card>
        <CardHeader><CardTitle>Summary</CardTitle></CardHeader>
        <CardContent>
          <dl>
            <Row label="Question">{r.question}</Row>
            <Row label="Asked by">{r.user_email ?? r.user_id} on {formatDate(r.created_at)}</Row>
            <Row label="Outcome"><Badge variant={r.status === "answered" ? "neutral" : "caution"}>{r.status}</Badge> {r.intent && <span className="ml-2 text-muted-foreground">intent {r.intent}</span>}</Row>
            <Row label="Mode">{r.mode}{r.provider && ` · ${r.provider} ${r.model ?? ""}`}</Row>
            <Row label="Prompt">{r.prompt_version} <span className="font-mono text-xs text-muted-foreground">{r.prompt_sha256.slice(0, 16)}</span></Row>
            <Row label="Latency">{r.latency_ms} ms{r.input_tokens != null && ` · ${r.input_tokens} in / ${r.output_tokens ?? 0} out tokens`}</Row>
            <Row label="Answer fingerprint"><span className="font-mono text-xs">{r.answer_sha256 ?? "–"}</span></Row>
            {r.error && <Row label="Error">{r.error}</Row>}
          </dl>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Plan and tool calls</CardTitle>
          <CardDescription>Tools only read data the asking user may see; their figures come from the deterministic services.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {r.tool_calls.length === 0 && <p className="text-sm text-muted-foreground">No tools were called.</p>}
          {r.tool_calls.map((c, i) => (
            <div key={i} className="rounded-md border p-2 text-xs">
              <p className="font-medium">
                {c.status === "ok" ? "✓" : "✕"} {c.tool} <span className="text-muted-foreground">({c.ms} ms, planned by {r.plan[i]?.by ?? "rules"})</span>
              </p>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">{JSON.stringify({ args: c.args, sources: c.sources, summary: c.summary, error: c.error }, null, 2)}</pre>
            </div>
          ))}
        </CardContent>
      </Card>
      {r.llm_calls.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Model calls</CardTitle><CardDescription>Prompts and responses are fingerprinted; external providers receive redacted text.</CardDescription></CardHeader>
          <CardContent className="space-y-1 text-xs">
            {r.llm_calls.map((c, i) => (
              <p key={i} className="font-mono">
                {c.purpose} · {c.provider} {c.model} · {c.status} · {c.ms} ms{c.input_tokens != null && ` · ${c.input_tokens}/${c.output_tokens} tokens`}
                {c.redacted && " · redacted"} · prompt {c.prompt_sha256.slice(0, 12)}{c.response_sha256 && ` · response ${c.response_sha256.slice(0, 12)}`}{c.error && ` · ${c.error}`}
              </p>
            ))}
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader><CardTitle>Safety checks</CardTitle></CardHeader>
        <CardContent className="space-y-1 text-sm">
          <p>Flags: {r.safety.flags?.length ? r.safety.flags.join(", ") : "none"}</p>
          {r.safety.model_output_rejected && <p className="text-caution">Model output rejected: {r.safety.model_output_rejected}</p>}
          {(r.safety.problems?.length ?? 0) > 0 && <p>Problems: {r.safety.problems?.join("; ")}</p>}
          <p>Sources cited: {r.source_ids.join(", ") || "none"}</p>
        </CardContent>
      </Card>
      {r.answer && (
        <Card>
          <CardHeader><CardTitle>Answer shown to the user</CardTitle></CardHeader>
          <CardContent><AssistantMessage message={r.answer} /></CardContent>
        </Card>
      )}
    </div>
  );
}
