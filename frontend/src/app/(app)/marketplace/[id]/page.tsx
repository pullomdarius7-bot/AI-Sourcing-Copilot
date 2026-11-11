"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, Lock, MapPin, Sparkles } from "lucide-react";
import { api, ApiError, errorMessage, useFetch } from "@/lib/api";
import { CATEGORIES, CONDITIONS, LOT_TYPES, money, pct, type Analysis, type LotDetail } from "@/lib/marketplace";
import { cn, formatDate } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ConditionBar, FreightTags, SellerBadges, Stat } from "@/components/marketplace/parts";
import { OfferDialog } from "@/components/marketplace/offer-dialog";
import { useMe } from "@/components/me-context";

const DECISION = {
  buy: { label: "BUY", text: "The Buy-It-Now price is at or below your ceiling.", className: "bg-primary text-primary-foreground" },
  negotiate: { label: "NEGOTIATE", text: "Within 15% of your ceiling: make an offer at or below it.", className: "bg-caution text-white" },
  pass: { label: "PASS", text: "The price is well above what this lot supports at your target margin.", className: "bg-destructive text-destructive-foreground" },
};

function AnalysisView({ analysis }: { analysis: Analysis }) {
  if (analysis.status === "queued" || analysis.status === "running") {
    return <Alert>Running the analysis… this takes a few seconds.</Alert>;
  }
  if (analysis.status === "failed" || !analysis.result) {
    return <Alert variant="error">The analysis did not complete: {analysis.error}. No credit was used.</Alert>;
  }
  const r = analysis.result;
  const d = DECISION[r.decision];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4 rounded-lg border p-4">
        <span className={cn("rounded-full px-3 py-1 text-sm font-bold", d.className)}>{d.label}</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm">{d.text}</p>
          <p className="text-xs text-muted-foreground">
            {r.assumptions.channel_label} fees {r.assumptions.fee_rate_pct}% · target net margin {r.assumptions.target_margin_pct}% ·
            freight {money(r.assumptions.freight)} · {analysis.method === "ai" ? "AI resale estimates" : "recovery-rate estimates"}
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs text-muted-foreground">Max bid ceiling</p>
          <p className="font-mono text-2xl font-semibold text-primary">{money(r.max_bid)}</p>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Expected revenue" value={money(r.totals.revenue)} />
        <Stat label="Fees, refurb & freight" value={money(r.totals.fees + r.totals.refurbishment + r.totals.freight)} />
        <Stat label="Net profit at asking" value={money(r.totals.net_profit)} tone={r.totals.net_profit >= 0 ? "good" : "bad"} />
        <Stat label="ROI at asking" value={pct(r.totals.roi_pct)} tone={(r.totals.roi_pct ?? 0) >= 0 ? "good" : "bad"} />
      </dl>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Itemized net profit model</caption>
          <thead className="bg-muted/50 text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Item</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Qty</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Resale / unit</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Revenue</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Fees</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Refurb</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Freight</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Cost</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Net</th>
            </tr>
          </thead>
          <tbody className="divide-y font-mono text-xs">
            {r.items.map((i) => (
              <tr key={i.position}>
                <td className="max-w-[16rem] px-3 py-2 font-sans text-sm">
                  {i.title}
                  <span className="block text-xs text-muted-foreground">{CONDITIONS[i.condition]} · {i.sellable_units} sellable{i.velocity ? ` · sells ${i.velocity}` : ""}</span>
                </td>
                <td className="px-3 py-2 text-right">{i.quantity}</td>
                <td className="px-3 py-2 text-right">{money(i.resale_unit, 2)}{i.resale_source === "ai" && <Sparkles className="ml-1 inline h-3 w-3 text-primary" aria-label="AI estimate" />}</td>
                <td className="px-3 py-2 text-right">{money(i.revenue, 2)}</td>
                <td className="px-3 py-2 text-right">{money(i.fees, 2)}</td>
                <td className="px-3 py-2 text-right">{money(i.refurbishment, 2)}</td>
                <td className="px-3 py-2 text-right">{money(i.freight, 2)}</td>
                <td className="px-3 py-2 text-right">{money(i.cost, 2)}</td>
                <td className={cn("px-3 py-2 text-right font-semibold", i.net_profit < 0 ? "text-destructive" : "text-primary")}>{money(i.net_profit, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
        {r.notes.map((n) => <li key={n}>{n}</li>)}
        <li>Estimates only: inspect the manifest and confirm freight before committing capital.</li>
      </ul>
    </div>
  );
}

export default function LotPage({ params }: { params: { id: string } }) {
  const me = useMe();
  const router = useRouter();
  const { data: lot, error, loading, reload } = useFetch<LotDetail>(`marketplace/lots/${params.id}`);
  const [channel, setChannel] = useState("ebay");
  const [margin, setMargin] = useState("35");
  const [freight, setFreight] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ message: string; upgrade?: boolean } | null>(null);
  const [offerOpen, setOfferOpen] = useState(false);
  const canTrade = me.permissions.includes("marketplace:trade");
  const running = lot?.analyses.some((a) => a.status === "queued" || a.status === "running");

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void reload(), 1500);
    return () => clearInterval(timer);
  }, [running, reload]);

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (error || !lot) return <Alert variant="error">{error?.message ?? "This lot is not on the marketplace."}</Alert>;

  async function analyse(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      await api(`marketplace/lots/${params.id}/analyses`, {
        method: "POST",
        body: { channel, target_margin_pct: Number(margin) || 35, freight_cost: freight ? Number(freight) : null },
      });
      await reload();
    } catch (err) {
      setProblem({ message: errorMessage(err), upgrade: err instanceof ApiError && err.status === 402 });
    } finally {
      setBusy(false);
    }
  }

  const latest = lot.analyses[0];
  const sold = lot.status === "sold";
  return (
    <div className="space-y-6">
      <Link href="/marketplace" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Marketplace
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-2">
          <h1 className="text-2xl font-semibold">{lot.title}</h1>
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1"><MapPin className="h-3.5 w-3.5" aria-hidden />{lot.ships_from}</span>
            {lot.lot_type && <span>{LOT_TYPES[lot.lot_type]}{lot.pallet_count && lot.lot_type === "multi_pallet" ? ` · ${lot.pallet_count} pallets` : ""}</span>}
            <span>Listed {formatDate(lot.published_at)}</span>
          </p>
          <SellerBadges seller={lot.seller} />
          <FreightTags liftgate={lot.liftgate_required} dock={lot.dock_high_only} pickup={lot.local_pickup} />
        </div>
        <div className="space-y-2 text-right">
          <p className="font-mono text-3xl font-semibold">{money(lot.asking_price)}</p>
          <p className="text-xs text-muted-foreground">Buy-It-Now</p>
          {sold ? <Badge variant="neutral">Sold</Badge> : canTrade && (
            lot.open_thread_id ? (
              <Button asChild><Link href={`/rfq/${lot.open_thread_id}`}>Open your negotiation</Link></Button>
            ) : (
              <Button onClick={() => setOfferOpen(true)}>Submit Offer / Request Quote</Button>
            )
          )}
        </div>
      </div>
      {lot.description && <p className="max-w-3xl text-sm">{lot.description}</p>}

      <Card>
        <CardContent className="space-y-4 pt-5">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            <Stat label="Total units" value={lot.total_units.toLocaleString()} />
            <Stat label="Total MSRP" value={money(lot.total_msrp)} />
            <Stat label="Asking price" value={money(lot.asking_price)} hint={lot.total_msrp ? `${((Number(lot.asking_price) / Number(lot.total_msrp)) * 100).toFixed(1)}% of MSRP` : undefined} />
            <Stat label="Est. resale value" value={money(lot.est_resale)} />
            <Stat label="Est. gross margin" value={pct(lot.est_margin_pct)} tone={Number(lot.est_margin_pct) >= 0 ? "good" : "bad"} />
          </dl>
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">Lot grade summary</p>
            <ConditionBar mix={lot.condition_mix} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-primary" aria-hidden /> AI deep analysis</CardTitle>
          <CardDescription>
            An itemized net profit model: resale value per item, marketplace fees, refurbishment, unsellable units, freight and your maximum bid.
            Costs {lot.analysis_cost} credit · you have {lot.credit_balance}.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {canTrade && !sold && (
            <form onSubmit={analyse} className="flex flex-wrap items-end gap-3">
              <div className="flex flex-col gap-1">
                <Label htmlFor="channel" className="text-xs">Sales channel</Label>
                <Select id="channel" className="h-9 w-40" value={channel} onChange={(e) => setChannel(e.target.value)}>
                  <option value="ebay">eBay</option>
                  <option value="amazon">Amazon</option>
                  <option value="direct">Direct / own store</option>
                </Select>
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="margin" className="text-xs">Target net margin %</Label>
                <Input id="margin" className="h-9 w-28" inputMode="decimal" value={margin} onChange={(e) => setMargin(e.target.value)} />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="freight" className="text-xs">Freight quote $ (optional)</Label>
                <Input id="freight" className="h-9 w-36" inputMode="decimal" value={freight} onChange={(e) => setFreight(e.target.value)} placeholder="estimate" />
              </div>
              <Button type="submit" disabled={busy || running}>{busy || running ? "Analysing…" : "Run AI Deep Analysis"}</Button>
            </form>
          )}
          {problem && (
            <Alert variant="error">
              {problem.message} {problem.upgrade && <Link href="/billing" className="font-semibold underline">Add credits or upgrade</Link>}
            </Alert>
          )}
          {latest ? <AnalysisView analysis={latest} /> : <p className="text-sm text-muted-foreground">No analysis yet for this lot.</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Manifest</CardTitle>
          <CardDescription className="flex items-center gap-1"><Lock className="h-3.5 w-3.5" aria-hidden /> Seller identity and pickup address are shared once an offer is accepted.</CardDescription>
        </CardHeader>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-y bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="px-4 py-2 font-medium">Product</th>
                <th scope="col" className="px-4 py-2 font-medium">Category</th>
                <th scope="col" className="px-4 py-2 font-medium">Condition</th>
                <th scope="col" className="px-4 py-2 font-medium">Identifiers</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Qty</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Unit MSRP</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {lot.items.map((i) => (
                <tr key={i.position}>
                  <td className="px-4 py-2">{i.title}{i.brand && <span className="block text-xs text-muted-foreground">{i.brand}</span>}</td>
                  <td className="px-4 py-2">{CATEGORIES[i.category] ?? i.category}</td>
                  <td className="px-4 py-2">{CONDITIONS[i.condition] ?? i.condition}</td>
                  <td className="px-4 py-2 font-mono text-xs">{[i.upc && `UPC ${i.upc}`, i.asin && `ASIN ${i.asin}`, i.model_number && i.model_number].filter(Boolean).join(" · ") || "—"}</td>
                  <td className="px-4 py-2 text-right font-mono">{i.quantity}</td>
                  <td className="px-4 py-2 text-right font-mono">{money(i.unit_msrp, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <OfferDialog
        open={offerOpen}
        onClose={() => setOfferOpen(false)}
        path={`marketplace/lots/${params.id}/offers`}
        title="Submit Offer / Request Quote"
        submitLabel="Send offer"
        initialPrice={latest?.result ? String(Math.floor(Math.min(latest.result.max_bid, Number(lot.asking_price)))) : ""}
        priceHint={latest?.result ? `Your last analysis puts the max bid at ${money(latest.result.max_bid)}.` : undefined}
        onDone={(thread) => router.push(`/rfq/${thread.id}`)}
      />
    </div>
  );
}
