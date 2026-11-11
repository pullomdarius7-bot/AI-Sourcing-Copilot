"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Download, FileSpreadsheet, Loader2, Upload } from "lucide-react";
import { apiForm, api, errorMessage, useFetch } from "@/lib/api";
import { money, pct, predominant, type PrivateLot } from "@/lib/marketplace";
import { cn, formatDate } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useMe } from "@/components/me-context";

const DECISION: Record<string, string> = { buy: "bg-primary text-primary-foreground", negotiate: "bg-caution text-white", pass: "bg-destructive text-destructive-foreground" };

function PriceForm({ lot, onSaved }: { lot: PrivateLot; onSaved: () => void }) {
  const [price, setPrice] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!(Number(price) > 0)) return setProblem("Enter the lot's cost.");
    try {
      await api(`buyer/manifests/${lot.id}`, { method: "PATCH", body: { asking_price: Number(price).toFixed(2) } });
      onSaved();
    } catch (err) {
      setProblem(errorMessage(err));
    }
  }
  return (
    <form onSubmit={save} className="flex flex-wrap items-center gap-2">
      <label htmlFor={`price-${lot.id}`} className="text-sm">Lot cost (bid or quote) $</label>
      <Input id={`price-${lot.id}`} className="h-8 w-28" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
      <Button size="sm" type="submit">Analyse</Button>
      {problem && <span className="text-xs text-destructive">{problem}</span>}
    </form>
  );
}

export default function DealsPage() {
  const me = useMe();
  const { data, error, loading, reload } = useFetch<PrivateLot[]>("buyer/manifests");
  const [busy, setBusy] = useState(false);
  const [price, setPrice] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const canTrade = me.permissions.includes("marketplace:trade");
  const working = data?.some((l) => l.ingest_status === "processing" || l.analysis?.status === "queued" || l.analysis?.status === "running");

  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => void reload(), 2500);
    return () => clearInterval(timer);
  }, [working, reload]);

  async function send(files: FileList) {
    if (!files.length) return;
    if (files.length > 20) return setProblem("Upload at most 20 manifests at a time.");
    setBusy(true);
    setProblem(null);
    try {
      const form = new FormData();
      Array.from(files).forEach((f) => form.append("files", f));
      if (price && files.length === 1) form.append("asking_price", Number(price).toFixed(2));
      await apiForm("buyer/manifests", form);
      setPrice("");
      await reload();
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Deal analyzer</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Drop in manifests from auctions or outside suppliers (CSV, XLSX or PDF). Each one is cleaned up and analysed into an itemized
          net profit model with a max bid. One credit per manifest up to 500 lines; the first analysis is included.
        </p>
      </div>
      {canTrade && (
        <Card>
          <CardHeader>
            <CardTitle>Upload manifests</CardTitle>
            <CardDescription>Select several files at once for batch parsing. Analyses use your margin rules.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {problem && <Alert variant="error">{problem}</Alert>}
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <label htmlFor="single-price" className="text-xs text-muted-foreground">Lot cost $ (single file, optional)</label>
                <Input id="single-price" className="w-36" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
              </div>
              <Button type="button" onClick={() => input.current?.click()} disabled={busy}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Upload className="h-4 w-4" aria-hidden />} Choose manifests
              </Button>
              <input ref={input} type="file" multiple accept=".csv,.xlsx,.pdf" className="sr-only" aria-label="Manifest files"
                onChange={(e) => e.target.files && void send(e.target.files)} />
            </div>
          </CardContent>
        </Card>
      )}

      {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {error && <Alert variant="error">{error.message}</Alert>}
      {data && data.length === 0 && <p className="text-sm text-muted-foreground">No manifests yet.</p>}
      <ul className="space-y-3">
        {data?.map((lot) => {
          const result = lot.analysis?.status === "succeeded" ? lot.analysis.result : null;
          return (
            <li key={lot.id} className="rounded-lg border bg-card p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 font-medium"><FileSpreadsheet className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                    {lot.ingest_status === "ready" ? <Link href={`/marketplace/${lot.id}`} className="hover:underline">{lot.title}</Link> : lot.source_filename}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{lot.source_filename} · {formatDate(lot.created_at)}</p>
                </div>
                {result ? (
                  <div className="flex items-center gap-3 text-right">
                    <span className={cn("rounded-full px-3 py-1 text-xs font-bold", DECISION[result.decision])}>{result.decision.toUpperCase()}</span>
                    <div><p className="text-xs text-muted-foreground">Max bid</p><p className="font-mono font-semibold text-primary">{money(result.max_bid)}</p></div>
                    <div className="flex gap-1">
                      <Button asChild size="sm" variant="outline"><a href={`/api/marketplace/analyses/${lot.analysis!.id}/deal-sheet.pdf`}><Download className="h-3.5 w-3.5" aria-hidden />PDF</a></Button>
                      <Button asChild size="sm" variant="outline"><a href={`/api/marketplace/analyses/${lot.analysis!.id}/deal-sheet.csv`}><Download className="h-3.5 w-3.5" aria-hidden />CSV</a></Button>
                    </div>
                  </div>
                ) : lot.ingest_status === "failed" ? (
                  <Badge variant="destructive">Could not read</Badge>
                ) : lot.ingest_status === "processing" || lot.analysis?.status === "queued" || lot.analysis?.status === "running" ? (
                  <Badge variant="neutral">Working…</Badge>
                ) : lot.analysis?.status === "failed" ? (
                  <Badge variant="destructive">{lot.analysis.error}</Badge>
                ) : null}
              </div>
              {lot.ingest_status === "failed" && <p className="mt-2 text-sm text-destructive">{lot.ingest_error}</p>}
              {lot.ingest_status === "ready" && (
                <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm">
                  <span className="text-muted-foreground">{lot.total_units.toLocaleString()} units · MSRP {money(lot.total_msrp)} · {predominant(lot.condition_mix)}
                    {lot.asking_price && <> · cost {money(lot.asking_price)} · est. margin {pct(lot.est_margin_pct)}</>}</span>
                  {!lot.asking_price && canTrade && <PriceForm lot={lot} onSaved={() => void reload()} />}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
