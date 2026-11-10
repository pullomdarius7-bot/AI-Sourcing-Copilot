"use client";

import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { DocumentItem, Quote, QuoteLine, Rfq } from "@/lib/types";
import { cn } from "@/lib/utils";

type LineDraft = { description: string; quantity: string; unit: string; unit_price: string; total_price: string; lead_time_days: string; rfq_item_id: string };
const EMPTY_LINE: LineDraft = { description: "", quantity: "", unit: "", unit_price: "", total_price: "", lead_time_days: "", rfq_item_id: "" };
const num = (v: string) => (v.trim() === "" ? null : Number(v));
const fmt = (v: number | null) => (v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 4 }));

function toBody(lines: LineDraft[]) {
  return lines.filter((l) => l.description.trim()).map((l) => ({
    description: l.description, quantity: num(l.quantity), unit: l.unit || null, unit_price: num(l.unit_price),
    total_price: num(l.total_price), lead_time_days: num(l.lead_time_days), rfq_item_id: l.rfq_item_id || null,
  }));
}

function LinesEditor({ rfq, lines, setLines }: { rfq: Rfq; lines: LineDraft[]; setLines: (l: LineDraft[]) => void }) {
  const update = (i: number, k: keyof LineDraft, v: string) => setLines(lines.map((l, n) => (n === i ? { ...l, [k]: v } : l)));
  return (
    <div className="space-y-2">
      {lines.map((l, i) => (
        <div key={i} className="grid gap-2 md:grid-cols-[1fr_6rem_5rem_7rem_7rem_6rem_12rem_auto]">
          <Input aria-label="Description" placeholder="Description as quoted" value={l.description} onChange={(e) => update(i, "description", e.target.value)} />
          <Input aria-label="Quantity" type="number" step="any" min={0} placeholder="Qty" value={l.quantity} onChange={(e) => update(i, "quantity", e.target.value)} />
          <Input aria-label="Unit" placeholder="Unit" value={l.unit} onChange={(e) => update(i, "unit", e.target.value)} />
          <Input aria-label="Unit price" type="number" step="any" min={0} placeholder="Unit price" value={l.unit_price} onChange={(e) => update(i, "unit_price", e.target.value)} />
          <Input aria-label="Line total" type="number" step="any" min={0} placeholder="Line total" value={l.total_price} onChange={(e) => update(i, "total_price", e.target.value)} />
          <Input aria-label="Lead time days" type="number" step="any" min={0} placeholder="Days" value={l.lead_time_days} onChange={(e) => update(i, "lead_time_days", e.target.value)} />
          <Select aria-label="Answers RFQ line" value={l.rfq_item_id} onChange={(e) => update(i, "rfq_item_id", e.target.value)}>
            <option value="">Suggest a match</option>
            {rfq.items.map((it) => <option key={it.id} value={it.id}>Line {it.line}: {it.statement.slice(0, 40)}</option>)}
          </Select>
          <Button type="button" size="sm" variant="ghost" aria-label="Remove line" onClick={() => setLines(lines.filter((_, n) => n !== i))}>✕</Button>
        </div>
      ))}
      <Button type="button" size="sm" variant="outline" onClick={() => setLines([...lines, { ...EMPTY_LINE }])}>Add line</Button>
    </div>
  );
}

