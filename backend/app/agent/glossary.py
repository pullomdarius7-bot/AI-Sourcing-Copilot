"""Built-in sourcing reference. Static, versioned and citable, so general questions get a sourced answer offline.

Method entries are generated from the analysis engine's own constants, so they cannot drift from the code.
"""
import re
from dataclasses import dataclass

from app.analysis.engine import DEFAULT_WEIGHTS, ENGINE_VERSION, PRIORITY_WEIGHT, SEVERITY_POINTS

GLOSSARY_VERSION = "2026.11"


@dataclass(frozen=True)
class Entry:
    key: str
    title: str
    terms: tuple[str, ...]
    text: str
    kind: str = "glossary"


_INCOTERMS = {
    "EXW": ("Ex Works", "The seller makes the goods available at its own premises. The buyer arranges and pays for loading, "
            "export clearance, main carriage and import, and carries the risk from the moment the goods are made available."),
    "FCA": ("Free Carrier", "The seller delivers the goods, cleared for export, to the carrier named by the buyer at a named "
            "place. Risk passes when the goods are handed to that carrier; the buyer pays main carriage and import."),
    "CPT": ("Carriage Paid To", "The seller pays carriage to the named destination, but risk passes to the buyer when the "
            "goods are handed to the first carrier. Insurance is not included."),
    "CIP": ("Carriage and Insurance Paid To", "As CPT, and the seller also buys cargo insurance, under Incoterms 2020 at the "
            "higher Institute Cargo Clauses (A) level. Risk passes at handover to the first carrier."),
    "DAP": ("Delivered at Place", "The seller bears cost and risk until the goods are ready for unloading at the named "
            "destination. The buyer unloads and handles import clearance and duties."),
    "DPU": ("Delivered at Place Unloaded", "As DAP, but the seller also unloads the goods at the destination. The buyer "
            "handles import clearance and duties. Replaced DAT in Incoterms 2020."),
    "DDP": ("Delivered Duty Paid", "The seller bears all cost and risk to the named destination, including import "
            "clearance and duties. The buyer unloads."),
    "FAS": ("Free Alongside Ship", "Sea and inland waterway only. The seller delivers the goods, cleared for export, "
            "alongside the vessel at the named port of shipment, where risk passes."),
    "FOB": ("Free on Board", "Sea and inland waterway only. The seller delivers the goods on board the vessel at the named "
            "port of shipment, cleared for export; risk passes once they are on board."),
    "CFR": ("Cost and Freight", "Sea and inland waterway only. The seller pays freight to the named destination port, but "
            "risk passes when the goods are on board at the port of shipment."),
    "CIF": ("Cost, Insurance and Freight", "As CFR, and the seller also buys minimum cargo insurance (Institute Cargo "
            "Clauses (C)). Risk passes when the goods are on board at the port of shipment."),
}

_TERMS = [
    Entry("incoterms", "Incoterms 2020", ("incoterms", "incoterm", "trade terms", "delivery terms"),
          "Incoterms 2020 are the International Chamber of Commerce's eleven standard trade terms. Each defines where "
          "delivery happens, where risk passes from seller to buyer and who pays which costs. They do not set the "
          "price, payment terms or transfer of ownership. Seven apply to any mode of transport (EXW, FCA, CPT, CIP, DAP, "
          "DPU, DDP) and four to sea and inland waterway only (FAS, FOB, CFR, CIF). Prices quoted on different Incoterms "
          "are not like-for-like until freight, insurance and duties are accounted for."),
    Entry("moq", "Minimum order quantity (MOQ)", ("moq", "minimum order quantity", "minimum order", "min qty"),
          "The smallest quantity a supplier will sell in one order. When the MOQ is above the quantity you need, the "
          "real cost is the price times the MOQ, not times your quantity, and the surplus has to be stored, used later "
          "or written off."),
    Entry("lead_time", "Lead time", ("lead time", "lead-time", "delivery time", "lead times"),
          "The time from placing an order to receiving the goods, usually stated in calendar days or weeks. Compare "
          "it with the date the goods are needed, and check whether it is counted to dispatch or to delivery at your "
          "site."),
    Entry("rfq", "Request for quotation (RFQ)", ("rfq", "request for quotation", "request for quote"),
          "A document asking selected suppliers to quote price and terms for a defined requirement. A good RFQ states "
          "quantities, units, specifications, required delivery date, delivery terms, payment terms and the response "
          "deadline, so the answers can be compared."),
    Entry("rfi", "Request for information (RFI)", ("rfi", "request for information"),
          "An early-stage request to learn what suppliers can offer, usually without prices. It is used to build a "
          "shortlist before an RFQ or RFP."),
    Entry("rfp", "Request for proposal (RFP)", ("rfp", "request for proposal"),
          "A request for a solution proposal where the approach matters as well as the price, typically for services "
          "or complex projects. Evaluation weighs method, capability and cost."),
    Entry("payment_terms", "Payment terms", ("payment terms", "net 30", "net 60", "net terms", "prepayment"),
          "When and how an invoice is paid. 'Net 30' means the full amount is due 30 days after the invoice date; "
          "'2/10 net 30' offers a 2 % discount for payment within 10 days. Longer terms improve the buyer's cash "
          "flow; prepayment shifts risk to the buyer."),
    Entry("quote_validity", "Quotation validity", ("validity", "valid until", "quote validity", "expiry"),
          "The date until which the supplier is bound by the quoted prices and terms. An award after that date needs "
          "the supplier to confirm or re-quote."),
    Entry("tco", "Total cost of ownership (TCO)", ("tco", "total cost of ownership", "landed cost"),
          "All costs of buying and using an item over its life: price, freight, duties, inventory, quality failures, "
          "maintenance and disposal. Landed cost is the part up to arrival at your site: price plus freight, "
          "insurance, duties and handling."),
    Entry("sourcing_strategies", "Single, sole and multiple sourcing", ("single source", "sole source", "dual source",
          "multiple sourcing", "single sourcing", "sole sourcing", "dual sourcing"),
          "Sole sourcing means only one supplier can supply; single sourcing means choosing one supplier although "
          "others could. Dual or multiple sourcing splits volume to reduce supply risk at the cost of lower volume per "
          "supplier."),
    Entry("qualification", "Supplier qualification", ("qualification", "qualified supplier", "supplier approval",
          "vendor qualification"),
          "Checking that a supplier meets your requirements before buying from it, for example quality certification, "
          "sanctions screening, financial stability, insurance and code of conduct. In this application a supplier "
          "counts as qualified only when an approver signed it off and no required check has failed or expired."),
]

