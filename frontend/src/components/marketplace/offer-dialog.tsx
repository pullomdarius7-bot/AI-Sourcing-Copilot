"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { SHIPPING, tomorrowPlus, type Thread } from "@/lib/marketplace";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/** "Submit Offer / Request Quote" for a new negotiation, or a counteroffer inside one. */
export function OfferDialog({
  open, onClose, path, title, submitLabel, initialPrice, initialShipping, initialClose, priceHint, onDone, counter = false,
}: {
  open: boolean; onClose: () => void; path: string; title: string; submitLabel: string; initialPrice?: string;
  initialShipping?: string | null; initialClose?: string | null; priceHint?: string; onDone: (thread: Thread) => void;
  counter?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [price, setPrice] = useState(initialPrice ?? "");
  const [shipping, setShipping] = useState("buyer_freight");
  const [close, setClose] = useState(tomorrowPlus(7));
  const [terms, setTerms] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setProblem(null);
      setPrice(initialPrice ?? "");
      setShipping(initialShipping ?? "buyer_freight");
      if (initialClose && initialClose >= new Date().toISOString().slice(0, 10)) setClose(initialClose);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open, initialPrice, initialShipping, initialClose]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const value = Number(price);
    if (!price || Number.isNaN(value) || value <= 0) {
      setProblem("Enter an offer price above zero.");
      return;
    }
    if (close < new Date().toISOString().slice(0, 10)) {
      setProblem("The target close date cannot be in the past.");
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      const thread = await api<Thread>(path, {
        method: "POST",
        body: { offered_price: value.toFixed(2), shipping_terms: shipping, target_close_date: close, custom_terms: terms.trim() || null },
      });
      onDone(thread);
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="offer-title"
      className="w-[min(32rem,calc(100vw-2rem))] rounded-xl border bg-card p-0 text-card-foreground shadow-xl backdrop:bg-foreground/40">
      <form onSubmit={submit} className="space-y-5 p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="offer-title" className="text-lg font-semibold">{title}</h2>
            <p className="mt-1 text-sm text-muted-foreground">Identities stay private until an offer is accepted. Contact details in your terms are removed.</p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="h-4 w-4" aria-hidden /></Button>
        </div>
        {problem && <Alert variant="error">{problem}</Alert>}
        <div className="space-y-2">
          <Label htmlFor="offer-price">Offered price ($)</Label>
          <Input id="offer-price" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} required autoFocus aria-describedby={priceHint ? "price-hint" : undefined} />
          {priceHint && <p id="price-hint" className="text-xs text-muted-foreground">{priceHint}</p>}
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Desired shipping terms</legend>
          {Object.entries(SHIPPING).map(([key, label]) => (
            <label key={key} className="flex items-center gap-2 text-sm">
              <input type="radio" name="shipping" value={key} checked={shipping === key} onChange={() => setShipping(key)} className="accent-primary" />
              {label}
            </label>
          ))}
        </fieldset>
        <div className="space-y-2">
          <Label htmlFor="offer-close">Target close date</Label>
          <Input id="offer-close" type="date" min={new Date().toISOString().slice(0, 10)} value={close} onChange={(e) => setClose(e.target.value)} required className="max-w-[12rem]" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="offer-terms">{counter ? "Terms (optional; the previous terms apply if empty)" : "Custom terms (optional)"}</Label>
          <Textarea id="offer-terms" rows={3} maxLength={2000} value={terms} onChange={(e) => setTerms(e.target.value)} placeholder="Pickup window, inspection, payment timing…" />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={busy}>{busy ? "Sending…" : submitLabel}</Button>
        </div>
      </form>
    </dialog>
  );
}
