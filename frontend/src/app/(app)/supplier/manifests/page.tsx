"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { FileSpreadsheet, Loader2, Upload } from "lucide-react";
import { apiForm, errorMessage, useFetch } from "@/lib/api";
import { CONDITIONS, money, predominant, type SupplierLot } from "@/lib/marketplace";
import { cn, formatDate } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useMe } from "@/components/me-context";

const STATUS: Record<SupplierLot["status"], { label: string; variant: "default" | "neutral" | "caution" | "destructive" }> = {
  draft: { label: "Draft", variant: "neutral" },
  published: { label: "Live", variant: "default" },
  sold: { label: "Sold", variant: "default" },
  withdrawn: { label: "Withdrawn", variant: "caution" },
};

export default function ManifestsPage() {
  const me = useMe();
  const { data: lots, error, loading, reload } = useFetch<SupplierLot[]>("supplier/lots");
  const [condition, setCondition] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const canUpload = me.permissions.includes("listings:manage");
  const processing = lots?.some((l) => l.ingest_status === "processing");

  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(() => void reload(), 2500);
    return () => clearInterval(timer);
  }, [processing, reload]);

  async function send(file: File) {
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (![".csv", ".xlsx", ".pdf"].includes(ext)) {
      setProblem("Upload the manifest as CSV, XLSX or PDF.");
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (condition) form.append("default_condition", condition);
      await apiForm("supplier/manifests", form);
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
        <h1 className="text-2xl font-semibold">Manifests & lots</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Upload a raw warehouse export. Titles, brands, categories, conditions and identifiers are cleaned up for you; then add
          the lot details and publish it to buyers.
        </p>
      </div>

      {canUpload && (
        <Card>
          <CardHeader>
            <CardTitle>Upload a manifest</CardTitle>
            <CardDescription>CSV, XLSX or PDF. One file becomes one lot.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {problem && <Alert variant="error">{problem}</Alert>}
            <div
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files[0]) void send(e.dataTransfer.files[0]); }}
              className={cn("flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center",
                dragging ? "border-primary bg-primary/5" : "border-border")}
            >
              {busy ? <Loader2 className="h-6 w-6 animate-spin text-primary" aria-hidden /> : <Upload className="h-6 w-6 text-muted-foreground" aria-hidden />}
              <p className="text-sm">{busy ? "Uploading…" : "Drag the manifest here, or"}</p>
              <Button type="button" variant="outline" disabled={busy} onClick={() => input.current?.click()}>Choose file</Button>
              <input ref={input} type="file" accept=".csv,.xlsx,.pdf" className="sr-only" aria-label="Manifest file"
                onChange={(e) => e.target.files?.[0] && void send(e.target.files[0])} />
            </div>
            <div className="max-w-xs space-y-2">
              <Label htmlFor="default-condition">Condition for lines without a grade</Label>
              <Select id="default-condition" value={condition} onChange={(e) => setCondition(e.target.value)}>
                <option value="">Detect from the file</option>
                {Object.entries(CONDITIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </div>
          </CardContent>
        </Card>
      )}

      <section aria-labelledby="lots-heading" className="space-y-3">
        <h2 id="lots-heading" className="text-base font-semibold">Your lots</h2>
        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {error && <Alert variant="error">{error.message}</Alert>}
        {lots && lots.length === 0 && <p className="text-sm text-muted-foreground">No lots yet. Upload your first manifest above.</p>}
        <ul className="space-y-3">
          {lots?.map((lot) => (
            <li key={lot.id}>
              <Link href={`/supplier/manifests/${lot.id}`} className="block rounded-lg border bg-card p-4 hover:border-primary/50">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium">
                      <FileSpreadsheet className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="truncate">{lot.title || lot.source_filename}</span>
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">{lot.source_filename} · uploaded {formatDate(lot.created_at)}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {lot.open_offers > 0 && <Badge variant="caution">{lot.open_offers} open offer{lot.open_offers > 1 ? "s" : ""}</Badge>}
                    {lot.ingest_status === "processing" && <Badge variant="neutral">Normalizing…</Badge>}
                    {lot.ingest_status === "failed" && <Badge variant="destructive">Could not read</Badge>}
                    {lot.ingest_status === "ready" && <Badge variant={STATUS[lot.status].variant}>{STATUS[lot.status].label}</Badge>}
                  </div>
                </div>
                {lot.ingest_status === "ready" && (
                  <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                    <div><dt className="text-xs text-muted-foreground">Units</dt><dd className="font-mono">{lot.total_units.toLocaleString()}</dd></div>
                    <div><dt className="text-xs text-muted-foreground">Total MSRP</dt><dd className="font-mono">{money(lot.total_msrp)}</dd></div>
                    <div><dt className="text-xs text-muted-foreground">Buy-It-Now</dt><dd className="font-mono">{money(lot.asking_price)}</dd></div>
                    <div><dt className="text-xs text-muted-foreground">Grade</dt><dd>{predominant(lot.condition_mix)}</dd></div>
                  </dl>
                )}
                {lot.ingest_status === "failed" && <p className="mt-2 text-sm text-destructive">{lot.ingest_error}</p>}
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
