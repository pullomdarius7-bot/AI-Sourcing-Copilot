"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AnalysisBadge, StatusBadge } from "@/components/status-badge";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import { ACTIVE_STATUSES, formatBytes, pipelineSteps, type StepState } from "@/lib/docs";
import type { Analysis, DocumentDetail, ExtractionRun, Field } from "@/lib/types";
import { RunStatusBadge } from "@/components/review-badges";
import { cn, formatDate } from "@/lib/utils";

const STEP_STYLE: Record<StepState, string> = {
  pending: "border-input text-muted-foreground",
  queued: "border-caution text-caution",
  running: "border-primary bg-primary/10 text-primary animate-pulse",
  done: "border-primary bg-primary text-primary-foreground",
  failed: "border-destructive bg-destructive text-destructive-foreground",
  skipped: "border-input bg-secondary text-muted-foreground",
  review: "border-caution bg-caution text-white",
};
const STEP_TEXT: Record<StepState, string> = {
  pending: "Waiting",
  queued: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
  skipped: "Skipped",
  review: "Review",
};

function Value<T>({ field, format }: { field: Field<T>; format?: (v: T) => string }) {
  if (field.unknown || field.value === null) {
    return <span className="text-caution">Not stated</span>;
  }
  return (
    <span title={field.source ? `Source: “${field.source}”` : undefined}>
      {format ? format(field.value) : String(field.value)}
      <span className="ml-2 text-xs text-muted-foreground">{Math.round(field.confidence * 100)}%</span>
    </span>
  );
}

