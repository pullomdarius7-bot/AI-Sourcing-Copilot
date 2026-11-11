"use client";

import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { Billing } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const STATUS: Record<Billing["subscription_status"], { label: string; variant: "default" | "neutral" | "caution" | "destructive" }> = {
  none: { label: "Free trial", variant: "neutral" },
  trialing: { label: "Trial, card on file", variant: "default" },
  active: { label: "Active", variant: "default" },
  past_due: { label: "Payment failed", variant: "destructive" },
  cancelled: { label: "Cancelled", variant: "caution" },
};
const KIND: Record<string, string> = {
  trial_grant: "Free trial", plan_grant: "Monthly credits", purchase: "Purchase", usage: "Usage",
  refund: "Refund", adjustment: "Adjustment", expiry: "Expired",
};
const ACTION: Record<string, string> = {
  manifest_extraction: "Manifest extraction", deep_analysis: "Full analysis run", assistant_answer: "Assistant answer",
};

export default function BillingPage() {
  const { data, error, loading } = useFetch<Billing>("billing");
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const result = new URLSearchParams(window.location.search).get("checkout");
    if (result === "success") setNotice("Thank you. Your subscription is being activated; credits appear here within a minute.");
    if (result === "cancelled") setNotice("Checkout was cancelled. Nothing was charged.");
  }, []);

  async function go(key: string, path: string, body?: unknown) {
    setBusy(key);
    setProblem(null);
    try {
      const { url } = await api<{ url: string }>(path, { method: "POST", body });
      window.location.href = url;
    } catch (err) {
      setProblem(errorMessage(err));
      setBusy(null);
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (error || !data) return <Alert variant="error">{error?.message ?? "Could not load billing."}</Alert>;

  const subscribed = ["trialing", "active", "past_due"].includes(data.subscription_status);
  const status = STATUS[data.subscription_status];
  const current = data.plans.find((p) => p.key === data.plan);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Billing & credits</h1>
        <p className="mt-1 text-sm text-muted-foreground">One credit covers a manifest of up to 500 line items, or one full analysis run.</p>
      </div>
      {notice && <Alert variant="success">{notice}</Alert>}
      {problem && <Alert variant="error">{problem}</Alert>}

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardDescription>Credits remaining</CardDescription>
            <CardTitle className="text-3xl tabular-nums">{data.credit_balance}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {data.ai_access ? "Unused credits do not roll over to the next period." : "AI analysis is paused for this account."}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Plan</CardDescription>
            <CardTitle className="flex flex-wrap items-center gap-2">
              {current?.name ?? "No plan"} <Badge variant={status.variant}>{status.label}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {data.subscription_status === "active" && data.current_period_end && <>Renews {formatDate(data.current_period_end)}</>}
            {(data.subscription_status === "none" || data.subscription_status === "trialing") && data.trial_ends_at && (
              <>Trial {new Date(data.trial_ends_at) > new Date() ? "ends" : "ended"} {formatDate(data.trial_ends_at)}</>
            )}
            {data.subscription_status === "past_due" && "Update your payment method to resume."}
            {data.subscription_status === "cancelled" && "Choose a plan below to resubscribe."}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Payment & invoices</CardDescription>
            <CardTitle>Billing portal</CardTitle>
          </CardHeader>
          <CardContent>
            {data.can_manage && data.has_billing_profile ? (
              <Button variant="outline" size="sm" disabled={busy !== null} onClick={() => go("portal", "billing/create-portal-session")}>
                {busy === "portal" ? "Opening…" : "Manage billing"}
              </Button>
            ) : (
              <p className="text-sm text-muted-foreground">
                {data.can_manage ? "Available after you choose a plan." : "Only an administrator can manage billing."}
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <section aria-labelledby="plans-heading" className="space-y-3">
        <h2 id="plans-heading" className="text-base font-semibold">{subscribed ? "Your plan" : "Choose a plan"}</h2>
        <div className={cn("grid gap-4", data.plans.length > 1 && "md:grid-cols-2")}>
          {data.plans.map((plan) => {
            const isCurrent = plan.key === data.plan && subscribed;
            return (
              <Card key={plan.key} className={cn(isCurrent && "border-primary ring-1 ring-primary")}>
                <CardHeader>
                  <CardTitle>{plan.name}</CardTitle>
                  <CardDescription>
                    <span className="text-2xl font-semibold text-foreground">${plan.price_cents / 100}</span> / month
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <ul className="space-y-2 text-sm">
                    <li className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />{plan.monthly_credits} credits every month</li>
                    {plan.seats && <li className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />Up to {plan.seats} team seats</li>}
                  </ul>
                  {isCurrent ? (
                    <Badge variant="default">Current plan</Badge>
                  ) : subscribed ? (
                    data.can_manage && (
                      <Button variant="outline" size="sm" disabled={busy !== null} onClick={() => go("portal", "billing/create-portal-session")}>Switch in billing portal</Button>
                    )
                  ) : data.can_manage ? (
                    <div>
                      <Button disabled={busy !== null} onClick={() => go(plan.key, "billing/create-checkout-session", { plan: plan.key })}>
                        {busy === plan.key ? "Opening checkout…" : `Subscribe to ${plan.name}`}
                      </Button>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {data.checkout_trial_days
                          ? `Your trial continues: first charge in ${data.checkout_trial_days} ${data.checkout_trial_days === 1 ? "day" : "days"}.`
                          : "Charged today; credits are added as soon as payment succeeds."}
                      </p>
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">Ask an administrator to subscribe.</p>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      </section>

      <section aria-labelledby="activity-heading" className="space-y-3">
        <h2 id="activity-heading" className="text-base font-semibold">Credit activity</h2>
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b text-muted-foreground">
                <tr>
                  <th scope="col" className="px-5 py-3 font-medium">When</th>
                  <th scope="col" className="px-5 py-3 font-medium">What</th>
                  <th scope="col" className="px-5 py-3 text-right font-medium">Credits</th>
                  <th scope="col" className="px-5 py-3 text-right font-medium">Balance</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.transactions.map((t) => (
                  <tr key={t.id}>
                    <td className="whitespace-nowrap px-5 py-3 text-muted-foreground">{formatDate(t.created_at)}</td>
                    <td className="px-5 py-3">
                      {t.action ? ACTION[t.action] ?? t.action : KIND[t.kind] ?? t.kind}
                      {t.kind === "refund" && <span className="text-muted-foreground"> (refunded)</span>}
                      {t.state === "held" && <span className="text-muted-foreground"> (in progress)</span>}
                    </td>
                    <td className={cn("px-5 py-3 text-right font-mono tabular-nums", t.delta > 0 && "text-primary")}>{t.delta > 0 ? `+${t.delta}` : t.delta}</td>
                    <td className="px-5 py-3 text-right font-mono tabular-nums">{t.balance_after}</td>
                  </tr>
                ))}
                {data.transactions.length === 0 && (
                  <tr><td colSpan={4} className="px-5 py-6 text-center text-muted-foreground">No credit activity yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </section>
    </div>
  );
}
