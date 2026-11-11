"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, BookmarkPlus, ExternalLink, Globe, Loader2, Mail, MapPin, Phone, Send, Store, X } from "lucide-react";
import { api, ApiError, errorMessage, useFetch } from "@/lib/api";
import {
  BUSINESS_TYPES, CATEGORIES, CONDITIONS, INVITE_STATUS, money, REGIONS, type SourcingCandidate, type SourcingRequestDetail,
} from "@/lib/marketplace";
import { cn } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SellerBadges } from "@/components/marketplace/parts";
import { useMe } from "@/components/me-context";

function Relevance({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" title="How well the supplier matches your request">
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-secondary" aria-hidden>
        <span className="block h-full bg-primary" style={{ width: `${pct}%` }} />
      </span>
      {pct}% match
    </span>
  );
}

function WebSupplier({ c, onAction, canAdd }: { c: SourcingCandidate; onAction: (path: string) => void; canAdd: boolean }) {
  return (
    <li className="rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="font-medium">{c.company_name}</p>
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {c.business_type && <span>{BUSINESS_TYPES[c.business_type] ?? c.business_type}</span>}
            {c.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" aria-hidden />{c.location}</span>}
            {c.website && (
              <a href={c.website} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 text-primary hover:underline">
                {c.domain}<ExternalLink className="h-3 w-3" aria-hidden />
              </a>
            )}
            {c.categories.map((cat) => <span key={cat}>{CATEGORIES[cat] ?? cat}</span>)}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Relevance value={c.relevance} />
          {c.invite_status && <Badge variant={c.invite_status === "queued" ? "default" : "neutral"}>{INVITE_STATUS[c.invite_status] ?? c.invite_status}</Badge>}
        </div>
      </div>
      {c.summary && <p className="mt-2 text-sm">{c.summary}</p>}
      {(c.products.length > 0 || c.evidence.length > 0) && (
        <ul className="mt-3 space-y-1.5 border-l-2 pl-3 text-sm">
          {c.products.map((p) => (
            <li key={p.quote}><span className="font-medium">{p.name}</span>: <q className="text-muted-foreground">{p.quote}</q> <a href={p.url} target="_blank" rel="noopener noreferrer nofollow" className="text-xs text-primary hover:underline">source</a></li>
          ))}
          {c.evidence.map((e) => (
            <li key={e.quote} className="text-muted-foreground"><q>{e.quote}</q> <a href={e.url} target="_blank" rel="noopener noreferrer nofollow" className="text-xs text-primary hover:underline">source</a></li>
          ))}
        </ul>
      )}
      <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {c.moq && <div><dt className="inline text-muted-foreground">MOQ: </dt><dd className="inline">{c.moq}</dd></div>}
        {c.price_notes && <div><dt className="inline text-muted-foreground">Pricing: </dt><dd className="inline">{c.price_notes}</dd></div>}
        {c.contact_email && <div className="flex items-center gap-1"><Mail className="h-3.5 w-3.5 text-muted-foreground" aria-hidden /><a className="text-primary hover:underline" href={`mailto:${c.contact_email}`}>{c.contact_email}</a></div>}
        {c.contact_phone && <div className="flex items-center gap-1"><Phone className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />{c.contact_phone}</div>}
        {c.contact_url && <div><a className="text-primary hover:underline" href={c.contact_url} target="_blank" rel="noopener noreferrer nofollow">Contact page</a></div>}
      </dl>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {c.supplier_id ? (
          <Button asChild size="sm" variant="outline"><Link href={`/suppliers/${c.supplier_id}`}>In your supplier directory</Link></Button>
        ) : canAdd && (
          <Button size="sm" variant="outline" onClick={() => onAction(`sourcing/candidates/${c.id}/add-to-suppliers`)}>
            <BookmarkPlus className="h-4 w-4" aria-hidden /> Save to suppliers (for an RFQ)
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => onAction(`sourcing/candidates/${c.id}/dismiss`)}><X className="h-4 w-4" aria-hidden /> Not relevant</Button>
        <span className="ml-auto text-xs text-muted-foreground">
          {c.method === "ai" ? "Profiled by AI; every detail is quoted from the supplier's own site." : "Read from the supplier's site without AI."}
        </span>
      </div>
    </li>
  );
}

export default function SourcingRequestPage({ params }: { params: { id: string } }) {
  const me = useMe();
  const { data: r, error, loading, reload } = useFetch<SourcingRequestDetail>(`sourcing/requests/${params.id}`);
  const [problem, setProblem] = useState<{ text: string; upgrade?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const searching = r?.web_status === "queued" || r?.web_status === "running";
  const canTrade = me.permissions.includes("marketplace:trade");
  const canAdd = canTrade && me.permissions.includes("suppliers:manage");

  useEffect(() => {
    if (!searching) return;
    const timer = setInterval(() => void reload(), 2500);
    return () => clearInterval(timer);
  }, [searching, reload]);

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (error || !r) return <Alert variant="error">{error?.message ?? "Request not found"}</Alert>;

  async function act(path: string) {
    setBusy(true);
    setProblem(null);
    try {
      await api(path, { method: "POST" });
      await reload();
    } catch (err) {
      setProblem({ text: errorMessage(err), upgrade: err instanceof ApiError && err.status === 402 });
    } finally {
      setBusy(false);
    }
  }

  const market = r.candidates.filter((c) => c.kind === "marketplace");
  const web = r.candidates.filter((c) => c.kind === "web");
  return (
    <div className="space-y-6">
      <Link href="/sourcing" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="h-4 w-4" aria-hidden /> AI Sourcing</Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{r.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {[r.category && CATEGORIES[r.category], r.condition && CONDITIONS[r.condition], r.quantity && `${r.quantity.toLocaleString()} units`,
              r.target_unit_price && `target ${money(r.target_unit_price, 2)}/unit`, r.region && `to the ${REGIONS[r.region]}`,
              r.needed_by && `by ${r.needed_by}`].filter(Boolean).join(" · ")}
          </p>
          {r.description && <p className="mt-2 max-w-3xl text-sm">{r.description}</p>}
        </div>
        {r.status === "open" && canTrade && (
          <Button variant="outline" size="sm" onClick={() => void act(`sourcing/requests/${r.id}/close`)} disabled={busy}>Close request</Button>
        )}
      </div>
      {r.warnings.map((w) => <Alert key={w}>{w}</Alert>)}
      {problem && <Alert variant="error">{problem.text} {problem.upgrade && <Link href="/billing" className="font-semibold underline">Add credits or upgrade</Link>}</Alert>}

      {r.proposals.length > 0 && (
        <Card className="border-primary">
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Send className="h-4 w-4 text-primary" aria-hidden /> Proposals from suppliers</CardTitle>
            <CardDescription>Suppliers on the platform answered your request with lots. Identities stay masked until you accept.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {r.proposals.map((t) => (
                <li key={t.id}>
                  <Link href={`/rfq/${t.id}`} className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3 hover:border-primary/50">
                    <span className="min-w-0"><span className="block font-medium">{t.lot_title}</span><SellerBadges seller={t.counterparty} compact /></span>
                    <span className="flex items-center gap-2"><span className="font-mono font-semibold">{money(t.offer_price ?? t.agreed_price, 2)}</span>
                      <Badge variant={t.awaiting === "you" ? "default" : "neutral"}>{t.awaiting === "you" ? "Your answer" : t.status}</Badge></span>
                  </Link>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <section aria-labelledby="market-heading" className="space-y-3">
        <h2 id="market-heading" className="flex items-center gap-2 text-base font-semibold"><Store className="h-4 w-4" aria-hidden /> On the marketplace ({market.length})</h2>
        {market.length === 0 ? (
          <p className="text-sm text-muted-foreground">No listed lot matches this request yet.</p>
        ) : (
          <ul className="grid gap-2 md:grid-cols-2">
            {market.map((c) => (
              <li key={c.id}>
                <Link href={`/marketplace/${c.lot_id}`} className="block rounded-lg border bg-card p-4 hover:border-primary/50">
                  <p className="font-medium">{c.company_name}</p>
                  <p className="text-xs text-muted-foreground">{[c.summary, c.location].filter(Boolean).join(" · ")}</p>
                  <div className="mt-2"><Relevance value={c.relevance} /></div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="web-heading" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="web-heading" className="flex items-center gap-2 text-base font-semibold"><Globe className="h-4 w-4" aria-hidden /> Found on the web ({web.length})</h2>
          {canTrade && r.status === "open" && r.web_search_available && !searching && (
            <Button size="sm" variant={web.length ? "outline" : "default"} onClick={() => void act(`sourcing/requests/${r.id}/search-web`)} disabled={busy}>
              <Globe className="h-4 w-4" aria-hidden /> {r.web_status === "none" || r.web_status === "skipped" ? "Search the web" : "Search again"} ({r.web_search_cost} credits)
            </Button>
          )}
        </div>
        {searching && (
          <Alert><span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" aria-hidden />Searching the web and reading supplier websites… this takes a minute. You will also get an email.</span></Alert>
        )}
        {r.web_note && !searching && <p className={cn("text-sm", r.web_status === "failed" ? "text-destructive" : "text-muted-foreground")}>{r.web_note}</p>}
        {r.invites_sent > 0 && (
          <p className="text-sm text-muted-foreground">
            {r.invites_sent === 1 && web.length === 1 ? "We invited this supplier" : `We invited ${r.invites_sent} of these suppliers`} to
            join the platform and quote. Their proposals will appear here.
          </p>
        )}
        <ul className="space-y-3">
          {web.map((c) => <WebSupplier key={c.id} c={c} onAction={(p) => void act(p)} canAdd={canAdd} />)}
        </ul>
      </section>
    </div>
  );
}
