"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ConfidenceBadge, RunStatusBadge, StateBadge } from "@/components/review-badges";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import { buildSegments, type Span } from "@/lib/evidence";
import type { Evidence, ParameterItem, RequirementItem, ReviewEvent, RunDetail, ValidationState } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

type Item = { kind: "requirement"; data: RequirementItem } | { kind: "parameter"; data: ParameterItem };

const MARK: Record<ValidationState, string> = {
  unverified: "bg-caution/20 border-b-2 border-caution",
  verified: "bg-primary/15 border-b-2 border-primary",
  corrected: "bg-primary/15 border-b-2 border-primary",
  rejected: "bg-destructive/10 line-through border-b border-destructive",
};
const CATEGORIES = ["material", "dimensions", "tolerance", "quantity", "delivery", "quality", "compliance", "commercial", "packaging", "other"];
const PRIORITIES = ["mandatory", "preferred", "optional", "unknown"];
const selectClass = "h-10 w-full rounded-md border border-input bg-card px-2 text-sm";
const textareaClass = "min-h-[72px] w-full rounded-md border border-input bg-card px-3 py-2 text-sm";

function path(item: Item, action: string) {
  return `${item.kind === "requirement" ? "requirements" : "parameters"}/${item.data.id}/${action}`;
}

function valueLabel(p: ParameterItem) {
  return p.unknown || p.display_value === null ? "Not stated" : p.display_value;
}