function NewQuote({ rfq, quotedIds, onCreated }: { rfq: Rfq; quotedIds: Set<string>; onCreated: () => void }) {
  const docs = useFetch<{ items: DocumentItem[] }>("documents?status=processed&limit=100");
  const [mode, setMode] = useState<"manual" | "document">("manual");
  const [supplier, setSupplier] = useState("");
  const [header, setHeader] = useState({ currency: rfq.currency, incoterms: "", payment_terms: "", valid_until: "", notes: "" });
  const [lines, setLines] = useState<LineDraft[]>(rfq.items.map((it) => ({ ...EMPTY_LINE, description: it.statement, rfq_item_id: it.id, quantity: it.quantity?.toString() ?? "", unit: it.unit ?? "" })));
  const [docId, setDocId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const candidates = rfq.suppliers.filter((s) => !quotedIds.has(s.supplier_id));
  const analysed = (docs.data?.items ?? []).filter((d) => ["succeeded", "needs_review"].includes(d.analysis_status));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      if (mode === "document") {
        await api(`rfqs/${rfq.id}/quotes/from-document`, { body: { supplier_id: supplier, document_id: docId } });
      } else {
        await api(`rfqs/${rfq.id}/quotes`, {
          body: {
            supplier_id: supplier, currency: header.currency || null, incoterms: header.incoterms || null,
            payment_terms: header.payment_terms || null, valid_until: header.valid_until || null, notes: header.notes || null,
            lines: toBody(lines),
          },
        });
      }
      onCreated();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (candidates.length === 0) return <p className="text-sm text-muted-foreground">Every invited supplier has a quote recorded.</p>;
  return (
    <form onSubmit={submit} className="space-y-3 rounded-md border p-3">
      {error && <Alert variant="error">{error}</Alert>}
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="q-supplier">Supplier</Label>
          <Select id="q-supplier" className="w-64" value={supplier} onChange={(e) => setSupplier(e.target.value)} required>
            <option value="">Choose…</option>
            {candidates.map((s) => <option key={s.supplier_id} value={s.supplier_id}>{s.name}</option>)}
          </Select>
        </div>
        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-1"><input type="radio" checked={mode === "manual"} onChange={() => setMode("manual")} /> Enter by hand</label>
          <label className="flex items-center gap-1"><input type="radio" checked={mode === "document"} onChange={() => setMode("document")} /> From an analysed document</label>
        </div>
      </div>
      {mode === "document" ? (
        <div className="space-y-1">
          <Label htmlFor="q-doc">Quote document</Label>
          <Select id="q-doc" value={docId} onChange={(e) => setDocId(e.target.value)} required>
            <option value="">Choose a document with a completed AI analysis…</option>
            {analysed.map((d) => <option key={d.id} value={d.id}>{d.filename}{d.analysis_status === "needs_review" ? " (flagged for review)" : ""}</option>)}
          </Select>
          <p className="text-xs text-muted-foreground">Only values the analysis tied to a quote in the document are copied. Anything it could not find stays empty for you to fill in.</p>
        </div>
      ) : (
        <>
          <div className="grid gap-2 md:grid-cols-5">
            <Input aria-label="Currency" placeholder="Currency" maxLength={3} value={header.currency} onChange={(e) => setHeader({ ...header, currency: e.target.value })} />
            <Input aria-label="Incoterms" placeholder="Incoterms" maxLength={3} value={header.incoterms} onChange={(e) => setHeader({ ...header, incoterms: e.target.value })} />
            <Input aria-label="Payment terms" placeholder="Payment terms" value={header.payment_terms} onChange={(e) => setHeader({ ...header, payment_terms: e.target.value })} />
            <Input aria-label="Valid until" type="date" value={header.valid_until} onChange={(e) => setHeader({ ...header, valid_until: e.target.value })} />
            <Input aria-label="Notes" placeholder="Notes" value={header.notes} onChange={(e) => setHeader({ ...header, notes: e.target.value })} />
          </div>
          <LinesEditor rfq={rfq} lines={lines} setLines={setLines} />
        </>
      )}
      <Button type="submit" disabled={!supplier || (mode === "document" && !docId)}>Record quote</Button>
    </form>
  );
}

function QuoteCard({ rfq, quote, canEdit, onChanged }: { rfq: Rfq; quote: Quote; canEdit: boolean; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [editingLines, setEditingLines] = useState(false);
  const [lines, setLines] = useState<LineDraft[]>([]);
  const draft = quote.status === "draft";
  const open = canEdit && ["sent", "closed"].includes(rfq.status);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const itemLabel = (id: string | null) => {
    const it = rfq.items.find((i) => i.id === id);
    return it ? `Line ${it.line}` : "—";
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>{quote.supplier_name}</CardTitle>
          <div className="flex gap-2">
            {quote.source === "analysis" && <Badge variant="neutral">from document</Badge>}
            <Badge variant={draft ? "caution" : "default"}>{draft ? "Draft – not used in comparison" : "Verified"}</Badge>
          </div>
        </div>
        <CardDescription>
          {[quote.currency ?? "currency not stated", quote.incoterms ?? "Incoterms not stated", quote.payment_terms ?? "payment terms not stated",
            quote.valid_until ? `valid until ${quote.valid_until}` : "validity not stated"].join(" · ")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {quote.notes && <p className="whitespace-pre-line text-sm text-muted-foreground">{quote.notes}</p>}
        {editingLines ? (
          <div className="space-y-2">
            <LinesEditor rfq={rfq} lines={lines} setLines={setLines} />
            <div className="flex gap-2">
              <Button size="sm" onClick={() => act(async () => {
                await api(`quotes/${quote.id}/lines`, { method: "PUT", body: { lines: toBody(lines) } });
                setEditingLines(false);
              })}>Save lines</Button>
              <Button size="sm" variant="ghost" onClick={() => setEditingLines(false)}>Cancel</Button>
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3 font-medium">Quoted line</th>
                  <th className="py-1 pr-3 text-right font-medium">Qty</th>
                  <th className="py-1 pr-3 text-right font-medium">Unit price</th>
                  <th className="py-1 pr-3 text-right font-medium">Total</th>
                  <th className="py-1 pr-3 text-right font-medium">Days</th>
                  <th className="py-1 font-medium">Answers</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {quote.lines.map((l: QuoteLine) => (
                  <tr key={l.id} className={cn(l.match_status === "suggested" && "bg-caution/5")}>
                    <td className="py-2 pr-3">
                      <span title={l.source_quote ? `Source: “${l.source_quote}”` : undefined}>{l.description}</span>
                      {quote.source === "analysis" && <span className="ml-1 text-xs text-muted-foreground">({Math.round(l.confidence * 100)}%)</span>}
                    </td>
                    <td className="py-2 pr-3 text-right">{fmt(l.quantity)} {l.unit ?? ""}</td>
                    <td className="py-2 pr-3 text-right">{fmt(l.unit_price)}</td>
                    <td className="py-2 pr-3 text-right">{fmt(l.total_price)}</td>
                    <td className="py-2 pr-3 text-right">{fmt(l.lead_time_days)}</td>
                    <td className="py-2">
                      {draft && open ? (
                        <div className="flex items-center gap-1">
                          <Select aria-label="Answers RFQ line" className="h-8 w-40" value={l.rfq_item_id ?? ""} onChange={(e) => act(() => api(`quotes/${quote.id}/lines/${l.id}/match`, { body: { rfq_item_id: e.target.value || null } }))}>
                            <option value="">Not an RFQ line</option>
                            {rfq.items.map((it) => <option key={it.id} value={it.id}>Line {it.line}</option>)}
                          </Select>
                          {l.match_status === "suggested" && (
                            <Button size="sm" variant="outline" className="h-8" onClick={() => act(() => api(`quotes/${quote.id}/lines/${l.id}/match`, { body: { rfq_item_id: l.rfq_item_id } }))}>Confirm</Button>
                          )}
                        </div>
                      ) : (
                        <span>{itemLabel(l.rfq_item_id)} {l.match_status !== "confirmed" && <span className="text-xs text-muted-foreground">({l.match_status})</span>}</span>
                      )}
                    </td>
                  </tr>
                ))}
                {quote.lines.length === 0 && <tr><td colSpan={6} className="py-4 text-center text-muted-foreground">No lines.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        {open && (
          <div className="flex flex-wrap items-center gap-2">
            {draft && !editingLines && (
              <>
                <Button size="sm" onClick={() => act(() => api(`quotes/${quote.id}/verify`, { method: "POST" }))}>Verify quote</Button>
                <Button size="sm" variant="outline" onClick={() => {
                  setLines(quote.lines.map((l) => ({
                    description: l.description, quantity: l.quantity?.toString() ?? "", unit: l.unit ?? "", unit_price: l.unit_price?.toString() ?? "",
                    total_price: l.total_price?.toString() ?? "", lead_time_days: l.lead_time_days?.toString() ?? "",
                    rfq_item_id: l.match_status === "confirmed" ? l.rfq_item_id ?? "" : "",
                  })));
                  setEditingLines(true);
                }}>Edit lines</Button>
                <span className="text-xs text-muted-foreground">Verifying confirms you checked every value against the supplier&rsquo;s quote.</span>
              </>
            )}
            {!draft && (
              <>
                <Input aria-label="Reason to reopen" placeholder="Reason to reopen" className="max-w-xs" value={reason} onChange={(e) => setReason(e.target.value)} />
                <Button size="sm" variant="outline" disabled={reason.trim().length < 3} onClick={() => act(() => api(`quotes/${quote.id}/reopen`, { body: { reason } }))}>Reopen</Button>
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function Quotes({ rfq, canEdit, onChanged }: { rfq: Rfq; canEdit: boolean; onChanged: () => void }) {
  const quotes = useFetch<Quote[]>(`rfqs/${rfq.id}/quotes`);
  const [adding, setAdding] = useState(false);
  const reload = () => {
    quotes.reload();
    onChanged();
  };
  return (
    <div className="space-y-4">
      {quotes.data?.map((q) => <QuoteCard key={q.id} rfq={rfq} quote={q} canEdit={canEdit} onChanged={reload} />)}
      {quotes.data?.length === 0 && <p className="text-sm text-muted-foreground">No quotes recorded yet.</p>}
      {canEdit && rfq.status === "sent" && (adding ? (
        <NewQuote rfq={rfq} quotedIds={new Set((quotes.data ?? []).map((q) => q.supplier_id))} onCreated={() => { setAdding(false); reload(); }} />
      ) : (
        <Button variant="outline" onClick={() => setAdding(true)}>Record a quote</Button>
      ))}
    </div>
  );
}
