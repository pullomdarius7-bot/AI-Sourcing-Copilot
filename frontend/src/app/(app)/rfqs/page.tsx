"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { RFQ_STATUS_LABEL, RfqStatusBadge } from "@/components/supplier-badges";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { ExtractionRun, Rfq, RfqListItem, SupplierCategory } from "@/lib/types";
import { formatDate } from "@/lib/utils";

function NewRfq({ onCreated }: { onCreated: (id: string) => void }) {
  const runs = useFetch<ExtractionRun[]>("extraction-runs?status=approved&limit=50");
  const categories = useFetch<SupplierCategory[]>("supplier-categories");
  const [form, setForm] = useState({ title: "", runId: "", due: "", currency: "", categoryId: "", message: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const rfq = await api<Rfq>("rfqs", {
        body: {
          title: form.title, from_run_id: form.runId || null, response_due_date: form.due || null,
          currency: form.currency || null, category_id: form.categoryId || null, message: form.message || null,
        },
      });
      onCreated(rfq.id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
      {error && <Alert variant="error" className="md:col-span-2">{error}</Alert>}
      <div className="space-y-1 md:col-span-2">
        <Label htmlFor="r-title">Title</Label>
        <Input id="r-title" value={form.title} onChange={set("title")} required minLength={3} />
      </div>
      <div className="space-y-1 md:col-span-2">
        <Label htmlFor="r-run">Start from approved requirements</Label>
        <Select id="r-run" value={form.runId} onChange={set("runId")}>
          <option value="">Blank RFQ (add lines by hand)</option>
          {runs.data?.map((r) => (
            <option key={r.id} value={r.id}>
              {r.filename ?? r.document_id} · {r.counts.requirements} requirements · approved {formatDate(r.approved_at)}
            </option>
          ))}
        </Select>
        <p className="text-xs text-muted-foreground">Only approved extractions can be used. Rejected items are left out; values the document did not state become questions to suppliers.</p>
      </div>
      <div className="space-y-1">
        <Label htmlFor="r-due">Quotation due</Label>
        <Input id="r-due" type="date" value={form.due} onChange={set("due")} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="r-cur">Currency</Label>
        <Input id="r-cur" value={form.currency} onChange={set("currency")} placeholder="Default" maxLength={3} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="r-cat">Category</Label>
        <Select id="r-cat" value={form.categoryId} onChange={set("categoryId")}>
          <option value="">None</option>
          {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
      </div>
      <div className="space-y-1 md:col-span-2">
        <Label htmlFor="r-msg">Message to suppliers</Label>
        <Textarea id="r-msg" value={form.message} onChange={set("message")} />
      </div>
      <div><Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create draft RFQ"}</Button></div>
    </form>
  );
}

export default function RfqsPage() {
  const me = useMe();
  const router = useRouter();
  const [status, setStatus] = useState("");
  const [creating, setCreating] = useState(false);
  const list = useFetch<{ items: RfqListItem[]; total: number }>(`rfqs?limit=100${status ? `&status=${status}` : ""}`);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">RFQs</h1>
          <p className="mt-1 text-sm text-muted-foreground">Requests for quotation go out only after approval, and the award is always a recorded decision.</p>
        </div>
        {me.permissions.includes("rfqs:manage") && <Button onClick={() => setCreating(!creating)}>{creating ? "Close" : "New RFQ"}</Button>}
      </div>
      {creating && (
        <Card>
          <CardHeader>
            <CardTitle>New RFQ</CardTitle>
            <CardDescription>The RFQ starts as a draft you can edit before submitting it for approval.</CardDescription>
          </CardHeader>
          <CardContent><NewRfq onCreated={(id) => router.push(`/rfqs/${id}`)} /></CardContent>
        </Card>
      )}
      <Select aria-label="Filter by status" className="w-auto" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">All statuses</option>
        {Object.entries(RFQ_STATUS_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </Select>
      {list.error && <Alert variant="error">{list.error.message}</Alert>}
      <Card>
        <CardContent className="overflow-x-auto pt-5">
          <table className="w-full text-left text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">RFQ</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 font-medium">Suppliers</th>
                <th className="py-2 pr-4 font-medium">Quotes</th>
                <th className="py-2 pr-4 font-medium">Due</th>
                <th className="py-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {list.data?.items.map((r) => (
                <tr key={r.id}>
                  <td className="py-3 pr-4">
                    <Link href={`/rfqs/${r.id}`} className="font-medium text-primary hover:underline">{r.title}</Link>
                    <p className="font-mono text-xs text-muted-foreground">{r.number} · v{r.version}</p>
                  </td>
                  <td className="py-3 pr-4"><RfqStatusBadge status={r.status} /></td>
                  <td className="py-3 pr-4">{r.supplier_count}</td>
                  <td className="py-3 pr-4">{r.quote_count}</td>
                  <td className="py-3 pr-4">{r.response_due_date ?? "—"}</td>
                  <td className="whitespace-nowrap py-3 text-muted-foreground">{formatDate(r.created_at)}</td>
                </tr>
              ))}
              {list.data && list.data.items.length === 0 && (
                <tr><td colSpan={6} className="py-8 text-center text-muted-foreground">No RFQs yet.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
