"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Inbox, X } from "lucide-react";
import { api, errorMessage, useFetch } from "@/lib/api";
import { CATEGORIES, CONDITIONS, money, REGIONS, SHIPPING, tomorrowPlus, type Lead, type SupplierLot, type Thread } from "@/lib/marketplace";
import { formatDate } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { SellerBadges } from "@/components/marketplace/parts";
import { useMe } from "@/components/me-context";

function ProposeDialog({ lead, lots, onClose }: { lead: Lead | null; lots: SupplierLot[]; onClose: () => void }) {
  const router = useRouter();
  const ref = useRef<HTMLDialogElement>(null);
  const [lotId, setLotId] = useState("");
  const [price, setPrice] = useState("");
  const [shipping, setShipping] = useState("buyer_freight");
  const [close, setClose] = useState(tomorrowPlus(7));
  const [terms, setTerms] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (lead && !dialog.open) {
      setProblem(null);
      setLotId(lots[0]?.id ?? "");
      setPrice(lots[0]?.asking_price ?? "");
      dialog.showModal();
    }
    if (!lead && dialog.open) dialog.close();
  }, [lead, lots]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!lead) return;
    if (!lotId) return setProblem("Choose one of your live lots.");
    if (!(Number(price) > 0)) return setProblem("Enter a price above zero.");
    setBusy(true);
    setProblem(null);
    try {
      const thread = await api<Thread>(`supplier/requests/${lead.id}/proposals`, {
        body: { lot_id: lotId, offered_price: Number(price).toFixed(2), shipping_terms: shipping, target_close_date: close, custom_terms: terms.trim() || null },
      });
      router.push(`/rfq/${thread.id}`);
    } catch (err) {
      setProblem(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="propose-title"
      className="w-[min(32rem,calc(100vw-2rem))] rounded-xl border bg-card p-0 text-card-foreground shadow-xl backdrop:bg-foreground/40">
      <form onSubmit={submit} className="space-y-4 p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="propose-title" className="text-lg font-semibold">Propose a lot</h2>
            <p className="mt-1 text-sm text-muted-foreground">For: {lead?.title}. The buyer sees your alias and badges, not your company.</p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="h-4 w-4" aria-hidden /></Button>
        </div>
        {problem && <Alert variant="error">{problem}</Alert>}
        <div className="space-y-2">
          <Label htmlFor="lot">Lot</Label>
          <Select id="lot" value={lotId} onChange={(e) => { setLotId(e.target.value); setPrice(lots.find((l) => l.id === e.target.value)?.asking_price ?? ""); }}>
            {lots.map((l) => <option key={l.id} value={l.id}>{l.title} ({l.total_units.toLocaleString()} units, {money(l.asking_price)})</option>)}
          </Select>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="p-price">Price ($)</Label>
            <Input id="p-price" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="p-close">Offer valid until</Label>
            <Input id="p-close" type="date" min={new Date().toISOString().slice(0, 10)} value={close} onChange={(e) => setClose(e.target.value)} />
          </div>
        </div>
        <fieldset className="space-y-1">
          <legend className="text-sm font-medium">Freight</legend>
          {Object.entries(SHIPPING).map(([k, v]) => (
            <label key={k} className="flex items-center gap-2 text-sm"><input type="radio" name="p-ship" className="accent-primary" checked={shipping === k} onChange={() => setShipping(k)} />{v}</label>
          ))}
        </fieldset>
        <div className="space-y-2">
          <Label htmlFor="p-terms">Terms (optional)</Label>
          <Textarea id="p-terms" rows={2} maxLength={2000} value={terms} onChange={(e) => setTerms(e.target.value)} />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={busy}>{busy ? "Sending…" : "Send proposal"}</Button>
        </div>
      </form>
    </dialog>
  );
}

export default function LeadsPage() {
  const me = useMe();
  const { data, error, loading } = useFetch<Lead[]>("supplier/requests");
  const { data: lots } = useFetch<SupplierLot[]>("supplier/lots");
  const [target, setTarget] = useState<Lead | null>(null);
  const live = (lots ?? []).filter((l) => l.status === "published");
  const canTrade = me.permissions.includes("marketplace:trade");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Buyer requests</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          What buyers are looking for right now, best matches for your inventory first. Answer with one of your live lots; there is no fee per lead.
        </p>
      </div>
      {live.length === 0 && lots && (
        <Alert>You need a live lot to send a proposal. <Link href="/supplier/manifests" className="font-semibold underline">Upload and publish a manifest</Link>.</Alert>
      )}
      {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {error && <Alert variant="error">{error.message}</Alert>}
      {data && data.length === 0 && (
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground"><Inbox className="h-6 w-6" aria-hidden />No open buyer requests right now.</div>
      )}
      <ul className="space-y-3">
        {data?.map((lead) => (
          <li key={lead.id} className="rounded-lg border bg-card p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <p className="font-medium">{lead.title}</p>
                <p className="text-sm text-muted-foreground">
                  {[lead.category && CATEGORIES[lead.category], lead.condition && CONDITIONS[lead.condition],
                    lead.quantity && `${lead.quantity.toLocaleString()} units`, lead.target_unit_price && `target ${money(lead.target_unit_price, 2)}/unit`,
                    lead.region && `deliver to the ${REGIONS[lead.region]}`, lead.needed_by && `by ${lead.needed_by}`].filter(Boolean).join(" · ")}
                </p>
                {lead.description && <p className="text-sm">{lead.description}</p>}
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><SellerBadges seller={lead.buyer} compact /> · posted {formatDate(lead.created_at)}</div>
              </div>
              <div className="flex flex-col items-end gap-2">
                {lead.match >= 0.6 && <Badge variant="default">Matches your inventory</Badge>}
                {lead.my_thread_id ? (
                  <Button asChild size="sm" variant="outline"><Link href={`/rfq/${lead.my_thread_id}`}>Open your proposal</Link></Button>
                ) : canTrade && (
                  <Button size="sm" onClick={() => setTarget(lead)} disabled={live.length === 0}>Propose a lot</Button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
      <ProposeDialog lead={target} lots={live} onClose={() => setTarget(null)} />
    </div>
  );
}
