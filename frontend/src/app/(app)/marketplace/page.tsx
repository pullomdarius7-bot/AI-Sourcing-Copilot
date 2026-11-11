"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { MapPin, Search } from "lucide-react";
import { useFetch } from "@/lib/api";
import { CATEGORIES, CONDITIONS, LOT_TYPES, money, pct, predominant, REGIONS, type LotCard } from "@/lib/marketplace";
import { cn } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ConditionBar, FreightTags, SellerBadges } from "@/components/marketplace/parts";

type Filters = { category: string[]; condition: string[]; lot_type: string[]; region: string[]; min_margin: string; max_price: string; q: string; sort: string };
const EMPTY: Filters = { category: [], condition: [], lot_type: [], region: [], min_margin: "", max_price: "", q: "", sort: "newest" };

function Chips({ legend, options, value, onChange }: { legend: string; options: Record<string, string>; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset>
      <legend className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{legend}</legend>
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(options).map(([key, label]) => {
          const on = value.includes(key);
          return (
            <label key={key} className={cn("cursor-pointer rounded-full border px-3 py-1 text-xs transition-colors focus-within:ring-2 focus-within:ring-ring",
              on ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-secondary")}>
              <input type="checkbox" className="sr-only" checked={on} onChange={() => onChange(on ? value.filter((v) => v !== key) : [...value, key])} />
              {label}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

export default function MarketplacePage() {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [query, setQuery] = useState("");
  const path = useMemo(() => {
    const qs = new URLSearchParams();
    for (const key of ["category", "condition", "lot_type", "region"] as const) filters[key].forEach((v) => qs.append(key, v));
    if (filters.min_margin) qs.set("min_margin", filters.min_margin);
    if (filters.max_price) qs.set("max_price", filters.max_price);
    if (filters.q) qs.set("q", filters.q);
    qs.set("sort", filters.sort);
    return `marketplace/lots?${qs.toString()}`;
  }, [filters]);
  const { data, error, loading } = useFetch<{ total: number; lots: LotCard[] }>(path);
  const set = <K extends keyof Filters>(key: K) => (value: Filters[K]) => setFilters((f) => ({ ...f, [key]: value }));
  const active = JSON.stringify(filters) !== JSON.stringify(EMPTY);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Marketplace</h1>
        <p className="mt-1 text-sm text-muted-foreground">Lots from verified liquidators and wholesalers. Margins are estimates; run a deep analysis before you bid.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
        <aside aria-label="Filters" className="space-y-5 lg:sticky lg:top-6 lg:self-start">
          <form onSubmit={(e) => { e.preventDefault(); set("q")(query.trim()); }} className="flex gap-2">
            <Label htmlFor="search" className="sr-only">Search lots</Label>
            <Input id="search" placeholder="Search lots" value={query} onChange={(e) => setQuery(e.target.value)} />
            <Button type="submit" size="sm" variant="outline" aria-label="Search"><Search className="h-4 w-4" aria-hidden /></Button>
          </form>
          <Chips legend="Category" options={CATEGORIES} value={filters.category} onChange={set("category")} />
          <Chips legend="Condition" options={CONDITIONS} value={filters.condition} onChange={set("condition")} />
          <Chips legend="Lot size" options={LOT_TYPES} value={filters.lot_type} onChange={set("lot_type")} />
          <Chips legend="Ships from" options={REGIONS} value={filters.region} onChange={set("region")} />
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="min_margin" className="text-xs">Min. est. margin %</Label>
              <Input id="min_margin" inputMode="decimal" value={filters.min_margin} onChange={(e) => set("min_margin")(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="max_price" className="text-xs">Max. price $</Label>
              <Input id="max_price" inputMode="decimal" value={filters.max_price} onChange={(e) => set("max_price")(e.target.value)} />
            </div>
          </div>
          {active && <Button variant="ghost" size="sm" onClick={() => { setFilters(EMPTY); setQuery(""); }}>Clear filters</Button>}
        </aside>

        <section aria-labelledby="results" className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="results" className="text-sm text-muted-foreground" aria-live="polite">{data ? `${data.total} lot${data.total === 1 ? "" : "s"}` : "Loading…"}</h2>
            <div className="flex items-center gap-2">
              <Label htmlFor="sort" className="text-xs text-muted-foreground">Sort</Label>
              <Select id="sort" className="h-8 w-44 text-xs" value={filters.sort} onChange={(e) => set("sort")(e.target.value)}>
                <option value="newest">Newest</option>
                <option value="margin">Highest est. margin</option>
                <option value="price_low">Price: low to high</option>
                <option value="price_high">Price: high to low</option>
                <option value="msrp">Highest MSRP</option>
              </Select>
            </div>
          </div>
          {error && <Alert variant="error">{error.message}</Alert>}
          {data && data.lots.length === 0 && !loading && <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">No lots match these filters.</p>}
          <ul className="grid gap-4 xl:grid-cols-2">
            {data?.lots.map((lot) => {
              const margin = Number(lot.est_margin_pct);
              return (
                <li key={lot.id}>
                  <Link href={`/marketplace/${lot.id}`} className="flex h-full flex-col gap-3 rounded-lg border bg-card p-4 transition-colors hover:border-primary/50">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-medium leading-snug">{lot.title}</p>
                        <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                          <MapPin className="h-3 w-3" aria-hidden />{lot.ships_from ?? "—"} · {lot.lot_type ? LOT_TYPES[lot.lot_type] : ""}
                        </p>
                      </div>
                      <p className="shrink-0 text-right">
                        <span className="block font-mono text-lg font-semibold">{money(lot.asking_price)}</span>
                        <span className="text-xs text-muted-foreground">Buy-It-Now</span>
                      </p>
                    </div>
                    <dl className="grid grid-cols-3 gap-2 text-sm">
                      <div><dt className="text-xs text-muted-foreground">Units</dt><dd className="font-mono">{lot.total_units.toLocaleString()}</dd></div>
                      <div><dt className="text-xs text-muted-foreground">Total MSRP</dt><dd className="font-mono">{money(lot.total_msrp)}</dd></div>
                      <div><dt className="text-xs text-muted-foreground">Est. gross margin</dt>
                        <dd className={cn("font-mono font-semibold", margin >= 30 ? "text-primary" : margin < 0 ? "text-destructive" : "")}>{pct(lot.est_margin_pct)}</dd></div>
                    </dl>
                    <ConditionBar mix={lot.condition_mix} />
                    <div className="mt-auto flex flex-wrap items-center justify-between gap-2">
                      <SellerBadges seller={lot.seller} compact />
                      <span className="text-xs text-muted-foreground">{predominant(lot.condition_mix)} · {lot.categories.map((c) => CATEGORIES[c] ?? c).slice(0, 3).join(", ")}</span>
                    </div>
                    <FreightTags liftgate={lot.liftgate_required} dock={lot.dock_high_only} pickup={lot.local_pickup} />
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      </div>
    </div>
  );
}
