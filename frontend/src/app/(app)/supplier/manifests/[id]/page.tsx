"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, Lock } from "lucide-react";
import { api, errorMessage, useFetch } from "@/lib/api";
import { CATEGORIES, CONDITIONS, LOT_TYPES, money, pct, REGIONS, type SupplierLot } from "@/lib/marketplace";
import { cn } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ConditionBar, Stat } from "@/components/marketplace/parts";
import { useMe } from "@/components/me-context";

type Form = Record<string, string | boolean>;
const TEXT_FIELDS = ["title", "description", "lot_type", "pallet_count", "asking_price", "reserve_price", "dispatch_zip",
  "pickup_address", "pickup_contact", "pickup_phone", "pickup_notes"] as const;
const FLAG_FIELDS = ["liftgate_required", "dock_high_only", "local_pickup"] as const;
const FLAG_LABEL: Record<string, string> = { liftgate_required: "Liftgate required", dock_high_only: "Dock-high only", local_pickup: "Local pickup available" };

function toForm(lot: SupplierLot): Form {
  const form: Form = {};
  for (const k of TEXT_FIELDS) form[k] = lot[k] === null || lot[k] === undefined ? "" : String(lot[k]);
  for (const k of FLAG_FIELDS) form[k] = lot[k];
  return form;
}

function toBody(form: Form) {
  const body: Record<string, unknown> = {};
  for (const k of TEXT_FIELDS) {
    const v = String(form[k] ?? "").trim();
    body[k] = v === "" ? null : k === "pallet_count" ? Number(v) : v;
  }
  for (const k of FLAG_FIELDS) body[k] = Boolean(form[k]);
  if (!body.title) delete body.title;
  return body;
}