function AnalysisCard({ analysis }: { analysis: Analysis }) {
  const r = analysis.result;
  const rows: [string, Field<string>][] = [
    ["Supplier", r.supplier_name],
    ["Currency", r.currency],
    ["Incoterms", r.incoterms],
    ["Payment terms", r.payment_terms],
    ["Valid until", r.valid_until],
  ];
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>AI analysis</CardTitle>
          <div className="flex gap-2">
            {analysis.needs_review && <Badge variant="caution">Needs review</Badge>}
            <Badge>{r.document_type.replace("_", " ")}</Badge>
          </div>
        </div>
        <CardDescription>
          {analysis.provider} · {analysis.model} · {formatDate(analysis.created_at)}. Every value is unverified until a
          person confirms it. Hover a value to see the quote it came from.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm">{r.summary}</p>
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {rows.map(([label, field]) => (
            <div key={label} className="flex justify-between gap-3 border-b py-1">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="text-right">
                <Value field={field} />
              </dd>
            </div>
          ))}
        </dl>
        {r.line_items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-2 pr-4 font-medium">Item</th>
                  <th className="py-2 pr-4 font-medium">Qty</th>
                  <th className="py-2 pr-4 font-medium">Unit</th>
                  <th className="py-2 pr-4 font-medium">Unit price</th>
                  <th className="py-2 pr-4 font-medium">Total</th>
                  <th className="py-2 font-medium">Lead time</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {r.line_items.map((it, i) => (
                  <tr key={i}>
                    <td className="py-2 pr-4" title={`Source: “${it.source}”`}>
                      {it.description}
                    </td>
                    <td className="py-2 pr-4"><Value field={it.quantity} /></td>
                    <td className="py-2 pr-4"><Value field={it.unit} /></td>
                    <td className="py-2 pr-4"><Value field={it.unit_price} /></td>
                    <td className="py-2 pr-4"><Value field={it.total_price} /></td>
                    <td className="py-2"><Value field={it.lead_time_days} format={(v) => `${v} d`} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {analysis.flags.length > 0 && (
          <div>
            <p className="mb-2 text-sm font-medium">Guardrail notes</p>
            <div className="flex flex-wrap gap-2">
              {analysis.flags.map((f) => (
                <Badge key={f} variant="caution" className="font-mono">
                  {f}
                </Badge>
              ))}
            </div>
          </div>
        )}
        {r.missing_fields.length > 0 && (
          <p className="text-xs text-muted-foreground">Not stated in the document: {r.missing_fields.join(", ")}</p>
        )}
      </CardContent>
    </Card>
  );
}

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const me = useMe();
  const { data: doc, error, reload } = useFetch<DocumentDetail>(`documents/${id}`);
  const analysisPath = doc && doc.analysis_status !== "pending" && doc.analysis_status !== "skipped" && doc.status !== "quarantined" ? `documents/${id}/analysis` : null;
  const { data: analysis } = useFetch<Analysis>(analysisPath);
  const [actionError, setActionError] = useState<string | null>(null);
  const runs = useFetch<ExtractionRun[]>(`extraction-runs?document_id=${id}&limit=5`);
  const latestRun = runs.data?.[0];

  const active = doc ? ACTIVE_STATUSES.includes(doc.status) : false;
  const extracting = latestRun?.status === "extracting";
  useEffect(() => {
    if (!active && !extracting) return;
    const t = setInterval(() => {
      reload();
      runs.reload();
    }, 2500);
    return () => clearInterval(t);
  }, [active, extracting, reload, runs.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(fn: () => Promise<void>) {
    setActionError(null);
    try {
      await fn();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  }

  if (error) return <Alert variant="error">{error.message}</Alert>;
  if (!doc) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const steps = pipelineSteps(doc);
  const canDownload = doc.scan_status === "clean";
  const canReprocess = me.permissions.includes("documents:upload") && (doc.status === "failed" || doc.status === "processed");
  const canDelete = me.permissions.includes("documents:delete");
  const metadata = Object.entries(doc.file_metadata).filter(([, v]) => typeof v !== "object" || v === null);
  const sheets = Array.isArray(doc.file_metadata.sheets) ? (doc.file_metadata.sheets as { name: string; rows: number; columns: number }[]) : [];

  return (
    <div className="space-y-6">
      <div>
        <Link href="/documents" className="text-sm text-primary hover:underline">
          ← All documents
        </Link>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold">{doc.filename}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <StatusBadge status={doc.status} />
              {doc.status !== "quarantined" && <AnalysisBadge status={doc.analysis_status} />}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {canDownload && (
              <Button asChild variant="outline">
                <a href={`/api/documents/${doc.id}/download`}>Download</a>
              </Button>
            )}
            {canReprocess && (
              <Button variant="outline" onClick={() => act(async () => { await api(`documents/${doc.id}/reprocess`, { method: "POST" }); reload(); })}>
                Reprocess
              </Button>
            )}
            {canDelete && (
              <Button
                variant="destructive"
                onClick={() => {
                  if (!window.confirm("Delete this document and its stored file?")) return;
                  void act(async () => {
                    await api(`documents/${doc.id}`, { method: "DELETE" });
                    router.replace("/documents");
                  });
                }}
              >
                Delete
              </Button>
            )}
          </div>
        </div>
      </div>

      {actionError && <Alert variant="error">{actionError}</Alert>}
      {doc.status === "quarantined" && (
        <Alert variant="error">
          This file was blocked by the virus scan ({doc.scan_detail}). It has been removed from storage and cannot be downloaded or analyzed.
        </Alert>
      )}
      {doc.status === "failed" && doc.error && <Alert variant="error">Processing failed: {doc.error}</Alert>}
      {doc.status === "processed" && doc.analysis_status === "failed" && doc.error && (
        <Alert variant="error">AI analysis failed: {doc.error}. The file itself was scanned and parsed successfully.</Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Processing status</CardTitle>
          <CardDescription>{active ? "Updating live." : `Last updated ${formatDate(doc.updated_at)}.`}</CardDescription>
        </CardHeader>
        <CardContent>
          <ol className="grid gap-3 md:grid-cols-4">
            {steps.map((s, i) => (
              <li key={s.key} className="flex gap-3 md:block">
                <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs font-semibold", STEP_STYLE[s.state])} aria-hidden>
                  {i + 1}
                </span>
                <div className="md:mt-2">
                  <p className="text-sm font-medium">{s.label}</p>
                  <p className="text-xs text-muted-foreground">{STEP_TEXT[s.state]}</p>
                  {s.detail && <p className="mt-1 break-words text-xs text-muted-foreground">{s.detail}</p>}
                </div>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      {analysis && <AnalysisCard analysis={analysis} />}

      {doc.scan_status === "clean" && doc.text_preview && (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle>Requirements</CardTitle>
              {latestRun && <RunStatusBadge status={latestRun.status} />}
            </div>
            <CardDescription>
              Extract the requirements and their parameters. Every value is tied to a quote in this document, shows how
              confident the check is, and stays unverified until a person reviews it.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {latestRun && (
              <p className="text-sm text-muted-foreground">
                Latest extraction: {latestRun.counts.requirements} requirements, {latestRun.counts.parameters} parameters,{" "}
                {latestRun.counts.unresolved} awaiting review.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {latestRun && (
                <Button asChild variant="outline">
                  <Link href={`/requirements/${latestRun.id}`}>Open in evidence viewer</Link>
                </Button>
              )}
              {me.permissions.includes("requirements:extract") && (
                <Button
                  disabled={extracting}
                  onClick={() => act(async () => { await api(`documents/${doc.id}/requirements/extract`, { method: "POST" }); runs.reload(); })}
                >
                  {extracting ? "Extracting…" : latestRun ? "Extract again" : "Extract requirements"}
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>File</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-2 text-sm">
              {[
                ["Type", doc.content_type],
                ["Size", formatBytes(doc.size_bytes)],
                ["Uploaded", formatDate(doc.created_at)],
                ["Storage", doc.storage_backend ?? "removed"],
                ["Virus scan", doc.scan_status === "pending" ? "Pending" : `${doc.scan_status} (${doc.scan_engine ?? "n/a"})`],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="text-right">{v}</dd>
                </div>
              ))}
              <div>
                <dt className="text-muted-foreground">SHA-256</dt>
                <dd className="break-all font-mono text-xs">{doc.sha256}</dd>
              </div>
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Extracted metadata</CardTitle>
          </CardHeader>
          <CardContent>
            {metadata.length === 0 && sheets.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing extracted yet.</p>
            ) : (
              <dl className="space-y-2 text-sm">
                {metadata.map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt>
                    <dd className="break-words text-right">{String(v)}</dd>
                  </div>
                ))}
                {sheets.map((s) => (
                  <div key={s.name} className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">Sheet “{s.name}”</dt>
                    <dd>{s.rows} rows × {s.columns} cols</dd>
                  </div>
                ))}
              </dl>
            )}
          </CardContent>
        </Card>
      </div>

      {doc.text_preview && (
        <Card>
          <CardHeader>
            <CardTitle>Text preview</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 font-mono text-xs">{doc.text_preview}</pre>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Background jobs</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="divide-y text-sm">
            {doc.jobs.map((j) => (
              <li key={j.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="font-mono text-xs">{j.kind}</span>
                <span className="flex items-center gap-2">
                  <Badge variant={j.status === "dead" ? "destructive" : j.status === "succeeded" ? "default" : "caution"}>{j.status}</Badge>
                  <span className="text-xs text-muted-foreground">attempt {j.attempts}/{j.max_attempts}</span>
                </span>
                {j.last_error && <p className="w-full break-words text-xs text-destructive">{j.last_error}</p>}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
