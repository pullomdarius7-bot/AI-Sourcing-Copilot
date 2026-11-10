"""Unit canonicalisation and exact conversion factors.

A factor F from unit A to unit B means: 1 A = F B. Quantities multiply by F; prices per unit divide by F.
"""
from dataclasses import dataclass
from decimal import Decimal

D = Decimal

# Factor to the dimension's base unit (pcs, kg, m, m2, m3). Imperial factors are the exact legal definitions.
DIMENSIONS: dict[str, dict[str, Decimal]] = {
    "count": {"pcs": D(1), "pair": D(2), "dozen": D(12), "hundred": D(100), "thousand": D(1000)},
    "mass": {"mg": D("0.000001"), "g": D("0.001"), "kg": D(1), "t": D(1000), "lb": D("0.45359237"), "oz": D("0.028349523125")},
    "length": {"mm": D("0.001"), "cm": D("0.01"), "m": D(1), "km": D(1000), "in": D("0.0254"), "ft": D("0.3048"), "yd": D("0.9144")},
    "area": {"mm2": D("0.000001"), "cm2": D("0.0001"), "m2": D(1), "in2": D("0.00064516"), "ft2": D("0.09290304")},
    "volume": {"ml": D("0.000001"), "l": D("0.001"), "m3": D(1), "gal": D("0.003785411784")},
}
ALIASES = {
    "pc": "pcs", "pce": "pcs", "pces": "pcs", "piece": "pcs", "pieces": "pcs", "ea": "pcs", "each": "pcs", "stk": "pcs",
    "stück": "pcs", "unit": "pcs", "units": "pcs", "nos": "pcs", "no": "pcs", "pairs": "pair", "pr": "pair",
    "doz": "dozen", "dz": "dozen", "dozens": "dozen",
    "milligram": "mg", "milligrams": "mg", "gram": "g", "grams": "g", "gr": "g", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "kilogram": "kg", "kilograms": "kg", "tonne": "t", "tonnes": "t", "mt": "t", "metric ton": "t", "lbs": "lb",
    "pound": "lb", "pounds": "lb", "ounce": "oz", "ounces": "oz",
    "millimeter": "mm", "millimeters": "mm", "millimetre": "mm", "millimetres": "mm", "centimeter": "cm",
    "centimeters": "cm", "centimetre": "cm", "centimetres": "cm", "meter": "m", "meters": "m", "metre": "m",
    "metres": "m", "mtr": "m", "lm": "m", "kilometer": "km", "kilometre": "km", "inch": "in", "inches": "in", '"': "in",
    "foot": "ft", "feet": "ft", "'": "ft", "yard": "yd", "yards": "yd",
    "mm²": "mm2", "sqmm": "mm2", "cm²": "cm2", "sqcm": "cm2", "m²": "m2", "sqm": "m2", "sq m": "m2", "square meter": "m2",
    "square metre": "m2", "in²": "in2", "sqin": "in2", "ft²": "ft2", "sqft": "ft2", "sq ft": "ft2",
    "millilitre": "ml", "milliliter": "ml", "litre": "l", "litres": "l", "liter": "l", "liters": "l", "ltr": "l", "lt": "l",
    "m³": "m3", "cbm": "m3", "cubic meter": "m3", "cubic metre": "m3", "gallon": "gal", "gallons": "gal", "us gal": "gal",
}
_BY_UNIT = {unit: (dim, factor) for dim, units in DIMENSIONS.items() for unit, factor in units.items()}


def canonical(unit: str | None) -> str | None:
    if unit is None:
        return None
    u = " ".join(str(unit).strip().lower().rstrip(".").split())
    if not u:
        return None
    return ALIASES.get(u, u)


def dimension(unit: str | None) -> str | None:
    c = canonical(unit)
    return _BY_UNIT[c][0] if c in _BY_UNIT else None


@dataclass(frozen=True)
class Conversion:
    factor: Decimal  # 1 from-unit = factor to-units
    path: str  # human-readable explanation of how the factor was found
    custom: bool  # an organization-defined conversion was used


def _builtin(a: str, b: str) -> Decimal | None:
    if a in _BY_UNIT and b in _BY_UNIT and _BY_UNIT[a][0] == _BY_UNIT[b][0]:
        return _BY_UNIT[a][1] / _BY_UNIT[b][1]
    return None


def factor(from_unit: str | None, to_unit: str | None, custom: list[dict] | None = None) -> Conversion | None:
    """Exact factor, or None when the units cannot be related without an assumption.

    custom: [{"from_unit", "to_unit", "factor"}] where 1 from_unit = factor to_unit (e.g. 1 roll = 50 m).
    Search order: identical, built-in, custom (direct or inverse), custom followed or preceded by one built-in step.
    """
    a, b = canonical(from_unit), canonical(to_unit)
    if a is None or b is None:
        return None
    if a == b:
        return Conversion(D(1), f"{a} = {b}", False)
    builtin = _builtin(a, b)
    if builtin is not None:
        return Conversion(builtin, f"1 {a} = {_fmt(builtin)} {b} (standard)", False)
    rules = [(canonical(r["from_unit"]), canonical(r["to_unit"]), D(str(r["factor"]))) for r in custom or []]
    rules = [(x, y, f) for x, y, f in rules if x and y and f > 0]
    for x, y, f in rules:
        if (x, y) == (a, b):
            return Conversion(f, f"1 {a} = {_fmt(f)} {b} (organization rule)", True)
        if (x, y) == (b, a):
            return Conversion(D(1) / f, f"1 {b} = {_fmt(f)} {a} (organization rule, inverted)", True)
    for x, y, f in rules:
        for src, dst, step in ((x, y, f), (y, x, D(1) / f)):
            if src == a:
                tail = _builtin(dst, b)
                if tail is not None:
                    total = step * tail
                    return Conversion(total, f"1 {a} = {_fmt(step)} {dst} (organization rule) = {_fmt(total)} {b}", True)
            if dst == b:
                head = _builtin(a, src)
                if head is not None:
                    total = head * step
                    return Conversion(total, f"1 {a} = {_fmt(head)} {src} = {_fmt(total)} {b} (organization rule)", True)
    return None


def _fmt(value: Decimal) -> str:
    text = format(value.quantize(D("0.000000001")).normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
