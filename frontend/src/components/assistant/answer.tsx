"use client";

import Link from "next/link";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AgentBlock, AgentMessage, AgentSource } from "@/lib/types";
import { cn } from "@/lib/utils";

const KIND_LABEL: Record<AgentSource["kind"], string> = {
  rfq: "RFQ", supplier: "Supplier", document: "Document", comparison: "Comparison", analysis: "Analysis", quote: "Quotes",
  glossary: "Reference", method: "Method", report: "Report", requirements: "Requirements", queue: "Work queue",
};
const STATUS_TEXT: Record<string, string> = {
  answered: "Answered from your data", fallback: "Model output rejected – deterministic answer shown",
  refused: "Not an action the assistant can take", clarify: "Needs more detail", error: "Failed",
};

function sourceHref(s: AgentSource): string | null {
  if (s.kind === "report" && s.ref) return `/assistant/reports/${s.ref}`;
  return s.path;
}

export function Blocks({ blocks, onCite, active }: { blocks: AgentBlock[]; onCite?: (id: string) => void; active?: string | null }) {
  return (
    <div className="space-y-1.5">
      {blocks.map((b, i) =>
        b.type === "heading" ? (
          <h3 key={i} className="pt-2 text-sm font-semibold">{b.text}</h3>
        ) : (
          <p key={i} className="text-sm leading-relaxed">
            {b.text}{" "}
            {b.sources.map((s) => (
              <button key={s} type="button" onClick={() => onCite?.(s)} aria-label={`Show source ${s}`}
                className={cn("ml-0.5 rounded px-1 align-baseline font-mono text-[11px] text-primary hover:bg-primary/10",
                  active === s ? "bg-primary/15" : "bg-primary/5")}>
                {s}
              </button>
            ))}
          </p>
        ),
      )}
    </div>
  );
}

export function Sources({ sources, active, onSelect }: { sources: AgentSource[]; active?: string | null; onSelect?: (id: string) => void }) {
  if (sources.length === 0) return null;
  return (
    <ol className="grid gap-2 md:grid-cols-2">
      {sources.map((s) => {
        const href = sourceHref(s);
        return (
          <li key={s.id} id={`src-${s.id}`}
            className={cn("rounded-md border p-2 text-xs transition-colors", active === s.id && "border-primary bg-primary/5")}
            onClick={() => onSelect?.(s.id)}>
            <div className="flex items-center gap-2">
              <span className="font-mono font-semibold text-primary">{s.id}</span>
              <Badge variant="neutral">{KIND_LABEL[s.kind] ?? s.kind}</Badge>
              {href ? <Link href={href} className="truncate font-medium hover:underline">{s.title}</Link> : <span className="truncate font-medium">{s.title}</span>}
            </div>
            {s.as_of && <p className="mt-0.5 text-muted-foreground">As of {s.as_of}</p>}
            <p className="mt-1 line-clamp-4 whitespace-pre-line text-muted-foreground">{s.excerpt}</p>
          </li>
        );
      })}
    </ol>
  );
}

export function Unknowns({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="rounded-md border border-dashed px-3 py-2 text-sm">
      <p className="font-medium">Unknown</p>
      <ul className="list-disc pl-5 text-muted-foreground">{items.map((u) => <li key={u}>{u}</li>)}</ul>
    </div>
  );
}

export function AssistantMessage({ message, onAsk, onFeedback }: {
  message: AgentMessage; onAsk?: (q: string) => void; onFeedback?: (id: string, rating: 1 | -1) => void;
}) {
  const [active, setActive] = useState<string | null>(null);
  const [showTrace, setShowTrace] = useState(false);
  const run = message.run;
  const notice = message.blocks.length === 0 && message.unknowns.length === 0 ? message.content : null;
  const refusal = run?.status === "refused" || run?.status === "error";

  function cite(id: string) {
    setActive(id);
    document.getElementById(`src-${id}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  return (
    <div className="space-y-3">
      {(notice || refusal) && <p className="text-sm">{message.content.split("\n")[0]}</p>}
      {run?.status === "clarify" && <p className="text-sm">I need a little more detail to answer that.</p>}
      <Blocks blocks={message.blocks} onCite={cite} active={active} />
      <Unknowns items={message.unknowns} />
      {message.sources.length > 0 && (
        <details open className="text-sm">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">Sources ({message.sources.length})</summary>
          <div className="mt-2"><Sources sources={message.sources} active={active} onSelect={setActive} /></div>
        </details>
      )}
      {message.follow_ups.length > 0 && onAsk && (
        <div className="flex flex-wrap gap-2">
          {message.follow_ups.map((f) => (
            <Button key={f} size="sm" variant="outline" className="h-7 text-xs" onClick={() => onAsk(f)}>{f}</Button>
          ))}
        </div>
      )}
      {run && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant={run.status === "answered" ? "neutral" : run.status === "fallback" ? "caution" : run.status === "error" ? "destructive" : "neutral"}>
            {STATUS_TEXT[run.status] ?? run.status}
          </Badge>
          <span>{run.mode === "llm" ? `Model ${run.provider ?? ""}` : "Deterministic"} · {(run.latency_ms / 1000).toFixed(1)} s</span>
          <button type="button" className="underline-offset-2 hover:underline" onClick={() => setShowTrace(!showTrace)} aria-expanded={showTrace}>
            {showTrace ? "Hide trace" : "Trace"}
          </button>
          <Link href={`/assistant/runs/${run.id}`} className="underline-offset-2 hover:underline">Full trace</Link>
          {onFeedback && (
            <span className="ml-auto flex gap-1">
              <Button size="sm" variant={message.feedback === 1 ? "default" : "ghost"} className="h-7 px-2" aria-label="Helpful" onClick={() => onFeedback(message.id, 1)}>👍</Button>
              <Button size="sm" variant={message.feedback === -1 ? "default" : "ghost"} className="h-7 px-2" aria-label="Not helpful" onClick={() => onFeedback(message.id, -1)}>👎</Button>
            </span>
          )}
        </div>
      )}
      {run && showTrace && (
        <div className="rounded-md bg-secondary/50 p-2 font-mono text-[11px] leading-relaxed">
          <p>run {run.id} · prompt {run.prompt_version} · intent {run.intent ?? "–"}{run.model ? ` · ${run.model}` : ""}</p>
          {run.tools.map((t, i) => (
            <p key={i}>{t.status === "ok" ? "✓" : "✕"} {t.tool} ({t.ms} ms){t.error ? ` – ${t.error}` : ""}</p>
          ))}
          {run.flags.length > 0 && <p>flags: {run.flags.join(", ")}</p>}
          {run.model_output_rejected && <p>model output rejected: {run.model_output_rejected}</p>}
        </div>
      )}
    </div>
  );
}