_METHOD = [
    Entry("method_scoring", "How this application scores quotes", ("score", "scoring", "weights", "how is the score",
          "ranking", "rank", "explain scoring"),
          "Engine version {v}. Each scored supplier gets five criteria between 0 and 1: price (lowest basket total / "
          "the supplier's basket total, on the lines every scored supplier priced, minimum order quantities included), "
          "lead time (fastest lead time / the supplier's, using its slowest priced line; 0 when not stated), coverage "
          "(share of requirement lines comparably priced, weighted mandatory {mand}, preferred {pref}, optional {opt}), "
          "qualification (1 if currently qualified, else 0) and risk (1 minus risk points / 100). The score is 100 x "
          "the weighted sum. Default weights: price {wp}%, lead time {wl}%, coverage {wc}%, qualification {wq}%, risk "
          "{wr}%. The calculation is deterministic; no AI model computes any figure.", kind="method"),
    Entry("method_risk", "How this application scores risk", ("risk", "risk score", "deviation", "deviations",
          "risk points"),
          "Each quote is checked against the request. Every deviation has a severity, and risk points are critical "
          "{c}, major {m} and minor {n} each, capped at 100. Risk level is high from 50 points, medium from 20, "
          "otherwise low. Examples: an unqualified supplier or expired quote is critical; Incoterms differing from "
          "the requested ones, late delivery or an arithmetic mismatch is major; missing payment terms or a stale "
          "exchange rate is minor.", kind="method"),
    Entry("method_conversion", "How this application converts currencies and units", ("convert", "conversion",
          "exchange rate", "fx", "currency", "units", "unit conversion"),
          "Currencies are converted only with exchange rates your organization records, using the latest rate valid "
          "on the analysis date, directly or inverted, never through a third currency. Units are converted with exact "
          "standard factors within count, mass, length, area and volume; across them (for example box to pieces) only "
          "with a rule your organization records. Anything that cannot be converted exactly is reported, not "
          "estimated.", kind="method"),
    Entry("method_award", "How an award is decided here", ("award", "approve", "approval", "recommendation",
          "who decides"),
          "The analysis only ranks. A buyer recommends a supplier from a current comparison with a rationale; a "
          "different person with approval rights approves or rejects it. Approval checks again that the comparison "
          "is still current and the supplier qualified, and needs a written reason if the supplier was not the top "
          "score. Only an approved recommendation awards the RFQ.", kind="method"),
]


def _render_method(entry: Entry) -> Entry:
    pct = {k: round(v * 100) for k, v in DEFAULT_WEIGHTS.items()}
    text = entry.text.format(
        v=ENGINE_VERSION, wp=pct["price"], wl=pct["lead_time"], wc=pct["coverage"], wq=pct["qualification"],
        wr=pct["risk"], mand=PRIORITY_WEIGHT["mandatory"], pref=PRIORITY_WEIGHT["preferred"],
        opt=PRIORITY_WEIGHT["optional"], c=SEVERITY_POINTS["critical"], m=SEVERITY_POINTS["major"],
        n=SEVERITY_POINTS["minor"],
    )
    return Entry(entry.key, entry.title, entry.terms, text, entry.kind)


ENTRIES: list[Entry] = (
    [Entry(f"incoterm_{code.lower()}", f"{code} – {name} (Incoterms 2020)", (code.lower(), name.lower()),
           f"{code} ({name}): {text}") for code, (name, text) in _INCOTERMS.items()]
    + _TERMS
    + [_render_method(e) for e in _METHOD]
)


def _tokens(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9%/ -]+", " ", text.lower()) + " "


def lookup(question: str, limit: int = 3) -> list[Entry]:
    """Entries whose terms appear in the question as whole words, longest match first. Deterministic."""
    q = _tokens(question)
    scored: list[tuple[int, str, Entry]] = []
    for entry in ENTRIES:
        best = 0
        for term in entry.terms:
            if f" {term} " in q:
                best = max(best, len(term) + (5 if len(term) == 3 and term.isalpha() else 0))
        if best:
            scored.append((best, entry.key, entry))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [e for _, _, e in scored[:limit]]


def by_key(key: str) -> Entry | None:
    return next((e for e in ENTRIES if e.key == key), None)
