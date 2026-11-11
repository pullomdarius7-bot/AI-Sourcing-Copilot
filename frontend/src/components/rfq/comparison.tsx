"use client";

import { Fragment, useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { ComparisonBrief, ComparisonRecord, Deviation, RankingRow, Recommendation, Rfq, Severity } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

const CRITERIA = [
  ["price", "Price"],
  ["lead_time", "Lead time"],
  ["coverage", "Coverage"],
  ["qualification", "Qualification"],
  ["risk", "Risk"],
] as const;
const DEFAULTS = { price: 45, lead_time: 20, coverage: 15, qualification: 10, risk: 10 };
const SEVERITY_VARIANT: Record<Severity, "destructive" | "caution" | "neutral"> = { critical: "destructive", major: "caution", minor: "neutral", info: "neutral" };
const money = (v: number | null | undefined, c: string) => (v == null ? "unknown" : `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${c}`);
const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));

function Bar({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-xs text-muted-foreground">n/a</span>;
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-12 overflow-hidden rounded-full bg-secondary"><div className="h-full bg-primary" style={{ width: `${value * 100}%` }} /></div>
      <span className="w-7 text-right text-xs tabular-nums">{Math.round(value * 100)}</span>
    </div>
  );
}

function RiskBadge({ row }: { row: RankingRow }) {
  if (!row.risk) return <span className="text-xs text-muted-foreground">—</span>;
  const variant = row.risk.level === "high" ? "destructive" : row.risk.level === "medium" ? "caution" : "neutral";
  return <Badge variant={variant} title={row.risk.factors.join("\n") || "No deviations"}>{row.risk.score} {row.risk.level}</Badge>;
}

function DeviationList({ deviations }: { deviations: Deviation[] }) {
  const shown = deviations.filter((d) => d.severity !== "info");
  const info = deviations.filter((d) => d.severity === "info");
  return (
    <div className="space-y-1">
      {shown.length === 0 && <p className="text-xs text-muted-foreground">No deviations from the request.</p>}
      <ul className="space-y-1">
        {shown.map((d, i) => (
          <li key={`${d.code}-${d.line}-${i}`} className="flex items-start gap-2 text-xs">
            <Badge variant={SEVERITY_VARIANT[d.severity]} className="shrink-0">{d.severity}</Badge>
            <span>{d.message}</span>
          </li>
        ))}
      </ul>
      {info.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-muted-foreground">{info.map((d, i) => <li key={`${d.code}-${i}`}>{d.message}</li>)}</ul>
      )}
    </div>
  );
}

