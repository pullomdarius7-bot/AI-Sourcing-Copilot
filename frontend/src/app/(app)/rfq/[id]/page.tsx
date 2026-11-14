"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, CircleSlash, Handshake, Lock, ShieldAlert, ShieldCheck } from "lucide-react";
import { api, errorMessage, useFetch } from "@/lib/api";
import { CLOSED_REASON, money, SHIPPING, STATUS_LABEL, type Thread, type ThreadEvent } from "@/lib/marketplace";
import { cn, formatDate } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { SellerBadges } from "@/components/marketplace/parts";
import { OfferDialog } from "@/components/marketplace/offer-dialog";
import { useMe } from "@/components/me-context";

const KIND: Record<string, string> = {
  offer: "made an offer", counter: "countered", accept: "accepted the offer", decline: "declined the offer",
  withdraw: "withdrew their offer", auto_decline: "", closed: "", message: "",
};
const FLAG_TEXT: Record<string, string> = {
  email: "an email address", phone: "a phone number", link: "a link", payment_link: "a payment link",
  payment_handle: "a payment handle", handle: "a social handle", company_name: "a company name",
};

function OfferCard({ offer }: { offer: NonNullable<ThreadEvent["offer"]> }) {
  return (
    <dl className="mt-2 grid gap-x-6 gap-y-1 rounded-md border bg-background p-3 text-sm sm:grid-cols-3">
      <div><dt className="text-xs text-muted-foreground">Price</dt><dd className="font-mono font-semibold">{money(offer.price, 2)}</dd></div>
      <div><dt className="text-xs text-muted-foreground">Freight</dt><dd>{offer.shipping_terms ? SHIPPING[offer.shipping_terms] ?? offer.shipping_terms : "To agree"}</dd></div>
      {offer.quantity && offer.unit_price && (
        <div className="sm:col-span-3"><dt className="text-xs text-muted-foreground">Quantity</dt><dd>{offer.quantity.toLocaleString()} units at {money(offer.unit_price, 2)} each</dd></div>
      )}
      <div><dt className="text-xs text-muted-foreground">Target close</dt><dd>{offer.target_close_date}</dd></div>
      {offer.custom_terms && <div className="sm:col-span-3"><dt className="text-xs text-muted-foreground">Terms</dt><dd>{offer.custom_terms}</dd></div>}
    </dl>
  );
}

function EventRow({ event }: { event: ThreadEvent }) {
  const removed = event.flags.filter((f) => f in FLAG_TEXT);
  if (event.side === "system") {
    return (
      <li className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
        <CircleSlash className="h-3.5 w-3.5" aria-hidden /> {event.body} <span>· {formatDate(event.created_at)}</span>
      </li>
    );
  }
  return (
    <li className={cn("flex", event.mine ? "justify-end" : "justify-start")}>
      <div className={cn("max-w-[85%] rounded-lg border px-4 py-3", event.mine ? "bg-primary/5" : "bg-card")}>
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{event.mine ? "You" : event.author}</span> {KIND[event.kind]} · {formatDate(event.created_at)}
        </p>
        {event.offer && <OfferCard offer={event.offer} />}
        {event.body && <p className="mt-1 whitespace-pre-wrap text-sm">{event.body}</p>}
        {removed.length > 0 && (
          <p className="mt-2 flex items-center gap-1 text-xs text-caution">
            <ShieldAlert className="h-3.5 w-3.5" aria-hidden /> Removed {removed.map((f) => FLAG_TEXT[f]).join(", ")} to keep the deal on the platform.
          </p>
        )}
        {event.flags.includes("payment_off_platform") && (
          <p className="mt-1 text-xs text-caution">Payments outside the platform are not protected.</p>
        )}
      </div>
    </li>
  );
}

