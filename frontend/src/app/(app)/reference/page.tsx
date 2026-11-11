"use client";

import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { FxRate, UnitInfo, UnitRule } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const today = () => new Date().toISOString().slice(0, 10);

function FxRates({ canManage }: { canManage: boolean }) {
  const rates = useFetch<FxRate[]>("fx-rates");
  const [form, setForm] = useState({ base: "", quote: "", rate: "", valid_from: today(), source: "" });
  const [error, setError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      rates.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Exchange rates</CardTitle>
        <CardDescription>
          Quotes in another currency are converted with the latest rate valid on the analysis date, stated as 1 base = rate quote and used in either direction.
          Rates are never fetched or estimated, and there is no conversion through a third currency. Without a rate the quote is listed as not scored.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        {canManage && (
          <form className="grid gap-2 md:grid-cols-[5rem_5rem_9rem_10rem_1fr_auto] md:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await api("fx-rates", { body: { ...form, source: form.source || null } });
                setForm({ ...form, rate: "", source: "" });
              });
            }}>
            <div className="space-y-1"><Label htmlFor="fx-base">Base</Label><Input id="fx-base" maxLength={3} placeholder="EUR" value={form.base} onChange={(e) => setForm({ ...form, base: e.target.value.toUpperCase() })} required /></div>
            <div className="space-y-1"><Label htmlFor="fx-quote">Quote</Label><Input id="fx-quote" maxLength={3} placeholder="USD" value={form.quote} onChange={(e) => setForm({ ...form, quote: e.target.value.toUpperCase() })} required /></div>
            <div className="space-y-1"><Label htmlFor="fx-rate">Rate</Label><Input id="fx-rate" inputMode="decimal" placeholder="1.0850" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} required pattern="^[0-9]+(\.[0-9]{1,10})?$" /></div>
            <div className="space-y-1"><Label htmlFor="fx-from">Valid from</Label><Input id="fx-from" type="date" value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} required /></div>
            <div className="space-y-1"><Label htmlFor="fx-src">Source</Label><Input id="fx-src" placeholder="e.g. Treasury month-end rate" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} /></div>
            <Button type="submit">Add rate</Button>
          </form>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium">Pair</th>
                <th className="py-1 pr-3 text-right font-medium">Rate</th>
                <th className="py-1 pr-3 font-medium">Valid from</th>
                <th className="py-1 pr-3 font-medium">Source</th>
                <th className="py-1 pr-3 font-medium">Recorded</th>
                <th />
              </tr>
            </thead>
            <tbody className="divide-y">
              {rates.data?.map((r) => (
                <tr key={r.id}>
                  <td className="py-2 pr-3 font-medium">1 {r.base} = … {r.quote}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">{Number(r.rate).toString()}</td>
                  <td className="py-2 pr-3">{r.valid_from}</td>
                  <td className="py-2 pr-3 text-muted-foreground">{r.source ?? "—"}</td>
                  <td className="py-2 pr-3 text-muted-foreground">{formatDate(r.created_at)}</td>
                  <td className="py-2 text-right">
                    {canManage && <Button size="sm" variant="ghost" onClick={() => act(() => api(`fx-rates/${r.id}`, { method: "DELETE" }))}>Delete</Button>}
                  </td>
                </tr>
              ))}
              {rates.data?.length === 0 && <tr><td colSpan={6} className="py-4 text-center text-muted-foreground">No exchange rates recorded.</td></tr>}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function UnitRules({ canManage }: { canManage: boolean }) {
  const rules = useFetch<UnitRule[]>("unit-conversions");
  const catalogue = useFetch<UnitInfo[]>("units");
  const [form, setForm] = useState({ from_unit: "", to_unit: "", factor: "", note: "" });
  const [check, setCheck] = useState({ from: "", to: "" });
  const [checked, setChecked] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      rules.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function runCheck() {
    setError(null);
    try {
      const res = await api<{ factor: string | null; path: string | null }>(`unit-conversions/check?from_unit=${encodeURIComponent(check.from)}&to_unit=${encodeURIComponent(check.to)}`);
      setChecked(res.factor ? res.path : `No exact conversion from ${check.from} to ${check.to}. Add a rule if one exists for this product.`);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const dims = Array.from(new Set((catalogue.data ?? []).map((u) => u.dimension)));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Units</CardTitle>
        <CardDescription>
          Standard conversions within count, mass, length, area and volume use exact factors. Conversions between dimensions (a box to pieces, a piece to kilograms)
          depend on the product, so they are used only when your organization records a rule. A price per unit is divided by the factor; a quantity or MOQ is multiplied.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        {canManage && (
          <form className="grid gap-2 md:grid-cols-[8rem_8rem_9rem_1fr_auto] md:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await api("unit-conversions", { body: { ...form, note: form.note || null } });
                setForm({ from_unit: "", to_unit: "", factor: "", note: "" });
              });
            }}>
            <div className="space-y-1"><Label htmlFor="u-from">1 of unit</Label><Input id="u-from" placeholder="box" value={form.from_unit} onChange={(e) => setForm({ ...form, from_unit: e.target.value })} required /></div>
            <div className="space-y-1"><Label htmlFor="u-to">equals unit</Label><Input id="u-to" placeholder="pcs" value={form.to_unit} onChange={(e) => setForm({ ...form, to_unit: e.target.value })} required /></div>
            <div className="space-y-1"><Label htmlFor="u-f">Factor</Label><Input id="u-f" inputMode="decimal" placeholder="50" value={form.factor} onChange={(e) => setForm({ ...form, factor: e.target.value })} required pattern="^[0-9]+(\.[0-9]{1,12})?$" /></div>
            <div className="space-y-1"><Label htmlFor="u-note">Note</Label><Input id="u-note" placeholder="e.g. carton of 50, per supplier datasheet" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></div>
            <Button type="submit">Add rule</Button>
          </form>
        )}
        <table className="w-full text-left text-sm">
          <thead className="text-muted-foreground">
            <tr><th className="py-1 pr-3 font-medium">Rule</th><th className="py-1 pr-3 font-medium">Note</th><th className="py-1 pr-3 font-medium">Recorded</th><th /></tr>
          </thead>
          <tbody className="divide-y">
            {rules.data?.map((r) => (
              <tr key={r.id}>
                <td className="py-2 pr-3 font-medium tabular-nums">1 {r.from_unit} = {Number(r.factor).toString()} {r.to_unit}</td>
                <td className="py-2 pr-3 text-muted-foreground">{r.note ?? "—"}</td>
                <td className="py-2 pr-3 text-muted-foreground">{formatDate(r.created_at)}</td>
                <td className="py-2 text-right">{canManage && <Button size="sm" variant="ghost" onClick={() => act(() => api(`unit-conversions/${r.id}`, { method: "DELETE" }))}>Delete</Button>}</td>
              </tr>
            ))}
            {rules.data?.length === 0 && <tr><td colSpan={4} className="py-4 text-center text-muted-foreground">No organization rules.</td></tr>}
          </tbody>
        </table>

        <div className="space-y-2 rounded-md border p-3">
          <p className="text-sm font-medium">Check a conversion</p>
          <div className="flex flex-wrap items-end gap-2">
            <Input aria-label="From unit" className="w-32" placeholder="from" value={check.from} onChange={(e) => setCheck({ ...check, from: e.target.value })} />
            <Input aria-label="To unit" className="w-32" placeholder="to" value={check.to} onChange={(e) => setCheck({ ...check, to: e.target.value })} />
            <Button size="sm" variant="outline" disabled={!check.from || !check.to} onClick={runCheck}>Check</Button>
          </div>
          {checked && <p className="text-sm text-muted-foreground">{checked}</p>}
        </div>

        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">Standard units</summary>
          <div className="mt-2 grid gap-3 md:grid-cols-3">
            {dims.map((d) => (
              <div key={d}>
                <p className="font-medium capitalize">{d}</p>
                <ul className="text-xs text-muted-foreground">
                  {(catalogue.data ?? []).filter((u) => u.dimension === d).map((u) => (
                    <li key={u.unit}><span className="font-mono text-foreground">{u.unit}</span> = {u.to_base} base{u.aliases.length > 0 && ` · ${u.aliases.slice(0, 5).join(", ")}`}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </details>
      </CardContent>
    </Card>
  );
}

export default function ReferencePage() {
  const me = useMe();
  const canManage = me.permissions.includes("reference:manage");
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Rates &amp; units</h1>
        <p className="text-sm text-muted-foreground">
          Reference data for quote analysis. Every change is recorded in the audit log and makes earlier comparisons that depend on it outdated.
          {!canManage && " Approvers and admins can change it."}
        </p>
      </div>
      <FxRates canManage={canManage} />
      <UnitRules canManage={canManage} />
    </div>
  );
}
