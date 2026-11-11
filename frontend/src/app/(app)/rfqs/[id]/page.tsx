"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { History } from "@/components/history";
import { ComparisonPanel } from "@/components/rfq/comparison";
import { ItemsEditor } from "@/components/rfq/items";
import { Packages } from "@/components/rfq/packages";
import { Quotes } from "@/components/rfq/quotes";
import { RfqSuppliers } from "@/components/rfq/suppliers";
import { RfqStatusBadge } from "@/components/supplier-badges";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { DomainEvent, Rfq } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

const STEPS = [
  ["draft", "Draft"],
  ["pending_approval", "Approval"],
  ["approved", "Approved"],
  ["sent", "Out for quotation"],
  ["closed", "Closed"],
  ["awarded", "Awarded"],
] as const;

function Steps({ status }: { status: Rfq["status"] }) {
  const index = STEPS.findIndex(([s]) => s === status);
  return (
    <ol className="flex flex-wrap gap-2" aria-label="RFQ progress">
      {STEPS.map(([key, label], i) => (
        <li key={key} aria-current={key === status ? "step" : undefined}
          className={cn("rounded-full border px-3 py-1 text-xs",
            status === "cancelled" ? "border-input text-muted-foreground" :
            i < index ? "border-primary/40 text-primary" : i === index ? "border-primary bg-primary text-primary-foreground" : "border-input text-muted-foreground")}>
          {label}
        </li>
      ))}
    </ol>
  );
}

function DetailsForm({ rfq, onSaved }: { rfq: Rfq; onSaved: () => void }) {
  const initial = {
    title: rfq.title, due: rfq.response_due_date ?? "", currency: rfq.currency, message: rfq.message ?? "",
    requiredBy: rfq.required_by ?? "", incoterms: rfq.requested_incoterms ?? "", payment: rfq.requested_payment_terms ?? "",
  };
  const [form, setForm] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const dirty = (Object.keys(initial) as (keyof typeof initial)[]).some((k) => form[k] !== initial[k]);
  return (
    <form
      className="grid gap-3 md:grid-cols-[1fr_10rem_6rem]"
      onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        try {
          await api(`rfqs/${rfq.id}`, { method: "PATCH", body: {
            title: form.title, response_due_date: form.due || null, currency: form.currency, message: form.message || null,
            required_by: form.requiredBy || null, requested_incoterms: form.incoterms || null, requested_payment_terms: form.payment || null,
          } });
          onSaved();
        } catch (err) {
          setError(errorMessage(err));
        }
      }}
    >
      {error && <Alert variant="error" className="md:col-span-3">{error}</Alert>}
      <div className="space-y-1"><Label htmlFor="d-title">Title</Label><Input id="d-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
      <div className="space-y-1"><Label htmlFor="d-due">Quotation due</Label><Input id="d-due" type="date" value={form.due} onChange={(e) => setForm({ ...form, due: e.target.value })} /></div>
      <div className="space-y-1"><Label htmlFor="d-cur">Currency</Label><Input id="d-cur" maxLength={3} value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} /></div>
      <div className="space-y-1"><Label htmlFor="d-req">Delivery required by</Label><Input id="d-req" type="date" value={form.requiredBy} onChange={(e) => setForm({ ...form, requiredBy: e.target.value })} /></div>
      <div className="space-y-1"><Label htmlFor="d-inco">Incoterms requested</Label><Input id="d-inco" maxLength={3} placeholder="e.g. DAP" value={form.incoterms} onChange={(e) => setForm({ ...form, incoterms: e.target.value.toUpperCase() })} /></div>
      <div className="space-y-1"><Label htmlFor="d-pay">Payment terms requested</Label><Input id="d-pay" placeholder="e.g. Net 30" value={form.payment} onChange={(e) => setForm({ ...form, payment: e.target.value })} /></div>
      <p className="text-xs text-muted-foreground md:col-span-3">The required date and requested terms are sent to suppliers and used to flag late delivery and differing terms in the comparison.</p>
      <div className="space-y-1 md:col-span-3"><Label htmlFor="d-msg">Message to suppliers</Label><Textarea id="d-msg" value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} /></div>
      {dirty && <div><Button type="submit" size="sm">Save details</Button></div>}
    </form>
  );
}