export default function ThreadPage({ params }: { params: { id: string } }) {
  const me = useMe();
  const { data: thread, error, loading, reload } = useFetch<Thread>(`rfq-threads/${params.id}`);
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [counterOpen, setCounterOpen] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const canTrade = me.permissions.includes("marketplace:trade");

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [thread?.events.length]);
  useEffect(() => {
    const timer = setInterval(() => void reload(), 10000);
    return () => clearInterval(timer);
  }, [reload]);

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (error || !thread) return <Alert variant="error">{error?.message ?? "Conversation not found"}</Alert>;

  async function post(path: string, body?: unknown, key = path) {
    setBusy(key);
    setProblem(null);
    try {
      const out = await api<Thread>(`rfq-threads/${params.id}/${path}`, { method: "POST", body });
      setWarnings(out.warnings);
      await reload();
      return true;
    } catch (err) {
      setProblem(errorMessage(err));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    if (await post("messages", { body: message }, "message")) setMessage("");
  }

  const yourTurn = thread.awaiting === "you" && canTrade;
  const yourOffer = thread.status === "open" && thread.offer_by === thread.my_side && canTrade;
  const open = thread.status === "open" || thread.status === "accepted";
  const other = thread.my_side === "buyer" ? "seller" : "buyer";

  return (
    <div className="space-y-6">
      <Link href="/messages" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Offers & messages
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold">{thread.lot_title}</h1>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span>{thread.my_side === "buyer" ? "Seller" : "Buyer"}:</span>
            <SellerBadges seller={thread.counterparty} />
          </div>
          {thread.my_side === "buyer" && thread.lot_id && <Link href={`/marketplace/${thread.lot_id}`} className="text-sm text-primary hover:underline">View the lot</Link>}
          {thread.my_side === "buyer" && thread.request_id && !thread.lot_id && <Link href={`/sourcing/${thread.request_id}`} className="text-sm text-primary hover:underline">View your request</Link>}
        </div>
        <div className="text-right">
          <Badge variant={thread.status === "accepted" ? "default" : thread.status === "open" ? "neutral" : "caution"}>{STATUS_LABEL[thread.status]}</Badge>
          {thread.closed_reason && thread.status !== "accepted" && <p className="mt-1 text-xs text-muted-foreground">{CLOSED_REASON[thread.closed_reason]}</p>}
          <p className="mt-2 font-mono text-2xl font-semibold">{money(thread.agreed_price ?? thread.offer_price, 2)}</p>
          <p className="text-xs text-muted-foreground">{thread.status === "accepted" ? "Agreed price" : "On the table"}</p>
        </div>
      </div>

      {problem && <Alert variant="error">{problem}</Alert>}
      {warnings.map((w) => <Alert key={w}>{w}</Alert>)}

      {thread.status === "accepted" && thread.revealed && (
        <Card className="border-primary">
          <CardHeader><CardTitle className="flex items-center gap-2"><Handshake className="h-5 w-5 text-primary" aria-hidden /> Deal agreed: contact details released</CardTitle></CardHeader>
          <CardContent className="grid gap-6 text-sm sm:grid-cols-2">
            {(["seller", "buyer"] as const).map((party) => {
              const p = thread.revealed![party];
              return (
                <div key={party} className="space-y-1">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{party === thread.my_side.replace("supplier", "seller") ? "You" : party}</p>
                  <p className="font-medium">{p.company}</p>
                  {p.contact && <p>{p.contact.name} · <a className="text-primary hover:underline" href={`mailto:${p.contact.email}`}>{p.contact.email}</a></p>}
                  {party === "seller" && thread.revealed!.seller.pickup_address && (
                    <p className="whitespace-pre-wrap text-muted-foreground">
                      Pickup: {thread.revealed!.seller.pickup_address}
                      {thread.revealed!.seller.pickup_contact && `\n${thread.revealed!.seller.pickup_contact}`}
                      {thread.revealed!.seller.pickup_phone && ` · ${thread.revealed!.seller.pickup_phone}`}
                    </p>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {(yourTurn || yourOffer) && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 pt-5">
            {yourTurn ? (
              <>
                <p className="mr-auto text-sm">The {other} is waiting for your answer to {money(thread.offer_price, 2)}.</p>
                <Button onClick={() => void post("accept", {}, "accept")} disabled={busy !== null}><Check className="h-4 w-4" aria-hidden /> Accept</Button>
                <Button variant="outline" onClick={() => setCounterOpen(true)} disabled={busy !== null}>Counteroffer</Button>
                <Button variant="ghost" onClick={() => void post("decline", {}, "decline")} disabled={busy !== null}>Decline</Button>
              </>
            ) : (
              <>
                <p className="mr-auto text-sm text-muted-foreground">Waiting for the {other} to answer your offer.</p>
                <Button variant="ghost" onClick={() => void post("withdraw", {}, "withdraw")} disabled={busy !== null}>Withdraw offer</Button>
              </>
            )}
          </CardContent>
        </Card>
      )}

      <section aria-labelledby="history" className="space-y-3">
        <h2 id="history" className="sr-only">Negotiation history</h2>
        <ol className="space-y-3">
          {thread.events.map((e) => <EventRow key={e.id} event={e} />)}
        </ol>
        <div ref={bottom} />
      </section>

      {open && canTrade && (
        <form onSubmit={send} className="space-y-2">
          <Label htmlFor="message">Message</Label>
          <Textarea id="message" rows={3} maxLength={4000} value={message} onChange={(e) => setMessage(e.target.value)}
            placeholder={thread.status === "accepted" ? "Coordinate pickup and freight…" : "Ask about the lot, inspection, pickup windows…"} />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <Lock className="h-3.5 w-3.5" aria-hidden /> Emails, phone numbers and payment links are removed automatically.
            </p>
            <Button type="submit" disabled={busy !== null || !message.trim()}>{busy === "message" ? "Sending…" : "Send"}</Button>
          </div>
        </form>
      )}

      <p className={cn("flex items-center gap-1 text-xs", thread.chain_valid ? "text-muted-foreground" : "text-destructive")}>
        {thread.chain_valid ? <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> : <ShieldAlert className="h-3.5 w-3.5" aria-hidden />}
        {thread.chain_valid ? "Tamper-evident record: every offer and message is hash-chained." : "Warning: this record failed its integrity check."}
        {thread.chain_head && <span className="font-mono">#{thread.chain_head.slice(0, 12)}</span>}
      </p>

      <OfferDialog
        open={counterOpen}
        onClose={() => setCounterOpen(false)}
        path={`rfq-threads/${params.id}/counter`}
        title="Counteroffer"
        submitLabel="Send counteroffer"
        initialPrice={thread.offer_price ?? ""}
        initialShipping={thread.shipping_terms}
        initialClose={thread.target_close_date}
        counter
        onDone={(out) => { setCounterOpen(false); setWarnings(out.warnings); void reload(); }}
      />
    </div>
  );
}
