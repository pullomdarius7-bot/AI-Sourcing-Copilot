"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { CATEGORIES, CONDITIONS, money, REGIONS } from "@/lib/marketplace";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

type Req = {
  id: string; title: string; description: string; category: string; condition: string; quantity: number;
  target_unit_price: string; region: string; needed_by: string;
  my_quote: null | { unit_price: string; quantity_available: number; condition: string; available_by: string; notes: string | null; status: string };
};
type Form = { company_name: string; joined: boolean; discount_pct: number; requests: Req[] };

function QuoteForm({ token, req, onSent }: { token: string; req: Req; onSent: () => void }) {
  const [unit, setUnit] = useState(req.my_quote?.unit_price ?? "");
  const [qty, setQty] = useState(String(req.my_quote?.quantity_available ?? req.quantity));
  const [condition, setCondition] = useState(req.my_quote?.condition ?? req.condition);
  const [date, setDate] = useState(req.my_quote?.available_by ?? req.needed_by);
  const [notes, setNotes] = useState(req.my_quote?.notes ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const closed = req.my_quote && ["declined", "converted"].includes(req.my_quote.status);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!(Number(unit) > 0)) return setProblem("Enter your price per unit.");
    if (!(Number(qty) >= 1)) return setProblem("Enter how many units you can supply.");
    if (!date) return setProblem("Enter when the goods are available.");
    setBusy(true);
    setProblem(null);
    try {
      const out = await api<{ warnings: string[] }>("invites/quotes", {
        body: { token, request_id: req.id, unit_price: Number(unit).toFixed(2), quantity_available: Number(qty), condition, available_by: date, notes: notes.trim() || null },
      });
      setWarnings(out.warnings);
      onSent();
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{req.title}</CardTitle>
        <CardDescription>
          {[CATEGORIES[req.category], CONDITIONS[req.condition], `${req.quantity.toLocaleString()} units`, `target ${money(req.target_unit_price, 2)}/unit`,
            `deliver to the ${REGIONS[req.region]}`, `needed by ${req.needed_by}`].join(" · ")}
        </CardDescription>
        <p className="text-sm">{req.description}</p>
      </CardHeader>
      <CardContent>
        {req.my_quote && (
          <Alert variant="success" className="mb-4">
            <span className="inline-flex items-center gap-1"><CheckCircle2 className="h-4 w-4" aria-hidden />
              Quote sent: {req.my_quote.quantity_available.toLocaleString()} units at {money(req.my_quote.unit_price, 2)} each.</span>
            {req.my_quote.status === "interested" && " The buyer wants to go ahead: register free to read their reply and continue."}
            {req.my_quote.status === "declined" && " The buyer declined this quote."}
          </Alert>
        )}
        {warnings.map((w) => <Alert key={w} className="mb-2">{w}</Alert>)}
        {problem && <Alert variant="error" className="mb-4">{problem}</Alert>}
        {!closed && (
          <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2" noValidate>
            <div className="space-y-1">
              <Label htmlFor={`unit-${req.id}`}>Your price per unit ($)</Label>
              <Input id={`unit-${req.id}`} inputMode="decimal" value={unit} onChange={(e) => setUnit(e.target.value)} required />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`qty-${req.id}`}>Units you can supply</Label>
              <Input id={`qty-${req.id}`} type="number" min={1} value={qty} onChange={(e) => setQty(e.target.value)} required />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`cond-${req.id}`}>Condition</Label>
              <Select id={`cond-${req.id}`} value={condition} onChange={(e) => setCondition(e.target.value)}>
                {Object.entries(CONDITIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor={`date-${req.id}`}>Available by</Label>
              <Input id={`date-${req.id}`} type="date" min={new Date().toISOString().slice(0, 10)} value={date} onChange={(e) => setDate(e.target.value)} required />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor={`notes-${req.id}`}>Notes (optional)</Label>
              <Textarea id={`notes-${req.id}`} rows={2} maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)}
                placeholder="Packaging, MOQ, shipping options… (contact details are removed: talk to the buyer on the platform)" />
            </div>
            <div className="sm:col-span-2">
              <Button type="submit" disabled={busy}>{busy ? "Sending…" : req.my_quote ? "Update quote" : "Send free quote"}</Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

/** Public page linked from a supplier invitation: quote for free, without an account. */
export default function GuestQuotePage() {
  const [token, setToken] = useState<string | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async (t: string) => {
    try {
      setForm(await api<Form>(`invites/quote-form?token=${encodeURIComponent(t)}`));
    } catch (err) {
      setProblem(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("token");
    if (!t) {
      setProblem("This link is missing its token.");
      return;
    }
    setToken(t);
    void load(t);
  }, [load]);

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-5 py-10">
      <p className="text-sm font-semibold">Sourcing Copilot</p>
      {problem && <Alert variant="error">{problem}</Alert>}
      {form && token && (
        <>
          <div>
            <h1 className="text-2xl font-semibold">Send a free quote</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Hello {form.company_name} team. Verified buyers are looking for these products. Quoting is free and needs no account; if a buyer
              wants to go ahead, you register for free to continue the conversation and close the deal on the platform.
            </p>
          </div>
          {form.joined ? (
            <Alert>Your business is registered. <Link href="/login" className="font-semibold underline">Sign in</Link> to answer buyers.</Alert>
          ) : (
            <Alert>
              <strong>{form.discount_pct}% off your first month</strong> of the Verified Supplier plan when you join through this invitation.{" "}
              <Link href={`/register?role=supplier&invite=${encodeURIComponent(token)}`} className="font-semibold underline">Register free</Link>
            </Alert>
          )}
          {form.requests.length === 0 && <p className="text-sm text-muted-foreground">These buyer requests have closed. New ones will be sent to you.</p>}
          {!form.joined && form.requests.map((r) => <QuoteForm key={r.id} token={token} req={r} onSent={() => void load(token)} />)}
          <p className="text-xs text-muted-foreground">
            Don&apos;t want these emails? <Link href={`/unsubscribe?token=${encodeURIComponent(token)}`} className="underline">Unsubscribe</Link>.
          </p>
        </>
      )}
    </main>
  );
}
