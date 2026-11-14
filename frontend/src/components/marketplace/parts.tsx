import { BadgeCheck, Truck } from "lucide-react";
import { CONDITION_COLORS, CONDITIONS, type Seller } from "@/lib/marketplace";
import { cn } from "@/lib/utils";

/** Lot grade summary: units by condition as one stacked bar with a legend. */
export function ConditionBar({ mix, className }: { mix: Record<string, number>; className?: string }) {
  const entries = Object.entries(mix).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (!total) return null;
  return (
    <div className={className}>
      <div className="flex h-2 overflow-hidden rounded-full bg-secondary" role="img"
        aria-label={entries.map(([c, n]) => `${Math.round((n / total) * 100)}% ${CONDITIONS[c] ?? c}`).join(", ")}>
        {entries.map(([c, n]) => (
          <span key={c} className={cn("h-full", CONDITION_COLORS[c] ?? "bg-muted-foreground")} style={{ width: `${(n / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {entries.map(([c, n]) => (
          <li key={c} className="flex items-center gap-1.5">
            <span className={cn("h-2 w-2 rounded-full", CONDITION_COLORS[c] ?? "bg-muted-foreground")} aria-hidden />
            {CONDITIONS[c] ?? c} {Math.round((n / total) * 100)}%
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SellerBadges({ seller, compact = false }: { seller: Seller; compact?: boolean }) {
  const [identity, ...rest] = seller.badges;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium",
        seller.verified ? "bg-primary/10 text-primary" : "bg-secondary text-secondary-foreground")}>
        {seller.verified && <BadgeCheck className="h-3.5 w-3.5" aria-hidden />}
        {identity ?? seller.alias}
      </span>
      {!compact && rest.map((b) => (
        <span key={b} className="rounded-full bg-secondary px-2 py-0.5 text-secondary-foreground">{b}</span>
      ))}
    </div>
  );
}

export function FreightTags({ liftgate, dock, pickup }: { liftgate: boolean; dock: boolean; pickup: boolean }) {
  const tags = [liftgate && "Liftgate required", dock && "Dock-high only", pickup && "Local pickup"].filter(Boolean) as string[];
  if (!tags.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      <Truck className="h-3.5 w-3.5" aria-hidden />
      {tags.join(" · ")}
    </div>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "good" | "bad" }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("mt-0.5 font-mono text-lg font-semibold tabular-nums", tone === "good" && "text-primary", tone === "bad" && "text-destructive")}>{value}</dd>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
