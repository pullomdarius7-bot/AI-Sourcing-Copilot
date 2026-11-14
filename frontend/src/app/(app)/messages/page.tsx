"use client";

import Link from "next/link";
import { MessagesSquare } from "lucide-react";
import { useFetch } from "@/lib/api";
import { CLOSED_REASON, money, STATUS_LABEL, type ThreadSummary } from "@/lib/marketplace";
import { cn, formatDate } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { SellerBadges } from "@/components/marketplace/parts";

export default function MessagesPage() {
  const { data, error, loading } = useFetch<ThreadSummary[]>("rfq-threads");
  const waiting = data?.filter((t) => t.awaiting === "you").length ?? 0;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Offers & messages</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Every negotiation in one place. Identities stay masked until an offer is accepted.
          {waiting > 0 && <strong className="ml-1 text-foreground">{waiting} waiting for your answer.</strong>}
        </p>
      </div>
      {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {error && <Alert variant="error">{error.message}</Alert>}
      {data && data.length === 0 && (
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
          <MessagesSquare className="h-6 w-6" aria-hidden />
          No negotiations yet.
        </div>
      )}
      <ul className="space-y-2">
        {data?.map((t) => (
          <li key={t.id}>
            <Link href={`/rfq/${t.id}`} className={cn("flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4 hover:border-primary/50",
              t.awaiting === "you" && "border-l-4 border-l-primary")}>
              <div className="min-w-0 space-y-1">
                <p className="truncate font-medium">{t.lot_title}</p>
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>{t.my_side === "buyer" ? "You are buying from" : "Offer from"}</span>
                  <SellerBadges seller={t.counterparty} compact />
                </div>
              </div>
              <div className="flex items-center gap-3 text-right">
                <div>
                  <p className="font-mono font-semibold">{money(t.agreed_price ?? t.offer_price, 2)}</p>
                  <p className="text-xs text-muted-foreground">{formatDate(t.last_event_at)}</p>
                </div>
                {t.status === "open" ? (
                  <Badge variant={t.awaiting === "you" ? "default" : "neutral"}>{t.awaiting === "you" ? "Your turn" : "Waiting"}</Badge>
                ) : (
                  <Badge variant={t.status === "accepted" ? "default" : "neutral"} title={t.closed_reason ? CLOSED_REASON[t.closed_reason] : undefined}>{STATUS_LABEL[t.status]}</Badge>
                )}
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
