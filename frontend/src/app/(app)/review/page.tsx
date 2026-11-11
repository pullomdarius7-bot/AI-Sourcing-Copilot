"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfidenceBadge } from "@/components/review-badges";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { QueueItem } from "@/lib/types";

const PAGE = 20;
const BULK_MIN = 0.85;

export default function ReviewQueuePage() {
  const me = useMe();
  const allowed = me.permissions.includes("requirements:review");
  const [kind, setKind] = useState("");
  const [flagged, setFlagged] = useState("");
  const [maxConf, setMaxConf] = useState("");
  const [offset, setOffset] = useState(0);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (kind) params.set("item_type", kind);
  if (flagged) params.set("flagged", flagged);
  if (maxConf) params.set("max_confidence", maxConf);
  const queue = useFetch<{ items: QueueItem[]; total: number }>(allowed ? `review/queue?${params}` : null);

  useEffect(() => setOffset(0), [kind, flagged, maxConf]);

  if (!allowed) return <Alert variant="error">Your role cannot review extracted data.</Alert>;

  const items = queue.data?.items ?? [];
  const bulkEligible = items.filter(
    (i) => i.item_type === "parameter" && i.grounding_status === "grounded" && i.flags.length === 0 && i.confidence >= BULK_MIN,
  );

  async function act(fn: () => Promise<void>) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      queue.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const verify = (i: QueueItem) =>
    act(async () => {
      await api(`${i.item_type === "requirement" ? "requirements" : "parameters"}/${i.id}/verify`, { method: "POST", body: {} });
    });

  const reject = (i: QueueItem) =>
    act(async () => {
      await api(`${i.item_type === "requirement" ? "requirements" : "parameters"}/${i.id}/reject`, { method: "POST", body: { reason } });
      setRejecting(null);
      setReason("");
    });

  const bulkVerify = () =>
    act(async () => {
      const res = await api<{ verified: string[]; skipped: { id: string; reason: string }[] }>("parameters/bulk-verify", {
        method: "POST",
        body: { ids: bulkEligible.map((i) => i.id) },
      });
      setNotice(`${res.verified.length} verified${res.skipped.length ? `, ${res.skipped.length} skipped by the safety checks` : ""}.`);
    });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Review queue</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Everything extracted but not yet checked by a person, riskiest first: flagged items, then lowest confidence.
          {queue.data ? ` ${queue.data.total} waiting.` : ""}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select aria-label="Item type" className="h-10 rounded-md border border-input bg-card px-3 text-sm" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Requirements and parameters</option>
          <option value="requirement">Requirements only</option>
          <option value="parameter">Parameters only</option>
        </select>
        <select aria-label="Flags" className="h-10 rounded-md border border-input bg-card px-3 text-sm" value={flagged} onChange={(e) => setFlagged(e.target.value)}>
          <option value="">Flagged or not</option>
          <option value="true">Flagged only</option>
          <option value="false">Not flagged</option>
        </select>
        <select aria-label="Confidence" className="h-10 rounded-md border border-input bg-card px-3 text-sm" value={maxConf} onChange={(e) => setMaxConf(e.target.value)}>
          <option value="">Any confidence</option>
          <option value="0.6">Below 60%</option>
          <option value="0.85">Below 85%</option>
        </select>
        <Button variant="outline" disabled={bulkEligible.length === 0} onClick={bulkVerify} title="Only grounded values with no flags and confidence of 85% or more">
          Verify {bulkEligible.length} high-confidence on this page
        </Button>
      </div>

      {error && <Alert variant="error">{error}</Alert>}
      {notice && <Alert variant="success">{notice}</Alert>}
      {queue.error && <Alert variant="error">{queue.error.message}</Alert>}

      <Card>
        <CardContent className="divide-y pt-2">
          {items.map((i) => (
            <div key={`${i.item_type}:${i.id}`} className="space-y-2 py-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs text-muted-foreground">
                    {i.filename} · {i.item_type === "requirement" ? "requirement" : "parameter"}
                  </p>
                  <p className="font-medium">
                    {i.item_type === "parameter" && <span className="text-muted-foreground">{i.label.replace(/_/g, " ")}: </span>}
                    <span className={i.grounding_status === "unknown" ? "text-caution" : ""}>{i.value ?? "Not stated"}</span>
                  </p>
                  {i.requirement_statement && <p className="text-xs text-muted-foreground">in: {i.requirement_statement}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <ConfidenceBadge value={i.confidence} unknown={i.grounding_status === "unknown"} />
                  {i.flags.slice(0, 3).map((f) => (
                    <Badge key={f} variant="caution" className="font-mono">{f}</Badge>
                  ))}
                  {i.flags.length > 3 && <Badge variant="caution">+{i.flags.length - 3}</Badge>}
                </div>
              </div>
              {i.source_quote && <blockquote className="border-l-2 border-primary pl-3 text-sm text-muted-foreground">“{i.source_quote}”</blockquote>}
              {rejecting === i.id ? (
                <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); void reject(i); }}>
                  <input
                    aria-label="Reason for rejecting"
                    className="h-9 min-w-[16rem] flex-1 rounded-md border border-input bg-card px-3 text-sm"
                    placeholder="Why is this wrong?"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    minLength={3}
                    required
                  />
                  <Button type="submit" size="sm" variant="destructive">Reject</Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setRejecting(null)}>Cancel</Button>
                </form>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => verify(i)}>{i.grounding_status === "unknown" ? "Confirm not stated" : "Verify"}</Button>
                  <Button size="sm" variant="outline" onClick={() => { setRejecting(i.id); setReason(""); }}>Reject</Button>
                  <Button asChild size="sm" variant="ghost">
                    <Link href={`/requirements/${i.run_id}?focus=${i.id}`}>Open in evidence viewer</Link>
                  </Button>
                </div>
              )}
            </div>
          ))}
          {queue.data && items.length === 0 && <p className="py-10 text-center text-sm text-muted-foreground">Nothing is waiting for review.</p>}
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
        <Button variant="outline" size="sm" disabled={!queue.data || offset + PAGE >= queue.data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
      </div>
    </div>
  );
}
