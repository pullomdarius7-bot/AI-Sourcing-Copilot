"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Copy, KeyRound } from "lucide-react";
import { api, errorMessage, useFetch } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useMe } from "@/components/me-context";

type Rules = { channel: string; target_margin_pct: number; freight_per_pallet: number; freight_truckload: number; handling_per_unit: number };
type Key = { id: string; name: string; prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null };

const FIELDS: { key: keyof Rules; label: string; hint: string }[] = [
  { key: "target_margin_pct", label: "Minimum net margin %", hint: "The max bid is the highest price that still earns this margin." },
  { key: "freight_per_pallet", label: "Freight per pallet $", hint: "Used when no freight quote is entered." },
  { key: "freight_truckload", label: "Freight per truckload $", hint: "Full truckload (FTL) lots." },
  { key: "handling_per_unit", label: "Handling per unit $", hint: "Receiving, sorting, listing and storage." },
];

export default function TeamToolsPage() {
  const me = useMe();
  const rules = useFetch<{ available: boolean; rules: Rules; built_in: Rules }>("analysis-rules");
  const keys = useFetch<{ available: boolean; keys: Key[] }>(me.permissions.includes("org:manage") ? "api-keys" : null);
  const [form, setForm] = useState<Rules | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [keyName, setKeyName] = useState("");
  const [newKey, setNewKey] = useState<string | null>(null);
  const canEditRules = me.permissions.includes("reference:manage");

  useEffect(() => {
    if (rules.data) setForm(rules.data.rules);
  }, [rules.data]);

  if (rules.loading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  const available = rules.data?.available;

  async function saveRules(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setProblem(null);
    setNotice(null);
    try {
      await api("analysis-rules", { method: "PUT", body: form });
      setNotice("Margin rules saved. Every new analysis uses them unless someone overrides a value.");
      await rules.reload();
    } catch (err) {
      setProblem(errorMessage(err));
    }
  }

  async function createKey(e: React.FormEvent) {
    e.preventDefault();
    setProblem(null);
    try {
      const out = await api<{ key: string }>("api-keys", { body: { name: keyName.trim() || "Export key" } });
      setNewKey(out.key);
      setKeyName("");
      await keys.reload();
    } catch (err) {
      setProblem(errorMessage(err));
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Team tools</h1>
        <p className="mt-1 text-sm text-muted-foreground">Team-wide margin rules, freight overlays and the export API.</p>
      </div>
      {!available && (
        <Alert>These tools are part of the Sourcing Team plan. <Link href="/billing" className="font-semibold underline">See plans</Link></Alert>
      )}
      {notice && <Alert variant="success">{notice}</Alert>}
      {problem && <Alert variant="error">{problem}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Margin rules & freight overlays</CardTitle>
          <CardDescription>The defaults of every deep analysis and deal sheet your team runs.</CardDescription>
        </CardHeader>
        <CardContent>
          {form && (
            <form onSubmit={saveRules} className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                <div className="space-y-1">
                  <Label htmlFor="channel">Sales channel</Label>
                  <Select id="channel" value={form.channel} disabled={!available || !canEditRules} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
                    <option value="ebay">eBay</option>
                    <option value="amazon">Amazon</option>
                    <option value="direct">Direct / own store</option>
                  </Select>
                </div>
                {FIELDS.map((f) => (
                  <div key={f.key} className="space-y-1">
                    <Label htmlFor={f.key}>{f.label}</Label>
                    <Input id={f.key} inputMode="decimal" value={String(form[f.key])} disabled={!available || !canEditRules}
                      onChange={(e) => setForm({ ...form, [f.key]: Number(e.target.value) || 0 })} aria-describedby={`${f.key}-hint`} />
                    <p id={`${f.key}-hint`} className="text-xs text-muted-foreground">{f.hint}</p>
                  </div>
                ))}
              </div>
              {available && canEditRules && <Button type="submit">Save rules</Button>}
              {available && !canEditRules && <p className="text-sm text-muted-foreground">Approvers and administrators can change these.</p>}
            </form>
          )}
        </CardContent>
      </Card>

      {me.permissions.includes("org:manage") && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><KeyRound className="h-4 w-4" aria-hidden /> Export API</CardTitle>
            <CardDescription>
              Read-only access to your analyses, sourcing results and manifests for spreadsheets and BI tools:
              <code className="ml-1 rounded bg-muted px-1 text-xs">GET /api/export/analyses</code>,
              <code className="ml-1 rounded bg-muted px-1 text-xs">/api/export/sourcing-requests</code>,
              <code className="ml-1 rounded bg-muted px-1 text-xs">/api/export/manifests</code> with
              <code className="ml-1 rounded bg-muted px-1 text-xs">Authorization: Bearer sca_…</code>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {newKey && (
              <Alert variant="success">
                <p className="font-medium">Copy this key now; it is not shown again.</p>
                <p className="mt-1 flex items-center gap-2 break-all font-mono text-xs">{newKey}
                  <Button size="sm" variant="ghost" type="button" onClick={() => void navigator.clipboard?.writeText(newKey)} aria-label="Copy key"><Copy className="h-3.5 w-3.5" aria-hidden /></Button></p>
              </Alert>
            )}
            {available && (
              <form onSubmit={createKey} className="flex flex-wrap items-end gap-2">
                <div className="space-y-1">
                  <Label htmlFor="key-name">Key name</Label>
                  <Input id="key-name" className="w-56" value={keyName} maxLength={100} onChange={(e) => setKeyName(e.target.value)} placeholder="e.g. Power BI" />
                </div>
                <Button type="submit">Create key</Button>
              </form>
            )}
            <ul className="divide-y rounded-md border">
              {keys.data?.keys.map((k) => (
                <li key={k.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
                  <span><span className="font-medium">{k.name}</span> <span className="font-mono text-xs text-muted-foreground">{k.prefix}…</span></span>
                  <span className="flex items-center gap-3 text-xs text-muted-foreground">
                    Created {formatDate(k.created_at)} · {k.last_used_at ? `last used ${formatDate(k.last_used_at)}` : "never used"}
                    {k.revoked_at ? <Badge variant="neutral">Revoked</Badge> : (
                      <Button size="sm" variant="ghost" onClick={async () => { await api(`api-keys/${k.id}`, { method: "DELETE" }); await keys.reload(); }}>Revoke</Button>
                    )}
                  </span>
                </li>
              ))}
              {keys.data?.keys.length === 0 && <li className="px-4 py-3 text-sm text-muted-foreground">No keys yet.</li>}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