export default function RfqPage() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const rfqFetch = useFetch<Rfq>(`rfqs/${id}`);
  const events = useFetch<DomainEvent[]>(`rfqs/${id}/events`);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const can = (p: string) => me.permissions.includes(p);

  function reload() {
    rfqFetch.reload();
    events.reload();
    setTick((t) => t + 1);
  }

  async function act(path: string, body?: unknown) {
    setError(null);
    try {
      await api(`rfqs/${id}/${path}`, { method: "POST", body });
      setReason("");
      reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (rfqFetch.error) return <Alert variant="error">{rfqFetch.error.message}</Alert>;
  const rfq = rfqFetch.data;
  if (!rfq) return <p className="text-sm text-muted-foreground">Loading…</p>;
  const draft = rfq.status === "draft";
  const editable = draft && can("rfqs:manage");
  const quotesVisible = ["sent", "closed", "awarded"].includes(rfq.status);

  return (
    <div className="space-y-6">
      <div>
        <Link href="/rfqs" className="text-sm text-primary hover:underline">← All RFQs</Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">{rfq.title}</h1>
            <p className="font-mono text-xs text-muted-foreground">
              {rfq.number} · version {rfq.version}{rfq.content_sha256 ? ` · approved content ${rfq.content_sha256.slice(0, 12)}…` : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <RfqStatusBadge status={rfq.status} />
            <Button asChild size="sm" variant="outline"><a href={`/api/rfqs/${rfq.id}/pdf`}>PDF</a></Button>
            <Button asChild size="sm" variant="outline"><a href={`/api/rfqs/${rfq.id}/export.json`}>JSON</a></Button>
          </div>
        </div>
        <div className="mt-3"><Steps status={rfq.status} /></div>
      </div>

      {error && <Alert variant="error">{error}</Alert>}
      {rfq.status === "cancelled" && <Alert variant="error">Cancelled: {rfq.decision_note}</Alert>}
      {draft && rfq.decision_note && <Alert variant="info">Returned for changes: {rfq.decision_note}</Alert>}
      {rfq.source_run_id && (
        <p className="text-sm text-muted-foreground">
          Lines were generated from <Link className="text-primary hover:underline" href={`/requirements/${rfq.source_run_id}`}>approved requirements</Link>.
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Next step</CardTitle>
          <CardDescription>
            {draft && "Complete the RFQ, then submit it. An approver who did not create or submit it must approve it before anything is sent."}
            {rfq.status === "pending_approval" && "Waiting for approval. The approver sees exactly what will be sent and to whom."}
            {rfq.status === "approved" && `Approved ${formatDate(rfq.approved_at)}${rfq.decision_note ? `: ${rfq.decision_note}` : ""}. Send it to the invited suppliers.`}
            {rfq.status === "sent" && `Sent ${formatDate(rfq.sent_at)}. Record quotes as they arrive, verify them, then compare.`}
            {rfq.status === "closed" && "Closed for new quotes. Finish verifying, compare, recommend a supplier and have the award approved."}
            {rfq.status === "awarded" && `Awarded ${formatDate(rfq.awarded_at)}.`}
            {rfq.status === "cancelled" && "This RFQ is cancelled."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {draft && rfq.readiness.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-caution">{rfq.readiness.map((r) => <li key={r}>{r}</li>)}</ul>
          )}
          <div className="flex flex-wrap items-end gap-2">
            {draft && can("rfqs:manage") && <Button disabled={rfq.readiness.length > 0} onClick={() => act("submit")}>Submit for approval</Button>}
            {rfq.status === "pending_approval" && can("rfqs:approve") && <Button onClick={() => act("approve", { note: reason || null })}>Approve</Button>}
            {rfq.status === "approved" && can("rfqs:manage") && <Button onClick={() => act("send")}>Send to {rfq.suppliers.length} supplier(s)</Button>}
            {rfq.status === "sent" && can("rfqs:manage") && <Button variant="outline" onClick={() => act("close", { note: reason || null })}>Close for quotes</Button>}
            {["pending_approval", "approved"].includes(rfq.status) && can("rfqs:approve") && (
              <Button variant="outline" disabled={reason.trim().length < 3} onClick={() => act("return", { reason })}>Return to draft</Button>
            )}
            {!["awarded", "cancelled"].includes(rfq.status) && can("rfqs:manage") && (
              <Button variant="destructive" disabled={reason.trim().length < 3} onClick={() => act("cancel", { reason })}>Cancel RFQ</Button>
            )}
            {(can("rfqs:approve") || can("rfqs:manage")) && !["awarded", "cancelled"].includes(rfq.status) && (
              <Input aria-label="Note or reason" placeholder="Note / reason (required to return or cancel)" className="max-w-sm" value={reason} onChange={(e) => setReason(e.target.value)} />
            )}
          </div>
        </CardContent>
      </Card>

      {editable && (
        <Card>
          <CardHeader><CardTitle>Details</CardTitle></CardHeader>
          <CardContent><DetailsForm key={rfq.version + rfq.title} rfq={rfq} onSaved={reload} /></CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Requirement lines</CardTitle>
          <CardDescription>Values the source did not state are sent as questions to suppliers, never filled in.</CardDescription>
        </CardHeader>
        <CardContent><ItemsEditor rfqId={rfq.id} items={rfq.items} editable={editable} onChanged={reload} /></CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Suppliers</CardTitle>
          <CardDescription>Every invited supplier must be qualified when the RFQ is submitted, approved and sent.</CardDescription>
        </CardHeader>
        <CardContent><RfqSuppliers rfq={rfq} editable={editable} onChanged={reload} /></CardContent>
      </Card>

      {!draft && rfq.status !== "pending_approval" && (
        <Card>
          <CardHeader><CardTitle>Email packages</CardTitle></CardHeader>
          <CardContent><Packages key={tick} rfq={rfq} canManage={can("rfqs:manage")} /></CardContent>
        </Card>
      )}

      {quotesVisible && (
        <Card>
          <CardHeader>
            <CardTitle>Quotes</CardTitle>
            <CardDescription>Only verified quotes and confirmed line matches are used in the comparison.</CardDescription>
          </CardHeader>
          <CardContent><Quotes rfq={rfq} canEdit={can("quotes:manage")} onChanged={reload} /></CardContent>
        </Card>
      )}

      {quotesVisible && (
        <Card>
          <CardHeader>
            <CardTitle>Comparison</CardTitle>
            <CardDescription>Deterministic scores with the reasoning shown for every supplier. Currencies and units are converted only with recorded rates and exact rules.</CardDescription>
          </CardHeader>
          <CardContent>
            <ComparisonPanel key={tick} rfq={rfq} canRun={can("comparisons:run")} canApprove={can("rfqs:approve")} meId={me.id} onAwarded={reload} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>History</CardTitle></CardHeader>
        <CardContent><History events={events.data ?? []} /></CardContent>
      </Card>
    </div>
  );
}
