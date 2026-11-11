"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Globe, Search, Store } from "lucide-react";
import { api, errorMessage, useFetch } from "@/lib/api";
import { CATEGORIES, CONDITIONS, REGIONS, type SourcingRequest, type SourcingRequestDetail } from "@/lib/marketplace";
import { formatDate } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useMe } from "@/components/me-context";

const WEB_LABEL: Record<SourcingRequest["web_status"], string> = {
  none: "Marketplace only", queued: "Searching the web…", running: "Searching the web…", done: "Web searched",
  skipped: "Web not searched", failed: "Web search failed",
};

const EMPTY = { title: "", description: "", category: "", condition: "", quantity: "", target_unit_price: "", region: "", needed_by: "" };

export default function SourcingPage() {
  const me = useMe();
  const router = useRouter();
  const { data, error, loading } = useFetch<SourcingRequest[]>("sourcing/requests");
  const [form, setForm] = useState(EMPTY);
  const [searchWeb, setSearchWeb] = useState<"auto" | "always" | "never">("auto");
  const [openToSuppliers, setOpenToSuppliers] = useState(true);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const canTrade = me.permissions.includes("marketplace:trade");
  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (form.title.trim().length < 3) {
      setProblem("Describe the product in at least 3 characters.");
      return;
    }
    setBusy(true);
    setProblem(null);
    const body: Record<string, unknown> = { title: form.title.trim(), search_web: searchWeb, open_to_suppliers: openToSuppliers };
    for (const k of ["description", "category", "condition", "region", "needed_by"] as const) if (form[k]) body[k] = form[k];
    if (form.quantity) body.quantity = Number(form.quantity);
    if (form.target_unit_price) body.target_unit_price = Number(form.target_unit_price).toFixed(2);
    try {
      const created = await api<SourcingRequestDetail>("sourcing/requests", { body });
      router.push(`/sourcing/${created.id}`);
    } catch (err) {
      setProblem(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">AI Sourcing</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Tell us what you need. We check the marketplace first; if no supplier there has it, the AI searches the web for wholesalers,
          distributors and liquidators, reads their sites and brings back an organized shortlist with the evidence.
        </p>
      </div>

      {canTrade && (
        <Card>
          <CardHeader>
            <CardTitle>New request</CardTitle>
            <CardDescription>A web search costs 2 credits and is refunded if no supplier is found. Marketplace matching is free.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-4">
              {problem && <Alert variant="error">{problem}</Alert>}
              <div className="grid gap-4 md:grid-cols-[2fr_1fr]">
                <div className="space-y-2">
                  <Label htmlFor="title">What are you looking for?</Label>
                  <Input id="title" placeholder="e.g. Cordless drill kits, 20V" value={form.title} onChange={set("title")} maxLength={200} required />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="category">Category</Label>
                  <Select id="category" value={form.category} onChange={set("category")}>
                    <option value="">Any</option>
                    {Object.entries(CATEGORIES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select>
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="description">Details (optional)</Label>
                <Textarea id="description" rows={2} maxLength={4000} value={form.description} onChange={set("description")}
                  placeholder="Brands, models, packaging, certifications…" />
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                <div className="space-y-2">
                  <Label htmlFor="condition">Condition</Label>
                  <Select id="condition" value={form.condition} onChange={set("condition")}>
                    <option value="">Any</option>
                    {Object.entries(CONDITIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="quantity">Quantity (units)</Label>
                  <Input id="quantity" type="number" min={1} value={form.quantity} onChange={set("quantity")} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="target_unit_price">Target price / unit ($)</Label>
                  <Input id="target_unit_price" inputMode="decimal" value={form.target_unit_price} onChange={set("target_unit_price")} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="region">Deliver to</Label>
                  <Select id="region" value={form.region} onChange={set("region")}>
                    <option value="">Anywhere in the US</option>
                    {Object.entries(REGIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="needed_by">Needed by</Label>
                  <Input id="needed_by" type="date" min={new Date().toISOString().slice(0, 10)} value={form.needed_by} onChange={set("needed_by")} />
                </div>
              </div>
              <fieldset className="space-y-2">
                <legend className="text-sm font-medium">Search the web</legend>
                {([["auto", "Only if no marketplace supplier has it (recommended)"], ["always", "Always, as well as the marketplace"], ["never", "Never, marketplace only"]] as const).map(([v, label]) => (
                  <label key={v} className="flex items-center gap-2 text-sm">
                    <input type="radio" name="search_web" className="accent-primary" checked={searchWeb === v} onChange={() => setSearchWeb(v)} />
                    {label}
                  </label>
                ))}
              </fieldset>
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary" checked={openToSuppliers} onChange={(e) => setOpenToSuppliers(e.target.checked)} />
                <span>
                  Let suppliers see this request and send proposals (your identity stays hidden). Suppliers the AI finds on the web are
                  invited to quote.
                </span>
              </label>
              <Button type="submit" disabled={busy}><Search className="h-4 w-4" aria-hidden /> {busy ? "Finding suppliers…" : "Find suppliers"}</Button>
            </form>
          </CardContent>
        </Card>
      )}

      <section aria-labelledby="requests-heading" className="space-y-3">
        <h2 id="requests-heading" className="text-base font-semibold">Your requests</h2>
        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {error && <Alert variant="error">{error.message}</Alert>}
        {data && data.length === 0 && <p className="text-sm text-muted-foreground">No requests yet.</p>}
        <ul className="space-y-2">
          {data?.map((r) => (
            <li key={r.id}>
              <Link href={`/sourcing/${r.id}`} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4 hover:border-primary/50">
                <div className="min-w-0">
                  <p className="font-medium">{r.title}</p>
                  <p className="text-xs text-muted-foreground">
                    {[r.category && CATEGORIES[r.category], r.quantity && `${r.quantity.toLocaleString()} units`, formatDate(r.created_at)].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5"><Store className="h-3 w-3" aria-hidden />{r.marketplace_matches} on marketplace</span>
                  <span className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5"><Globe className="h-3 w-3" aria-hidden />{r.web_matches} from the web</span>
                  <Badge variant={r.web_status === "failed" ? "destructive" : r.web_status === "running" || r.web_status === "queued" ? "caution" : "neutral"}>{WEB_LABEL[r.web_status]}</Badge>
                  {r.status === "closed" && <Badge variant="neutral">Closed</Badge>}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
