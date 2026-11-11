export interface PublicPlan {
  key: "solo_buyer_monthly" | "enterprise_buyer_monthly" | "supplier_monthly";
  name: string;
  price_cents: number;
  monthly_credits: number;
  seats: number | null;
  topup_price_cents: number;
  account_types: string[];
}

/**
 * The public pricing page reads the price list from GET /api/v1/billing/plans (backend/app/billing/plans.py).
 * This copy is only used when the API cannot be reached; backend/tests/test_topups_and_seats.py fails if it
 * drifts from the catalog.
 */
export const FALLBACK_PLANS: PublicPlan[] = /* plans-json */ [
  {"key": "solo_buyer_monthly", "name": "Solo Operator", "price_cents": 7900, "monthly_credits": 25, "seats": 1, "topup_price_cents": 299, "account_types": ["solo_buyer", "org_buyer"]},
  {"key": "enterprise_buyer_monthly", "name": "Sourcing Team", "price_cents": 24900, "monthly_credits": 250, "seats": 5, "topup_price_cents": 99, "account_types": ["solo_buyer", "org_buyer"]},
  {"key": "supplier_monthly", "name": "Verified Supplier", "price_cents": 14900, "monthly_credits": 100, "seats": null, "topup_price_cents": 149, "account_types": ["supplier"]}
] /* end-plans-json */;

export function dollars(cents: number, exact = false) {
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: exact ? 2 : 0, maximumFractionDigits: exact ? 2 : 0,
  }).format(cents / 100);
}

export async function loadPublicPlans(): Promise<PublicPlan[]> {
  const api = process.env.API_INTERNAL_URL ?? "http://backend:8000";
  try {
    const res = await fetch(`${api}/api/v1/billing/plans`, { next: { revalidate: 300 } });
    if (res.ok) return (await res.json()) as PublicPlan[];
  } catch {
    // fall through to the bundled copy
  }
  return FALLBACK_PLANS;
}
