"""Currency conversion from exchange rates the organization records. No live feeds, no triangulation."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Rate:
    rate: Decimal  # 1 from-currency = rate to-currency
    valid_from: date
    path: str
    age_days: int


def find_rate(from_ccy: str, to_ccy: str, as_of: date, rates: list[dict]) -> Rate | None:
    """Latest rate valid on as_of for the pair, direct or inverted. rates: [{base, quote, rate, valid_from}],
    where 1 base = rate quote. Rates dated after as_of are ignored, so a re-run on the same day is reproducible."""
    a, b = from_ccy.upper(), to_ccy.upper()
    if a == b:
        return Rate(Decimal(1), as_of, f"{a} = {b}", 0)
    best: tuple[date, Decimal, str] | None = None
    for r in rates:
        valid = r["valid_from"] if isinstance(r["valid_from"], date) else date.fromisoformat(str(r["valid_from"]))
        if valid > as_of:
            continue
        base, quote, value = r["base"].upper(), r["quote"].upper(), Decimal(str(r["rate"]))
        if value <= 0:
            continue
        if (base, quote) == (a, b):
            candidate = (valid, value, f"1 {a} = {value} {b} (rate of {valid.isoformat()})")
        elif (base, quote) == (b, a):
            candidate = (valid, Decimal(1) / value, f"1 {b} = {value} {a} (rate of {valid.isoformat()}, inverted)")
        else:
            continue
        # latest date wins; on the same date a direct quote beats an inverted one, then the higher rate (stable order)
        key = (candidate[0], "inverted" not in candidate[2], candidate[1])
        if best is None or key > (best[0], "inverted" not in best[2], best[1]):
            best = candidate
    if best is None:
        return None
    return Rate(best[1], best[0], best[2], (as_of - best[0]).days)
