"""Deterministic money model for lots.

Two levels:
- estimate_lot: the public, free estimate shown on every listing (resale value from MSRP by condition and
  category, and the gross margin at the asking price).
- deep_analysis: the paid, itemized net profit model a buyer runs (fees, refurbishment, unsellable units,
  freight, a maximum bid at the buyer's target margin and a buy / negotiate / pass call). A model may refine
  the per-item resale value; everything else is arithmetic.

All figures are estimates and are labelled as such wherever they are shown.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

CENT = Decimal("0.01")

# Expected resale value as a share of MSRP, by condition grade.
RECOVERY = {"new": Decimal("0.55"), "like_new": Decimal("0.45"), "customer_return": Decimal("0.30"), "salvage": Decimal("0.10")}
# How well each category holds its value on the secondary market.
CATEGORY_FACTOR = {
    "apparel": Decimal("0.85"), "footwear": Decimal("0.90"), "electronics": Decimal("0.90"), "tools": Decimal("1.05"),
    "appliances": Decimal("0.95"), "home": Decimal("0.90"), "beauty": Decimal("0.80"), "toys": Decimal("0.90"),
    "sports": Decimal("0.95"), "general": Decimal("0.85"),
}
# Share of units that cannot be sold at all, by condition.
UNSELLABLE = {"new": Decimal("0.02"), "like_new": Decimal("0.05"), "customer_return": Decimal("0.15"), "salvage": Decimal("0.40")}
# Cleaning, testing, repackaging and repair, as a share of the revenue of the sellable units.
REFURBISHMENT = {"new": Decimal("0"), "like_new": Decimal("0.03"), "customer_return": Decimal("0.08"), "salvage": Decimal("0.20")}
CHANNEL_FEES = {"ebay": Decimal("0.1325"), "amazon": Decimal("0.15"), "direct": Decimal("0.03")}
CHANNEL_LABELS = {"ebay": "eBay", "amazon": "Amazon", "direct": "Direct / own store"}
# Inbound freight when the buyer does not give a quote.
FREIGHT_PER_PALLET = Decimal("225")
FREIGHT_TRUCKLOAD = Decimal("2800")
TRUCKLOAD_PALLETS = 26


def money(value: Decimal | float | int) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def resale_unit(unit_msrp: Decimal | None, condition: str, category: str) -> Decimal:
    if not unit_msrp:
        return Decimal("0")
    return money(unit_msrp * RECOVERY.get(condition, RECOVERY["customer_return"]) * CATEGORY_FACTOR.get(category, Decimal("0.85")))


@dataclass
class LotEstimate:
    item_count: int
    total_units: int
    total_msrp: Decimal
    est_resale: Decimal
    est_margin_pct: Decimal | None
    primary_category: str | None
    categories: list[str]
    conditions: list[str]
    condition_mix: dict[str, int]
    category_mix: dict[str, int]


def estimate_lot(items: list[Any], asking_price: Decimal | None) -> LotEstimate:
    units = msrp = resale = Decimal("0")
    by_condition: dict[str, int] = {}
    by_category_units: dict[str, int] = {}
    by_category_msrp: dict[str, Decimal] = {}
    for it in items:
        qty = int(it.quantity)
        line_msrp = (it.unit_msrp or Decimal("0")) * qty
        units += qty
        msrp += line_msrp
        resale += resale_unit(it.unit_msrp, it.condition, it.category) * qty
        by_condition[it.condition] = by_condition.get(it.condition, 0) + qty
        by_category_units[it.category] = by_category_units.get(it.category, 0) + qty
        by_category_msrp[it.category] = by_category_msrp.get(it.category, Decimal("0")) + line_msrp
    margin = None
    if asking_price and resale > 0:
        margin = ((resale - asking_price) / resale * 100).quantize(CENT, rounding=ROUND_HALF_UP)
    primary = max(by_category_msrp, key=lambda c: (by_category_msrp[c], by_category_units[c])) if by_category_msrp else None
    return LotEstimate(
        item_count=len(items), total_units=int(units), total_msrp=money(msrp), est_resale=money(resale),
        est_margin_pct=margin, primary_category=primary,
        categories=sorted(by_category_units), conditions=sorted(by_condition),
        condition_mix=dict(sorted(by_condition.items(), key=lambda kv: -kv[1])),
        category_mix=dict(sorted(by_category_units.items(), key=lambda kv: -kv[1])),
    )


def default_freight(lot_type: str | None, pallet_count: int | None) -> Decimal:
    if lot_type == "truckload":
        return FREIGHT_TRUCKLOAD
    pallets = pallet_count or (1 if lot_type in (None, "single_pallet") else 4)
    return FREIGHT_PER_PALLET * max(1, pallets)


def deep_analysis(
    items: list[dict[str, Any]], *, asking_price: Decimal, channel: str, target_margin_pct: Decimal,
    freight: Decimal, ai_resale: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Itemized net profit model. items: dicts with position, title, category, condition, quantity, unit_msrp.
    ai_resale: optional validated per-item model estimates {position: {"resale_unit", "velocity"}}."""
    fee_rate = CHANNEL_FEES[channel]
    total_msrp = sum((Decimal(str(i["unit_msrp"] or 0)) * i["quantity"] for i in items), Decimal("0"))
    rows = []
    totals = {k: Decimal("0") for k in ("revenue", "fees", "refurbishment", "freight", "cost", "net_profit")}
    units = 0
    for it in items:
        qty = int(it["quantity"])
        unit_msrp = Decimal(str(it["unit_msrp"] or 0))
        rules_value = resale_unit(unit_msrp, it["condition"], it["category"])
        model = (ai_resale or {}).get(it["position"])
        unit_value = Decimal(str(model["resale_unit"])) if model else rules_value
        sellable = Decimal(qty) * (1 - UNSELLABLE.get(it["condition"], Decimal("0.15")))
        revenue = money(unit_value * sellable)
        fees = money(revenue * fee_rate)
        refurb = money(revenue * REFURBISHMENT.get(it["condition"], Decimal("0.08")))
        share = (unit_msrp * qty / total_msrp) if total_msrp else (Decimal(1) / len(items))
        freight_alloc = money(freight * share)
        cost = money(asking_price * share)
        net = revenue - fees - refurb - freight_alloc - cost
        units += qty
        for key, value in (("revenue", revenue), ("fees", fees), ("refurbishment", refurb), ("freight", freight_alloc),
                           ("cost", cost), ("net_profit", net)):
            totals[key] += value
        rows.append({
            "position": it["position"], "title": it["title"], "category": it["category"], "condition": it["condition"],
            "quantity": qty, "unit_msrp": float(unit_msrp), "resale_unit": float(unit_value),
            "resale_source": "ai" if model else "rules", "velocity": (model or {}).get("velocity"),
            "sellable_units": float(sellable.quantize(Decimal("0.1"))), "revenue": float(revenue), "fees": float(fees),
            "refurbishment": float(refurb), "freight": float(freight_alloc), "cost": float(cost),
            "net_profit": float(money(net)),
        })
    revenue, net = totals["revenue"], totals["net_profit"]
    target = target_margin_pct / 100
    max_bid = money(revenue * (1 - target) - totals["fees"] - totals["refurbishment"] - totals["freight"])
    max_bid = max(max_bid, Decimal("0"))
    if asking_price <= max_bid:
        decision = "buy"
    elif asking_price <= max_bid * Decimal("1.15"):
        decision = "negotiate"
    else:
        decision = "pass"
    return {
        "items": rows,
        "totals": {
            "units": units, "msrp": float(money(total_msrp)), **{k: float(money(v)) for k, v in totals.items()},
            "roi_pct": float(money(net / asking_price * 100)) if asking_price else None,
            "net_margin_pct": float(money(net / revenue * 100)) if revenue else None,
        },
        "max_bid": float(max_bid),
        "decision": decision,
        "assumptions": {
            "channel": channel, "channel_label": CHANNEL_LABELS[channel], "fee_rate_pct": float(fee_rate * 100),
            "target_margin_pct": float(target_margin_pct), "freight": float(money(freight)),
            "asking_price": float(asking_price),
            "unsellable_pct": {k: float(v * 100) for k, v in UNSELLABLE.items()},
            "refurbishment_pct": {k: float(v * 100) for k, v in REFURBISHMENT.items()},
        },
    }