function ItemPanel({ item, canReview, locked, selectedText, events, onChanged }: {
  item: Item;
  canReview: boolean;
  locked: boolean;
  selectedText: string;
  events: ReviewEvent[];
  onChanged: () => void;
}) {
  const ev: Evidence = item.data;
  const [mode, setMode] = useState<"none" | "reject" | "correct">("none");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // correction form
  const p = item.kind === "parameter" ? item.data : null;
  const r = item.kind === "requirement" ? item.data : null;
  const [unknown, setUnknown] = useState(false);
  const [valueType, setValueType] = useState<"text" | "number">(p?.value_type ?? "text");
  const [text, setText] = useState(p?.value_text ?? "");
  const [number, setNumber] = useState(p?.value_number?.toString() ?? "");
  const [unit, setUnit] = useState(p?.unit ?? "");
  const [qualifier, setQualifier] = useState(p?.qualifier ?? "");
  const [evidence, setEvidence] = useState<"document" | "manual">("document");
  const [quote, setQuote] = useState(ev.source_quote ?? "");
  const [statement, setStatement] = useState(r?.statement ?? "");
  const [category, setCategory] = useState(r?.category ?? "other");
  const [priority, setPriority] = useState(r?.priority ?? "unknown");

  useEffect(() => {
    setMode("none");
    setNote("");
    setError(null);
  }, [item.data.id]);

  async function run(action: string, body: unknown) {
    setBusy(true);
    setError(null);
    try {
      await api(path(item, action), { method: "POST", body });
      setMode("none");
      setNote("");
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function submitCorrection(e: React.FormEvent) {
    e.preventDefault();
    if (p) {
      void run("correct", {
        unknown,
        value_type: valueType,
        text: valueType === "text" ? text : null,
        number: valueType === "number" && number !== "" ? Number(number) : null,
        unit: unit || null,
        qualifier: qualifier || null,
        evidence,
        source_quote: quote || null,
        note: note || null,
      });
    } else {
      void run("correct", { statement: statement || null, category, priority, source_quote: quote || null, note: note || null });
    }
  }

  const detail = Object.entries(ev.confidence_detail).filter(([k, v]) => typeof v === "number" && !["final", "model"].includes(k) && v !== 1);
  const history = events.filter((e) => e.item_id === item.data.id).reverse();
  const open = canReview && !locked;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>{p ? p.name.replace(/_/g, " ") : "Requirement"}</CardTitle>
          <div className="flex gap-2">
            <ConfidenceBadge value={ev.confidence} unknown={ev.grounding_status === "unknown"} />
            <StateBadge state={ev.validation_state} />
          </div>
        </div>
        <CardDescription>
          {p ? <span className="font-medium text-foreground">{valueLabel(p)}</span> : r?.statement}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Source</p>
          {ev.source_quote ? (
            <blockquote className="border-l-2 border-primary pl-3">“{ev.source_quote}”</blockquote>
          ) : ev.grounding_status === "unknown" ? (
            <p className="text-caution">The document does not state this. It stays unknown until someone supplies evidence.</p>
          ) : (
            <p className="text-muted-foreground">No quote (manual evidence).</p>
          )}
          <p className="mt-1 text-xs text-muted-foreground">
            {ev.source_line ? `Line ${ev.source_line}` : "Not located in the text"}
            {ev.source_section ? ` · sheet “${ev.source_section}”` : ""} · {ev.grounding_status}
            {typeof (ev.confidence_detail as { reason?: unknown }).reason === "string" ? ` · ${(ev.confidence_detail as { reason: string }).reason}` : ""}
          </p>
        </div>

        {(detail.length > 0 || ev.flags.length > 0) && (
          <div className="space-y-2">
            {detail.length > 0 && (
              <p className="text-xs text-muted-foreground">
                Model said {Math.round(ev.model_confidence * 100)}%.{" "}
                {detail.map(([k, v]) => `${k} ×${v}`).join(", ")}
              </p>
            )}
            <div className="flex flex-wrap gap-1.5">
              {ev.flags.map((f) => (
                <Badge key={f} variant="caution" className="font-mono">{f}</Badge>
              ))}
            </div>
          </div>
        )}

        {ev.original && (
          <p className="rounded-md bg-muted p-2 text-xs text-muted-foreground">
            Machine extracted:{" "}
            {"unknown" in ev.original
              ? ev.original.unknown ? "not stated" : String(ev.original.value_number ?? ev.original.value_text ?? "")
              : String(ev.original.statement ?? "")}
          </p>
        )}
        {ev.review_note && <p className="text-xs text-muted-foreground">Reviewer note: {ev.review_note}</p>}

        {error && <Alert variant="error">{error}</Alert>}

        {open && mode === "none" && (
          <div className="flex flex-wrap gap-2">
            {ev.validation_state === "unverified" && (
              <Button size="sm" disabled={busy} onClick={() => run("verify", {})}>
                {ev.grounding_status === "unknown" ? "Confirm not stated" : "Verify"}
              </Button>
            )}
            {ev.validation_state !== "rejected" && (
              <Button size="sm" variant="outline" onClick={() => setMode("correct")}>Correct</Button>
            )}
            {ev.validation_state !== "rejected" && (
              <Button size="sm" variant="outline" onClick={() => setMode("reject")}>Reject</Button>
            )}
            {ev.validation_state !== "unverified" && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => run("reset", {})}>Undo review</Button>
            )}
          </div>
        )}
        {locked && <p className="text-xs text-muted-foreground">This extraction is closed. Items can no longer be changed.</p>}

        {open && mode === "reject" && (
          <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void run("reject", { reason: note }); }}>
            <Label htmlFor="reject-reason">Why is this wrong?</Label>
            <textarea id="reject-reason" className={textareaClass} value={note} onChange={(e) => setNote(e.target.value)} required minLength={3} />
            <div className="flex gap-2">
              <Button type="submit" size="sm" variant="destructive" disabled={busy}>Reject</Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setMode("none")}>Cancel</Button>
            </div>
          </form>
        )}

        {open && mode === "correct" && (
          <form className="space-y-3" onSubmit={submitCorrection}>
            {p ? (
              <>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={unknown} onChange={(e) => setUnknown(e.target.checked)} />
                  The document does not actually state this (mark unknown)
                </label>
                {!unknown && (
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <Label htmlFor="c-type">Type</Label>
                      <select id="c-type" className={selectClass} value={valueType} onChange={(e) => setValueType(e.target.value as "text" | "number")}>
                        <option value="text">Text</option>
                        <option value="number">Number</option>
                      </select>
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="c-value">Value</Label>
                      {valueType === "text" ? (
                        <Input id="c-value" value={text} onChange={(e) => setText(e.target.value)} />
                      ) : (
                        <Input id="c-value" type="number" step="any" value={number} onChange={(e) => setNumber(e.target.value)} />
                      )}
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="c-unit">Unit</Label>
                      <Input id="c-unit" value={unit} onChange={(e) => setUnit(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="c-qual">Qualifier</Label>
                      <select id="c-qual" className={selectClass} value={qualifier} onChange={(e) => setQualifier(e.target.value as typeof qualifier)}>
                        <option value="">None</option>
                        <option value="exact">Exact</option>
                        <option value="min">Minimum</option>
                        <option value="max">Maximum</option>
                        <option value="approx">Approximately</option>
                      </select>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="space-y-1">
                  <Label htmlFor="c-statement">Statement</Label>
                  <textarea id="c-statement" className={textareaClass} value={statement} onChange={(e) => setStatement(e.target.value)} />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label htmlFor="c-cat">Category</Label>
                    <select id="c-cat" className={selectClass} value={category} onChange={(e) => setCategory(e.target.value)}>
                      {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="c-prio">Priority</Label>
                    <select id="c-prio" className={selectClass} value={priority} onChange={(e) => setPriority(e.target.value)}>
                      {PRIORITIES.map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </div>
                </div>
              </>
            )}
            {!unknown && (
              <div className="space-y-2">
                {p && (
                  <div className="flex gap-4 text-sm">
                    <label className="flex items-center gap-1"><input type="radio" checked={evidence === "document"} onChange={() => setEvidence("document")} /> Quote from the document</label>
                    <label className="flex items-center gap-1"><input type="radio" checked={evidence === "manual"} onChange={() => setEvidence("manual")} /> Other evidence</label>
                  </div>
                )}
                <Label htmlFor="c-quote">{evidence === "manual" ? "Where does it come from? (optional)" : "Quoted passage"}</Label>
                <textarea id="c-quote" className={textareaClass} value={quote} onChange={(e) => setQuote(e.target.value)} />
                {evidence === "document" && (
                  <Button type="button" size="sm" variant="outline" disabled={!selectedText} onClick={() => setQuote(selectedText)}>
                    Use text selected in the document
                  </Button>
                )}
              </div>
            )}
            <div className="space-y-1">
              <Label htmlFor="c-note">{evidence === "manual" && !unknown ? "Explain the evidence (required)" : "Note"}</Label>
              <textarea id="c-note" className={textareaClass} value={note} onChange={(e) => setNote(e.target.value)} />
            </div>
            <p className="text-xs text-muted-foreground">Quotes are checked against the document: the value has to appear in the passage you quote.</p>
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={busy}>Save correction</Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setMode("none")}>Cancel</Button>
            </div>
          </form>
        )}

        {history.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">History</p>
            <ul className="space-y-1 text-xs text-muted-foreground">
              {history.map((e) => (
                <li key={e.id}>
                  {formatDate(e.created_at)} · {e.actor_email ?? "system"} · {e.action.replace(/_/g, " ")}
                  {e.to_state ? ` → ${e.to_state}` : ""}
                  {e.note ? ` (“${e.note}”)` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function EvidenceViewerPage() {
  const { runId } = useParams<{ runId: string }>();
  const me = useMe();
  const run = useFetch<RunDetail>(`extraction-runs/${runId}`);
  const events = useFetch<ReviewEvent[]>(`extraction-runs/${runId}/events`);
  const doc = useFetch<{ text: string }>(run.data ? `documents/${run.data.document_id}/text` : null);
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedText, setSelectedText] = useState("");
  const [decisionNote, setDecisionNote] = useState("");
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const scrollTarget = useRef<HTMLElement | null>(null);

  const items = useMemo(() => {
    const out: Item[] = [];
    for (const r of run.data?.requirements ?? []) {
      out.push({ kind: "requirement", data: r });
      for (const p of r.parameters) out.push({ kind: "parameter", data: p });
    }
    for (const p of run.data?.unknown_parameters ?? []) out.push({ kind: "parameter", data: p });
    return out;
  }, [run.data]);
  const byKey = useMemo(() => new Map(items.map((i) => [`${i.kind}:${i.data.id}`, i])), [items]);

  useEffect(() => {
    const focus = new URLSearchParams(window.location.search).get("focus");
    if (focus && byKey.size && !selected) {
      for (const key of byKey.keys()) if (key.endsWith(focus)) setSelected(key);
    }
  }, [byKey, selected]);

  const spans: Span[] = useMemo(
    () =>
      items.flatMap((i) =>
        i.data.source_start !== null && i.data.source_end !== null
          ? [{ id: `${i.kind}:${i.data.id}`, start: i.data.source_start, end: i.data.source_end, state: i.data.validation_state }]
          : [],
      ),
    [items],
  );
  const segments = useMemo(() => (doc.data ? buildSegments(doc.data.text, spans) : []), [doc.data, spans]);
  const firstSelected = selected ? segments.findIndex((s) => s.ids.includes(selected)) : -1;

  useEffect(() => {
    scrollTarget.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selected, firstSelected]);

  function reloadAll() {
    run.reload();
    events.reload();
  }

  async function decide(kind: "approve" | "reject") {
    setDecisionError(null);
    try {
      await api(`extraction-runs/${runId}/${kind}`, { method: "POST", body: kind === "approve" ? { note: decisionNote || null } : { reason: decisionNote } });
      setRejecting(false);
      setDecisionNote("");
      reloadAll();
    } catch (err) {
      setDecisionError(errorMessage(err));
    }
  }

  if (run.error) return <Alert variant="error">{run.error.message}</Alert>;
  const d = run.data;
  if (!d) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const locked = !["pending_review", "ready_for_approval"].includes(d.status);
  const canReview = me.permissions.includes("requirements:review");
  const canApprove = me.permissions.includes("requirements:approve");
  const total = d.counts.requirements + d.counts.parameters;
  const sel = selected ? byKey.get(selected) : undefined;
  const noteCounts = d.flags.reduce<Record<string, number>>((acc, f) => ({ ...acc, [f.code]: (acc[f.code] ?? 0) + 1 }), {});

  function row(i: Item) {
    const key = `${i.kind}:${i.data.id}`;
    const isSel = key === selected;
    const p = i.kind === "parameter" ? i.data : null;
    return (
      <div key={key}>
        <button
          type="button"
          onClick={() => setSelected(key)}
          aria-pressed={isSel}
          className={cn(
            "flex w-full items-start justify-between gap-3 rounded-md px-3 py-2 text-left text-sm",
            i.kind === "parameter" && "ml-4 w-[calc(100%-1rem)]",
            isSel ? "bg-primary/10 ring-1 ring-primary" : "hover:bg-secondary",
          )}
        >
          <span className="min-w-0">
            {p ? (
              <>
                <span className="text-muted-foreground">{p.name.replace(/_/g, " ")}: </span>
                <span className={cn("font-medium", p.unknown && "text-caution")}>{valueLabel(p)}</span>
              </>
            ) : (
              <span className="font-medium">{(i.data as RequirementItem).statement}</span>
            )}
            {i.kind === "requirement" && (
              <span className="mt-1 flex gap-1.5">
                <Badge>{(i.data as RequirementItem).category}</Badge>
                <Badge variant={(i.data as RequirementItem).priority === "mandatory" ? "default" : "neutral"}>{(i.data as RequirementItem).priority}</Badge>
              </span>
            )}
          </span>
          <span className="flex shrink-0 items-center gap-1.5">
            <ConfidenceBadge value={i.data.confidence} unknown={i.data.grounding_status === "unknown"} />
            <StateBadge state={i.data.validation_state} />
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href="/requirements" className="text-sm text-primary hover:underline">← All extractions</Link>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">{d.filename ?? "Extraction"}</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {d.counts.requirements} requirements, {d.counts.parameters} parameters ({d.counts.unknown} unknown) · {d.provider ?? "—"} {d.model ?? ""} · {formatDate(d.created_at)}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <RunStatusBadge status={d.status} />
            <Button asChild variant="outline" size="sm"><Link href={`/documents/${d.document_id}`}>Document</Link></Button>
            {d.status === "approved" && (
              <Button asChild variant="outline" size="sm"><a href={`/api/extraction-runs/${d.id}/export`}>Export JSON</a></Button>
            )}
          </div>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <div className="h-2 w-56 overflow-hidden rounded-full bg-secondary" role="progressbar" aria-valuenow={total - d.counts.unresolved} aria-valuemin={0} aria-valuemax={total}>
            <div className="h-full bg-primary" style={{ width: total ? `${((total - d.counts.unresolved) / total) * 100}%` : "100%" }} />
          </div>
          <span className="text-xs text-muted-foreground">{total - d.counts.unresolved} of {total} reviewed</span>
        </div>
      </div>

      {d.status === "failed" && <Alert variant="error">Extraction failed: {d.error}</Alert>}
      {d.status === "superseded" && <Alert variant="info">A newer extraction replaced this one.</Alert>}
      {d.source_changed && <Alert variant="error">The document text changed since this extraction. Quotes may no longer match; run the extraction again.</Alert>}
      {d.status === "approved" && <Alert variant="success">Approved {formatDate(d.approved_at)}{d.decision_note ? `: ${d.decision_note}` : ""}</Alert>}
      {d.status === "rejected" && <Alert variant="error">Rejected: {d.decision_note}</Alert>}

      {Object.keys(noteCounts).length > 0 && (
        <details className="rounded-md border bg-card px-4 py-2 text-sm">
          <summary className="cursor-pointer font-medium">Extraction notes ({d.flags.length})</summary>
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(noteCounts).map(([code, n]) => (
              <Badge key={code} variant="caution" className="font-mono">{code}{n > 1 ? ` ×${n}` : ""}</Badge>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">Claims the checker could not confirm in the document were dropped or turned into unknowns; they are listed here for transparency.</p>
        </details>
      )}

      {canApprove && !locked && (
        <Card>
          <CardHeader>
            <CardTitle>Decision</CardTitle>
            <CardDescription>
              {d.status === "ready_for_approval"
                ? "Every item has been reviewed. Approving locks the extraction and releases it for use."
                : `${d.counts.unresolved} item(s) still need review before this can be approved.`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {decisionError && <Alert variant="error">{decisionError}</Alert>}
            <div className="max-w-md space-y-1">
              <Label htmlFor="decision-note">{rejecting ? "Reason for rejecting" : "Note (optional)"}</Label>
              <Input id="decision-note" value={decisionNote} onChange={(e) => setDecisionNote(e.target.value)} />
            </div>
            <div className="flex gap-2">
              <Button disabled={d.status !== "ready_for_approval"} onClick={() => decide("approve")}>Approve</Button>
              {rejecting ? (
                <>
                  <Button variant="destructive" onClick={() => decide("reject")}>Confirm rejection</Button>
                  <Button variant="ghost" onClick={() => setRejecting(false)}>Cancel</Button>
                </>
              ) : (
                <Button variant="outline" onClick={() => setRejecting(true)}>Reject extraction</Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="lg:max-h-[80vh] lg:overflow-auto">
          <CardHeader>
            <CardTitle>Extracted requirements</CardTitle>
            <CardDescription>Select an item to see its evidence in the document.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-1">
              {d.requirements.map((r) => (
                <div key={r.id} className="space-y-1 border-b pb-2 last:border-0">
                  {row({ kind: "requirement", data: r })}
                  {r.parameters.map((p) => row({ kind: "parameter", data: p }))}
                </div>
              ))}
              {d.unknown_parameters.length > 0 && (
                <div className="space-y-1 pt-2">
                  <p className="px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">Not stated in the document</p>
                  {d.unknown_parameters.map((p) => row({ kind: "parameter", data: p }))}
                </div>
              )}
              {items.length === 0 && <p className="py-6 text-center text-sm text-muted-foreground">No requirements were found in this document.</p>}
            </div>
          </CardContent>
        </Card>

        <div className="space-y-4 lg:sticky lg:top-4 lg:self-start">
          {sel && (
            <ItemPanel
              key={selected}
              item={sel}
              canReview={canReview}
              locked={locked}
              selectedText={selectedText}
              events={events.data ?? []}
              onChanged={reloadAll}
            />
          )}
          <Card className="lg:max-h-[60vh] lg:overflow-auto">
            <CardHeader>
              <CardTitle>Document</CardTitle>
              <CardDescription>
                Highlights show where each item comes from. Select text to use it as evidence in a correction.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {!doc.data ? (
                <p className="text-sm text-muted-foreground">Loading document…</p>
              ) : (
                <pre
                  className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed"
                  onMouseUp={() => setSelectedText(window.getSelection()?.toString().trim() ?? "")}
                >
                  {segments.map((s, idx) =>
                    s.ids.length === 0 ? (
                      <span key={idx}>{s.text}</span>
                    ) : (
                      <mark
                        key={idx}
                        ref={idx === firstSelected ? (el) => { scrollTarget.current = el; } : undefined}
                        onClick={() => setSelected(s.ids[s.ids.length - 1])}
                        className={cn(
                          "cursor-pointer rounded-sm px-0.5 text-foreground",
                          selected && s.ids.includes(selected)
                            ? "bg-primary/35 ring-1 ring-primary"
                            : MARK[byKey.get(s.ids[s.ids.length - 1])?.data.validation_state ?? "unverified"],
                        )}
                      >
                        {s.text}
                      </mark>
                    ),
                  )}
                </pre>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