function Recommendations({ rfq, record, canRecommend, canApprove, meId, onDecided }: {
  rfq: Rfq; record: ComparisonRecord | null; canRecommend: boolean; canApprove: boolean; meId: string; onDecided: () => void;
}) {
  const recs = useFetch<Recommendation[]>(`rfqs/${rfq.id}/recommendations`);
  const [supplier, setSupplier] = useState("");
  const [rationale, setRationale] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const pending = recs.data?.find((r) => r.status === "pending");
  const r = record?.result;
  const open = ["sent", "closed"].includes(rfq.status);

  async function act(fn: () => Promise<unknown>, done?: () => void) {
    setError(null);
    try {
      await fn();
      done?.();
      recs.reload();
      onDecided();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-3 rounded-md border p-3">
      <div>
        <p className="text-sm font-medium">Award recommendation and approval</p>
        <p className="text-xs text-muted-foreground">A buyer recommends a supplier from a current comparison; a different person with approval rights decides. Only an approved recommendation awards the RFQ.</p>
      </div>
      {error && <Alert variant="error">{error}</Alert>}

      {pending && (
        <div className="space-y-2 rounded-md bg-secondary/40 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="caution">Waiting for approval</Badge>
            <span className="font-medium">{pending.supplier_name}</span>
            {pending.snapshot.rank != null && <span className="text-muted-foreground">rank {pending.snapshot.rank}, score {pending.snapshot.score?.toFixed(1)}</span>}
            {pending.snapshot.risk && <span className="text-muted-foreground">· risk {pending.snapshot.risk.score} {pending.snapshot.risk.level}</span>}
            {pending.snapshot.basket_total != null && <span className="text-muted-foreground">· {money(pending.snapshot.basket_total, pending.snapshot.currency ?? rfq.currency)}</span>}
          </div>
          <p className="whitespace-pre-line">{pending.rationale}</p>
          <p className="text-xs text-muted-foreground">Recommended by {pending.submitted_by_email ?? "unknown"} on {formatDate(pending.submitted_at)}.</p>
          {pending.not_top_ranked && <p className="text-sm text-caution">This is not the highest-scoring supplier. Approval needs a recorded reason.</p>}
          {pending.comparison_stale && <Alert variant="error">Quotes, rates, unit rules or qualifications changed after this comparison. It can no longer be approved; reject or withdraw it and recommend again.</Alert>}
          {(pending.snapshot.deviations?.length ?? 0) > 0 && (
            <div>
              <p className="text-xs font-medium">Critical and major deviations at the time of recommendation</p>
              <DeviationList deviations={pending.snapshot.deviations ?? []} />
            </div>
          )}
          {canApprove && pending.submitted_by !== meId && (
            <div className="space-y-2">
              <Textarea aria-label="Decision note" placeholder={pending.not_top_ranked ? "Why you approve a lower-ranked supplier (required)" : "Decision note (required to reject)"}
                value={notes[pending.id] ?? ""} onChange={(e) => setNotes({ ...notes, [pending.id]: e.target.value })} />
              <div className="flex gap-2">
                <Button size="sm" disabled={pending.comparison_stale || (pending.not_top_ranked && !(notes[pending.id] ?? "").trim())}
                  onClick={() => act(() => api(`recommendations/${pending.id}/approve`, { body: { note: notes[pending.id] || null } }))}>Approve and award</Button>
                <Button size="sm" variant="outline" disabled={(notes[pending.id] ?? "").trim().length < 5}
                  onClick={() => act(() => api(`recommendations/${pending.id}/reject`, { body: { note: notes[pending.id] } }))}>Reject</Button>
              </div>
            </div>
          )}
          {canApprove && pending.submitted_by === meId && <p className="text-xs text-muted-foreground">You made this recommendation, so someone else has to decide it.</p>}
          {pending.submitted_by === meId && (
            <Button size="sm" variant="ghost" onClick={() => act(() => api(`recommendations/${pending.id}/withdraw`, { body: {} }))}>Withdraw</Button>
          )}
        </div>
      )}

      {!pending && open && canRecommend && r && record && !record.stale && r.ranking.length > 0 && (
        <div className="space-y-2">
          <Select aria-label="Supplier to recommend" className="max-w-sm" value={supplier} onChange={(e) => setSupplier(e.target.value)}>
            <option value="">Choose a supplier…</option>
            {r.ranking.map((row) => <option key={row.supplier_id} value={row.supplier_id}>{row.rank}. {row.supplier_name} ({row.score.toFixed(1)})</option>)}
          </Select>
          {supplier && supplier !== r.highest_score_supplier_id && <p className="text-sm text-caution">This is not the highest-scoring supplier. Explain why in the rationale.</p>}
          {supplier && (() => {
            const row = r.ranking.find((x) => x.supplier_id === supplier);
            const serious = (row?.deviations ?? []).filter((d) => d.severity === "critical" || d.severity === "major");
            return serious.length > 0 ? <Alert variant="error">{row?.supplier_name} has {serious.length} critical or major deviation(s). They are shown to the approver.</Alert> : null;
          })()}
          <Textarea aria-label="Rationale" placeholder="Rationale for the award (at least 10 characters)" value={rationale} onChange={(e) => setRationale(e.target.value)} />
          <Button disabled={!supplier || rationale.trim().length < 10}
            onClick={() => act(() => api(`rfqs/${rfq.id}/recommendations`, { body: { comparison_id: record.id, supplier_id: supplier, rationale } }), () => { setSupplier(""); setRationale(""); })}>
            Submit recommendation
          </Button>
        </div>
      )}
      {!pending && open && canRecommend && record?.stale && <p className="text-sm text-muted-foreground">Run the comparison again before recommending: its inputs changed.</p>}

      {rfq.status === "awarded" && <Alert variant="success">Awarded {formatDate(rfq.awarded_at)}: {rfq.award_note}</Alert>}

      {(recs.data ?? []).filter((x) => x.status !== "pending").length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">Decision history</summary>
          <ul className="mt-2 space-y-2">
            {(recs.data ?? []).filter((x) => x.status !== "pending").map((x) => (
              <li key={x.id} className="rounded-md border p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={x.status === "approved" ? "default" : x.status === "rejected" ? "destructive" : "neutral"}>{x.status}</Badge>
                  <span className="font-medium">{x.supplier_name}</span>
                  <span className="text-xs text-muted-foreground">recommended by {x.submitted_by_email ?? "unknown"}, decided by {x.decided_by_email ?? "unknown"} {x.decided_at ? `on ${formatDate(x.decided_at)}` : ""}</span>
                </div>
                <p className="mt-1 whitespace-pre-line text-xs">{x.rationale}</p>
                {x.decision_note && <p className="mt-1 text-xs text-muted-foreground">Decision: {x.decision_note}</p>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function ComparisonPanel({ rfq, canRun, canApprove, meId, onAwarded }: { rfq: Rfq; canRun: boolean; canApprove: boolean; meId: string; onAwarded: () => void }) {
  const list = useFetch<ComparisonBrief[]>(`rfqs/${rfq.id}/comparisons`);
  const [selected, setSelected] = useState<string | null>(rfq.award_comparison_id);
  const current = useFetch<ComparisonRecord>(selected ? `comparisons/${selected}` : null);
  const [weights, setWeights] = useState<Record<string, number>>(DEFAULTS);
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

  const c = current.data;
  const r = c?.result;
  const totalWeight = Object.values(weights).reduce((a, b) => a + b, 0);
  const lt = r?.lead_time_analysis;
  const criteria = CRITERIA.filter(([k]) => k !== "risk" || r?.ranking.some((row) => row.criteria.risk));

  return (
    <div className="space-y-5">
      {error && <Alert variant="error">{error}</Alert>}
      {canRun && ["sent", "closed"].includes(rfq.status) && (
        <div className="space-y-3 rounded-md border p-3">
          <p className="text-sm font-medium">Weights</p>
          <div className="grid gap-3 md:grid-cols-5">
            {CRITERIA.map(([key, label]) => (
              <div key={key} className="space-y-1">
                <Label htmlFor={`w-${key}`}>{label}: {totalWeight ? Math.round((weights[key] / totalWeight) * 100) : 0}%</Label>
                <input id={`w-${key}`} type="range" min={0} max={100} step={5} value={weights[key]} className="w-full accent-[hsl(var(--primary))]"
                  onChange={(e) => setWeights({ ...weights, [key]: Number(e.target.value) })} />
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">Risk is scored from deviations only: critical 25, major 10, minor 3 points, capped at 100.</p>
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
          {c.stale && <Alert variant="error">Quotes, exchange rates, unit rules or supplier qualification changed after this comparison. Run it again before deciding.</Alert>}
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
                  {criteria.map(([k, l]) => <th key={k} className="py-2 pr-3 font-medium">{l}</th>)}
                  <th className="py-2 pr-3 text-right font-medium">Basket</th>
                  <th className="py-2 pr-3 font-medium">Risk</th>
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
                        {row.currency && row.currency !== r.currency && <Badge variant="neutral" className="ml-2">{row.currency} converted</Badge>}
                      </td>
                      <td className="py-2 pr-3 text-right font-semibold tabular-nums">{row.score.toFixed(1)}</td>
                      {criteria.map(([k]) => <td key={k} className="py-2 pr-3"><Bar value={row.criteria[k]?.score} /></td>)}
                      <td className="py-2 pr-3 text-right tabular-nums">{money(row.basket_total, r.currency)}</td>
                      <td className="py-2 pr-3"><RiskBadge row={row} /></td>
                      <td className="py-2">
                        <div className="flex flex-wrap gap-1">{row.flags.map((f) => <Badge key={f} variant="caution" className="font-mono">{f}</Badge>)}</div>
                      </td>
                    </tr>
                    {open === row.supplier_id && (
                      <tr>
                        <td />
                        <td colSpan={criteria.length + 5} className="pb-3">
                          <div className="grid gap-4 md:grid-cols-2">
                            <div>
                              <p className="text-xs font-medium">How the score was reached</p>
                              <ul className="space-y-0.5 text-xs text-muted-foreground">{row.explanation.map((x) => <li key={x}>{x}</li>)}</ul>
                            </div>
                            {row.deviations && (
                              <div>
                                <p className="text-xs font-medium">Deviations from the request</p>
                                <DeviationList deviations={row.deviations} />
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {lt && r.ranking.length > 0 && (
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-md border p-3 text-sm">
                <p className="font-medium">Lead time</p>
                <p className="text-xs text-muted-foreground">
                  {lt.required_by ? `Required by ${lt.required_by}: ${lt.available_days} day(s) from the analysis date.` : "No required date on the RFQ; lead times are compared with each other only."}
                  {" "}Fastest {num(lt.fastest)}, median {num(lt.median)}, slowest {num(lt.slowest)} days.
                </p>
                <ul className="mt-2 space-y-1">
                  {r.ranking.map((row) => (
                    <li key={row.supplier_id} className="flex justify-between gap-2">
                      <span>{row.supplier_name}</span>
                      <span className={cn("tabular-nums", row.on_time === false && "text-destructive")}>
                        {row.lead_time_days == null ? "not stated" : `${num(row.lead_time_days)} d`}
                        {row.slack_days != null && ` (${row.slack_days >= 0 ? "+" : ""}${num(row.slack_days)} d)`}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="rounded-md border p-3 text-sm">
                <p className="font-medium">Minimum order quantities</p>
                <p className="text-xs text-muted-foreground">Where a supplier&rsquo;s MOQ is above the requested quantity, the comparison prices the MOQ, because that is what would be paid.</p>
                {(r.moq_analysis?.suppliers_with_excess.length ?? 0) === 0 ? (
                  <p className="mt-2 text-xs text-muted-foreground">No MOQ above the requested quantities.</p>
                ) : (
                  <ul className="mt-2 space-y-1">
                    {r.moq_analysis?.suppliers_with_excess.map((m) => (
                      <li key={m.supplier_name} className="flex justify-between gap-2"><span>{m.supplier_name}</span><span className="tabular-nums text-caution">+{money(m.excess, r.currency)}</span></li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}

          <details className="rounded-md border px-3 py-2 text-sm">
            <summary className="cursor-pointer font-medium">Line prices ({r.currency}, normalised to the requested unit; MOQ included)</summary>
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
                      <td className="py-1 pr-3 align-top">{it.line}. {it.statement.slice(0, 60)}{r.basket_item_ids.includes(it.item_id) ? "" : " *"}
                        {it.quantity != null && <span className="block text-xs text-muted-foreground">{num(it.quantity)} {it.unit ?? ""}</span>}
                      </td>
                      {r.ranking.map((row) => {
                        const cell = it.cells[row.supplier_id];
                        const notes = [...(cell?.conversions ?? []), ...(cell?.fx ? [cell.fx] : [])];
                        return (
                          <td key={row.supplier_id} className="py-1 pr-3 text-right align-top tabular-nums">
                            {cell?.comparable_total != null ? (
                              <>
                                {num(cell.comparable_total)}
                                <span className="block text-xs text-muted-foreground">
                                  {cell.unit_price != null && `${num(cell.unit_price)} / ${it.unit ?? "unit"}`}
                                  {cell.moq_excess ? ` · MOQ ${num(cell.moq)} (+${num(cell.moq_excess)})` : ""}
                                </span>
                                {notes.length > 0 && <span className="block text-[11px] text-muted-foreground" title={notes.join("\n")}>converted</span>}
                              </>
                            ) : <span className="text-xs text-muted-foreground">{cell?.flags.join(", ") || "not quoted"}</span>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1 text-xs text-muted-foreground">* not priced by every supplier, so not part of the price comparison. Hover “converted” for the exact unit and exchange-rate path.</p>
            </div>
          </details>

          {r.excluded.length > 0 && (
            <div className="text-sm">
              <p className="font-medium">Not scored</p>
              <ul className="text-muted-foreground">{r.excluded.map((e) => <li key={e.supplier_id}>{e.supplier_name}: {e.detail}</li>)}</ul>
            </div>
          )}
        </div>
      )}

      {(list.data?.length ?? 0) > 0 && (
        <Recommendations rfq={rfq} record={c ?? null} canRecommend={canRun} canApprove={canApprove} meId={meId} onDecided={onAwarded} />
      )}
    </div>
  );
}