export default function LotEditorPage({ params }: { params: { id: string } }) {
  const me = useMe();
  const { data: lot, error, loading, reload } = useFetch<SupplierLot>(`supplier/lots/${params.id}`);
  const [form, setForm] = useState<Form | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const canEdit = me.permissions.includes("listings:manage");

  useEffect(() => {
    if (lot) setForm(toForm(lot));
  }, [lot]);
  useEffect(() => {
    if (lot?.ingest_status !== "processing") return;
    const timer = setInterval(() => void reload(), 2500);
    return () => clearInterval(timer);
  }, [lot?.ingest_status, reload]);

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (error || !lot) return <Alert variant="error">{error?.message ?? "Lot not found"}</Alert>;

  const editable = canEdit && (lot.status === "draft" || lot.status === "published") && lot.ingest_status === "ready";
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...(f ?? {}), [k]: e.target.value }));

  async function save(e?: React.FormEvent) {
    e?.preventDefault();
    if (!form) return false;
    setBusy("save");
    setProblem(null);
    setNotice(null);
    try {
      const out = await api<SupplierLot>(`supplier/lots/${params.id}`, { method: "PATCH", body: toBody(form) });
      setWarnings(out.warnings ?? []);
      setNotice("Saved.");
      await reload();
      return true;
    } catch (err) {
      setProblem(errorMessage(err));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function action(name: "publish" | "withdraw") {
    if (name === "publish" && !(await save())) return;
    setBusy(name);
    setProblem(null);
    try {
      await api(`supplier/lots/${params.id}/${name}`, { method: "POST" });
      setNotice(name === "publish" ? "Your lot is live in the buyer marketplace." : "The lot was withdrawn and open negotiations were closed.");
      await reload();
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <Link href="/supplier/manifests" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden /> All lots
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{lot.title || lot.source_filename}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            From {lot.source_filename}
            {lot.ingest_method && <> · normalized by {lot.ingest_method === "ai" ? "AI, checked against the file" : "rules"}</>}
          </p>
        </div>
        <Badge variant={lot.status === "published" ? "default" : "neutral"}>{lot.status === "published" ? "Live" : lot.status}</Badge>
      </div>
      {notice && <Alert variant="success">{notice}</Alert>}
      {problem && <Alert variant="error">{problem}</Alert>}
      {warnings.map((w) => <Alert key={w}>{w}</Alert>)}

      {lot.ingest_status === "processing" && <Alert>Reading and normalizing the manifest… this page updates by itself.</Alert>}
      {lot.ingest_status === "failed" && <Alert variant="error">The manifest could not be read: {lot.ingest_error}</Alert>}

      {lot.ingest_status === "ready" && (
        <>
          <Card>
            <CardContent className="space-y-4 pt-5">
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
                <Stat label="Lines" value={lot.item_count.toLocaleString()} />
                <Stat label="Units" value={lot.total_units.toLocaleString()} />
                <Stat label="Total MSRP" value={money(lot.total_msrp)} />
                <Stat label="Est. resale value" value={money(lot.est_resale)} />
                <Stat label="Buyer gross margin" value={pct(lot.est_margin_pct)} hint="at your Buy-It-Now price" />
              </dl>
              <ConditionBar mix={lot.condition_mix} />
              {lot.ingest_flags.length > 0 && (
                <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                  {lot.ingest_flags.map((f) => <li key={f}>{f}</li>)}
                </ul>
              )}
            </CardContent>
          </Card>

          {form && (
            <form onSubmit={save} className="grid gap-6 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Listing</CardTitle>
                  <CardDescription>Shown to buyers. Contact details and your company name are removed automatically.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="title">Title</Label>
                    <Input id="title" value={String(form.title)} onChange={set("title")} disabled={!editable} maxLength={200} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="description">Description</Label>
                    <Textarea id="description" value={String(form.description)} onChange={set("description")} disabled={!editable} rows={3} />
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="lot_type">Lot type</Label>
                      <Select id="lot_type" value={String(form.lot_type)} onChange={set("lot_type")} disabled={!editable}>
                        <option value="">Choose…</option>
                        {Object.entries(LOT_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </Select>
                    </div>
                    {form.lot_type === "multi_pallet" && (
                      <div className="space-y-2">
                        <Label htmlFor="pallet_count">Pallets</Label>
                        <Input id="pallet_count" type="number" min={2} max={60} value={String(form.pallet_count)} onChange={set("pallet_count")} disabled={!editable} />
                      </div>
                    )}
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="asking_price">Buy-It-Now price ($)</Label>
                      <Input id="asking_price" inputMode="decimal" value={String(form.asking_price)} onChange={set("asking_price")} disabled={!editable} />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="reserve_price" className="flex items-center gap-1">Reserve ($) <Lock className="h-3 w-3" aria-label="private" /></Label>
                      <Input id="reserve_price" inputMode="decimal" value={String(form.reserve_price)} onChange={set("reserve_price")} disabled={!editable} aria-describedby="reserve-help" />
                      <p id="reserve-help" className="text-xs text-muted-foreground">Private. Offers below it are declined automatically.</p>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Dispatch & freight</CardTitle>
                  <CardDescription>Buyers see only the region and the first three ZIP digits.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="max-w-[12rem] space-y-2">
                    <Label htmlFor="dispatch_zip">Dispatch ZIP code</Label>
                    <Input id="dispatch_zip" inputMode="numeric" maxLength={5} value={String(form.dispatch_zip)} onChange={set("dispatch_zip")} disabled={!editable} />
                    {lot.region && <p className="text-xs text-muted-foreground">{REGIONS[lot.region]} region</p>}
                  </div>
                  <fieldset className="space-y-2">
                    <legend className="text-sm font-medium">Loading conditions</legend>
                    {FLAG_FIELDS.map((k) => (
                      <label key={k} className="flex items-center gap-2 text-sm">
                        <input type="checkbox" className="h-4 w-4 accent-primary" checked={Boolean(form[k])} disabled={!editable}
                          onChange={(e) => setForm((f) => ({ ...(f ?? {}), [k]: e.target.checked }))} />
                        {FLAG_LABEL[k]}
                      </label>
                    ))}
                  </fieldset>
                  <div className="rounded-md border border-dashed p-3">
                    <p className="flex items-center gap-1 text-sm font-medium"><Lock className="h-3.5 w-3.5" aria-hidden /> Pickup details</p>
                    <p className="mb-3 text-xs text-muted-foreground">Shared with the buyer only after you accept an offer.</p>
                    <div className="space-y-3">
                      <div className="space-y-1">
                        <Label htmlFor="pickup_address">Street address</Label>
                        <Textarea id="pickup_address" rows={2} value={String(form.pickup_address)} onChange={set("pickup_address")} disabled={!editable} />
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <div className="space-y-1">
                          <Label htmlFor="pickup_contact">Contact</Label>
                          <Input id="pickup_contact" value={String(form.pickup_contact)} onChange={set("pickup_contact")} disabled={!editable} />
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor="pickup_phone">Phone</Label>
                          <Input id="pickup_phone" type="tel" value={String(form.pickup_phone)} onChange={set("pickup_phone")} disabled={!editable} />
                        </div>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {editable && (
                <div className="flex flex-wrap items-center gap-3 lg:col-span-2">
                  <Button type="submit" variant="outline" disabled={busy !== null}>{busy === "save" ? "Saving…" : "Save"}</Button>
                  {lot.status !== "published" ? (
                    <Button type="button" disabled={busy !== null} onClick={() => void action("publish")}>{busy === "publish" ? "Publishing…" : "Publish to marketplace"}</Button>
                  ) : (
                    <Button type="button" variant="destructive" disabled={busy !== null} onClick={() => void action("withdraw")}>Withdraw from marketplace</Button>
                  )}
                  {lot.publish_problems.length > 0 && lot.status !== "published" && (
                    <p className="text-sm text-muted-foreground">Still needed: {lot.publish_problems.join(", ")}.</p>
                  )}
                </div>
              )}
            </form>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Normalized lines</CardTitle>
              <CardDescription>What buyers see. Lines marked with a dot were adjusted or need a look.</CardDescription>
            </CardHeader>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-y bg-muted/50 text-xs text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-medium">Line</th>
                    <th scope="col" className="px-4 py-2 font-medium">Product</th>
                    <th scope="col" className="px-4 py-2 font-medium">Category</th>
                    <th scope="col" className="px-4 py-2 font-medium">Condition</th>
                    <th scope="col" className="px-4 py-2 font-medium">Identifiers</th>
                    <th scope="col" className="px-4 py-2 text-right font-medium">Qty</th>
                    <th scope="col" className="px-4 py-2 text-right font-medium">Unit MSRP</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {lot.items?.map((i) => (
                    <tr key={i.position} className="align-top">
                      <td className="px-4 py-2 font-mono text-xs text-muted-foreground">{i.source_line}</td>
                      <td className="px-4 py-2">
                        <span className="flex items-start gap-1.5">
                          {(i.flags?.length ?? 0) > 0 && <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-caution" title={i.flags?.join(", ")} aria-label={`Notes: ${i.flags?.join(", ")}`} />}
                          <span>{i.title}{i.brand && <span className="block text-xs text-muted-foreground">{i.brand}</span>}</span>
                        </span>
                      </td>
                      <td className="px-4 py-2">{CATEGORIES[i.category] ?? i.category}</td>
                      <td className="px-4 py-2">{CONDITIONS[i.condition] ?? i.condition}</td>
                      <td className="px-4 py-2 font-mono text-xs">{[i.upc && `UPC ${i.upc}`, i.asin && `ASIN ${i.asin}`, i.model_number && `Model ${i.model_number}`].filter(Boolean).join(" · ") || "—"}</td>
                      <td className="px-4 py-2 text-right font-mono">{i.quantity}</td>
                      <td className={cn("px-4 py-2 text-right font-mono", !i.unit_msrp && "text-muted-foreground")}>{money(i.unit_msrp, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
