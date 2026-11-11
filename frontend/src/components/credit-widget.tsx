import Link from "next/link";
import { Coins } from "lucide-react";
import type { Me } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export const LOW_CREDITS = 2;

/** Whole days left in the free trial, or null when the account is not on a trial. */
export function trialDaysLeft(me: Me): number | null {
  if (!me.trial_ends_at || !(me.subscription_status === "none" || me.subscription_status === "trialing")) return null;
  return Math.max(0, Math.ceil((new Date(me.trial_ends_at).getTime() - Date.now()) / 86_400_000));
}

/** Header widget: remaining credits, trial countdown, and the way to get more. */
export function CreditWidget({ me }: { me: Me }) {
  const days = trialDaysLeft(me);
  const low = me.credit_balance <= LOW_CREDITS || !me.ai_access;
  return (
    <div className="mr-auto flex items-center gap-3">
      <Link
        href="/billing"
        className={cn(
          "flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm",
          low ? "border-caution/40 bg-caution/10 text-caution" : "bg-background",
        )}
        aria-label={`${me.credit_balance} credits remaining. Open billing.`}
      >
        <Coins className="h-4 w-4" aria-hidden />
        <span className="font-semibold tabular-nums">{me.credit_balance}</span>
        <span className="hidden sm:inline">{me.credit_balance === 1 ? "credit" : "credits"} left</span>
        {days !== null && (
          <span className="border-l pl-2 text-xs">
            {days === 0 ? "Trial ended" : `Trial: ${days} ${days === 1 ? "day" : "days"} left`}
          </span>
        )}
      </Link>
      <Button asChild size="sm" variant={low ? "default" : "outline"}>
        <Link href="/billing">Add Credits / Upgrade</Link>
      </Button>
    </div>
  );
}

/** Persistent banner across the app when credits are nearly gone or AI is paused. */
export function CreditBanner({ me }: { me: Me }) {
  let message: string | null = null;
  if (me.subscription_status === "past_due") {
    message = "Your last payment failed, so AI analysis is paused. Update your payment method to resume.";
  } else if (me.subscription_status === "cancelled") {
    message = "Your subscription has ended. Choose a plan to keep analysing manifests.";
  } else if (!me.ai_access) {
    message = "Your free trial has ended. Choose a plan to keep analysing manifests.";
  } else if (me.credit_balance <= LOW_CREDITS) {
    message =
      me.credit_balance === 0
        ? "You have no credits left. New uploads will not be analysed until you add credits."
        : `Only ${me.credit_balance} ${me.credit_balance === 1 ? "credit" : "credits"} left.`;
  }
  if (!message) return null;
  return (
    <div role="status" className="flex flex-wrap items-center justify-between gap-3 border-b border-caution/30 bg-caution/10 px-6 py-2.5 text-sm text-caution">
      <p className="font-medium">{message}</p>
      <Link href="/billing" className="font-semibold underline underline-offset-4">Add Credits / Upgrade</Link>
    </div>
  );
}
