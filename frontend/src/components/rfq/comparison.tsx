"use client";

import { Fragment, useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { ComparisonBrief, ComparisonRecord, Rfq } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

const CRITERIA = [
  ["price", "Price"],
  ["lead_time", "Lead time"],
  ["coverage", "Coverage"],
  ["qualification", "Qualification"],
] as const;
const DEFAULTS = { price: 50, lead_time: 20, coverage: 20, qualification: 10 };
const money = (v: number | null, c: string) => (v == null ? "unknown" : `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${c}`);

function Bar({ value }: { value: number | null }) {
  if (value == null) return <span className="text-xs text-muted-foreground">n/a</span>;
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-secondary"><div className="h-full bg-primary" style={{ width: `${value * 100}%` }} /></div>
      <span className="w-7 text-right text-xs tabular-nums">{Math.round(value * 100)}</span>
    </div>
  );
}

export function ComparisonPanel({ rfq, canRun, canAward, onAwarded }: { rfq: Rfq; canRun: boolean; canAward: boolean; onAwarded: () => void }) {
  const list = useFetch<ComparisonBrief[]>(`rfqs/${rfq.id}/comparisons`);
  const [selected, setSelected] = useState<string | null>(rfq.award_comparison_id);
  const current = useFetch<ComparisonRecord>(selected ? `comparisons/${selected}` : null);
  const [weights, setWeights] = useState<Record<string, number>>(DEFAULTS);
  const [awardTo, setAwardTo] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    if (!selected && list.data && list.data.length > 0) setSelected(list.data[0].id);
  }, [list.data, selected]);

  async function run() {
    setError(null);
    try {
      const res = await api<ComparisonRecord>(`rfqs/${rfq.id}/comparisons`, { body: { weights } });
      setSelected(res.id);
      list.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function award() {
    setError(null);
    try {
      await api(`rfqs/${rfq.id}/award`, { body: { supplier_id: awardTo, comparison_id: selected, note } });
      onAwarded();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const c = current.data;
  const r = c?.result;
  const top = r?.highest_score_supplier_id;
  const totalWeight = Object.values(weights).reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-5">
      {error && <Alert variant="error">{error}</Alert>}
      {canRun && ["sent", "closed"].includes(rfq.status) && (
        <div className="space-y-3 rounded-md border p-3">
          <p className="text-sm font-medium">Weights</p>
          <div className="grid gap-3 md:grid-cols-4">
            {CRITERIA.map(([key, label]) => (
              <div key={key} className="space-y-1">
                <Label htmlFor={`w-${key}`}>{label}: {totalWeight ? Math.round((weights[key] / totalWeight) * 100) : 0}%</Label>
                <input id={`w-${key}`} type="range" min={0} max={100} step={5} value={weights[key]} className="w-full accent-[hsl(var(--primary))]"
                  onChange={(e) => setWeights({ ...weights, [key]: Number(e.target.value) })} />
              </div>
            ))}
          </div>
          <Button onClick={run} disabled={totalWeight === 0}>Run comparison</Button>
        </div>
      )}

      {list.data && list.data.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor="cmp-pick">Comparison</Label>
          <Select id="cmp-pick" className="w-auto" value={selected ?? ""} onChange={(e) => setSelected(e.target.value)}>
            {list.data.map((b) => (
              <option key={b.id} value={b.id}>
                {formatDate(b.created_at)} · price {Math.round((b.weights.price ?? 0) * 100)}%{b.stale ? " · outdated" : ""}{b.id === rfq.award_comparison_id ? " · used for award" : ""}
              </option>
            ))}
          </Select>
          {c && (
            <>
              <Button asChild size="sm" variant="outline"><a href={`/api/comparisons/${c.id}/pdf`}>PDF</a></Button>
              <Button asChild size="sm" variant="outline"><a href={`/api/comparisons/${c.id}/xlsx`}>Excel</a></Button>
            </>
          )}
        </div>
      )}
      {list.data?.length === 0 && <p className="text-sm text-muted-foreground">No comparison yet. Verified quotes are compared; drafts are listed as not scored.</p>}

      {c && r && (
        <div className="space-y-4">
          {c.stale && <Alert variant="error">Quotes or supplier qualification changed after this comparison. Run it again before deciding.</Alert>}
          <Alert variant="info">{r.decision_note}</Alert>
          {r.global_flags.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-caution">{r.global_flags.map((f) => <li key={f}>{f}</li>)}</ul>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-2 pr-3 font-medium">#</th>
                  <th className="py-2 pr-3 font-medium">Supplier</th>
                  <th className="py-2 pr-3 text-right font-medium">Score</th>
                  {CRITERIA.map(([k, l]) => <th key={k} className="py-2 pr-3 font-medium">{l}</th>)}
                  <th className="py-2 pr-3 text-right font-medium">Basket</th>
                  <th className="py-2 font-medium">Flags</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {r.ranking.map((row) => (
                  <Fragment key={row.supplier_id}>
                    <tr className={cn(rfq.awarded_supplier_id === row.supplier_id && "bg-primary/5")}>
                      <td className="py-2 pr-3 tabular-nums">{row.rank}</td>
                      <td className="py-2 pr-3">
                        <button type="button" className="text-left font-medium text-primary hover:underline" onClick={() => setOpen(open === row.supplier_id ? null : row.supplier_id)} aria-expanded={open === row.supplier_id}>
                          {row.supplier_name}
                        </button>
                        {rfq.awarded_supplier_id === row.supplier_id && <Badge className="ml-2">Awarded</Badge>}
                        {!row.qualified && <Badge variant="destructive" className="ml-2">not qualified</Badge>}
                      </td>
                      <td className="py-2 pr-3 text-right font-semibold tabular-nums">{row.score.toFixed(1)}</td>
                      {CRITERIA.map(([k]) => <td key={k} className="py-2 pr-3"><Bar value={row.criteria[k].score} /></td>)}
                      <td className="py-2 pr-3 text-right tabular-nums">{money(row.basket_total, r.currency)}</td>
                      <td className="py-2">
                        <div className="flex flex-wrap gap-1">{row.flags.map((f) => <Badge key={f} variant="caution" className="font-mono">{f}</Badge>)}</div>
                      </td>
                    </tr>
                    {open === row.supplier_id && (
                      <tr>
                        <td />
                        <td colSpan={8} className="pb-3 text-xs text-muted-foreground">
                          <ul className="space-y-0.5">{row.explanation.map((x) => <li key={x}>{x}</li>)}</ul>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>

          <details className="rounded-md border px-3 py-2 text-sm">
            <summary className="cursor-pointer font-medium">Line prices ({r.currency}, unit price × requested quantity)</summary>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Line</th>
                    {r.ranking.map((row) => <th key={row.supplier_id} className="py-1 pr-3 text-right font-medium">{row.supplier_name}</th>)}
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {r.items.map((it) => (
                    <tr key={it.item_id}>
                      <td className="py-1 pr-3">{it.line}. {it.statement.slice(0, 60)}{r.basket_item_ids.includes(it.item_id) ? "" : " *"}</td>
                      {r.ranking.map((row) => {
                        const cell = it.cells[row.supplier_id];
                        return (
                          <td key={row.supplier_id} className="py-1 pr-3 text-right tabular-nums">
                            {cell?.comparable_total != null ? cell.comparable_total.toLocaleString(undefined, { maximumFractionDigits: 2 }) : <span className="text-xs text-muted-foreground">{cell?.flags.join(", ") || "not quoted"}</span>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1 text-xs text-muted-foreground">* not priced by every supplier, so not part of the price comparison.</p>
            </div>
          </details>

          {r.excluded.length > 0 && (
            <div className="text-sm">
              <p className="font-medium">Not scored</p>
              <ul className="text-muted-foreground">{r.excluded.map((e) => <li key={e.supplier_id}>{e.supplier_name}: {e.detail}</li>)}</ul>
            </div>
          )}

          {rfq.status === "awarded" && (
            <Alert variant="success">Awarded {formatDate(rfq.awarded_at)}: {rfq.award_note}</Alert>
          )}
          {canAward && ["sent", "closed"].includes(rfq.status) && !c.stale && r.ranking.length > 0 && (
            <div className="space-y-2 rounded-md border p-3">
              <p className="text-sm font-medium">Award</p>
              <p className="text-xs text-muted-foreground">The award is your decision. Record why, especially when it is not the highest score.</p>
              <Select aria-label="Supplier to award" className="max-w-sm" value={awardTo} onChange={(e) => setAwardTo(e.target.value)}>
                <option value="">Choose a supplier…</option>
                {r.ranking.map((row) => <option key={row.supplier_id} value={row.supplier_id}>{row.rank}. {row.supplier_name} ({row.score.toFixed(1)})</option>)}
              </Select>
              {awardTo && awardTo !== top && <p className="text-sm text-caution">This is not the highest-scoring supplier. Your reason will be recorded with the award.</p>}
              <Textarea aria-label="Reason for the award" placeholder="Reason for the award (at least 10 characters)" value={note} onChange={(e) => setNote(e.target.value)} />
              <Button disabled={!awardTo || note.trim().length < 10} onClick={award}>Award RFQ</Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
