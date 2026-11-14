"""Removes contact details and off-platform payment routes from text one party sends to the other.

Applied to every negotiation message, to the custom terms of every offer and to the text a seller writes on a
public listing. What is found is replaced with a marker and reported as a flag, so the sender can be warned.
Deliberately conservative about numbers that are product data: identifiers labelled UPC/EAN/ASIN/SKU/model and
prices are left alone.
"""
import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# "john at gmail dot com", "john [at] gmail [dot] com"
EMAIL_SPELLED = re.compile(
    r"\b[\w.+-]{2,}\s*[\(\[\{]?\s*(?:at|@)\s*[\)\]\}]?\s*[\w-]{2,}\s*[\(\[\{]?\s*(?:dot|\.)\s*[\)\]\}]?\s*"
    r"(?:com|net|org|io|co|us|biz|info|me)\b",
    re.IGNORECASE,
)
URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
BARE_DOMAIN = re.compile(
    r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|co|me|us|biz|info|app|link|ly|gg|shop|store|to)\b(?:/\S*)?",
    re.IGNORECASE,
)
PHONE = re.compile(r"(?<![\w$])\+?\(?\d[\d\s().-]{6,}\d(?![\w])")
CASHTAG = re.compile(r"(?<![\w$])\$[A-Za-z][A-Za-z0-9_]{2,}")
HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_][A-Za-z0-9_.]{2,}")
PAYMENT_WORDS = re.compile(
    r"\b(?:paypal|venmo|zelle|cash\s?app|western union|moneygram|wire (?:me|it|the money)|bitcoin|btc|usdt|"
    r"crypto(?:currency)? wallet|gift cards?)\b",
    re.IGNORECASE,
)
PAYMENT_DOMAINS = ("paypal", "venmo", "cash.app", "zelle", "wise.com", "revolut", "buy.stripe", "square.link", "checkout")
IDENTIFIER_LABEL = re.compile(r"(?:upc|ean|gtin|asin|sku|model|mpn|item|lot|po|invoice|order)\s*(?:no\.?|number|#)?\s*[:#]?\s*$",
                              re.IGNORECASE)

MARKERS = {
    "email": "[email removed]",
    "phone": "[phone removed]",
    "link": "[link removed]",
    "payment_link": "[payment link removed]",
    "payment_handle": "[payment handle removed]",
    "handle": "[handle removed]",
    "company_name": "[name removed]",
}
WARNINGS = {
    "email": "Email addresses are removed. Keep the conversation here until an offer is accepted.",
    "phone": "Phone numbers are removed. Contact details are shared automatically once an offer is accepted.",
    "link": "Links to other sites are removed.",
    "payment_link": "Payment links are removed: pay only through the platform.",
    "payment_handle": "Payment handles are removed: pay only through the platform.",
    "handle": "Social media handles are removed.",
    "company_name": "Company names are hidden until an offer is accepted.",
    "payment_off_platform": "Paying outside the platform is not protected. Agree the deal here instead.",
}


@dataclass
class Scrubbed:
    text: str
    flags: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(f != "payment_off_platform" for f in self.flags)

    @property
    def warnings(self) -> list[str]:
        return [WARNINGS[f] for f in self.flags]


def _digits(value: str) -> int:
    return sum(ch.isdigit() for ch in value)


def scrub(text: str, *, names: list[str] | tuple[str, ...] = ()) -> Scrubbed:
    """Return the text with contact details replaced, and which kinds were found (each once, in order)."""
    found: list[str] = []

    def note(kind: str) -> None:
        if kind not in found:
            found.append(kind)

    def replace_url(match: re.Match) -> str:
        value = match.group(0)
        kind = "payment_link" if any(d in value.lower() for d in PAYMENT_DOMAINS) else "link"
        note(kind)
        return MARKERS[kind]

    def replace(kind: str):
        def inner(_: re.Match) -> str:
            note(kind)
            return MARKERS[kind]
        return inner

    def replace_phone(match: re.Match) -> str:
        value = match.group(0)
        digits = _digits(value)
        before = text_now[max(0, match.start() - 24):match.start()]
        if digits < 10 or digits > 15 or IDENTIFIER_LABEL.search(before):
            return value  # too short for a phone number, or a labelled product identifier
        if re.fullmatch(r"\d{12,14}", value.strip()):
            return value  # a bare 12-14 digit number is far more likely a UPC/EAN than a phone number
        note("phone")
        return MARKERS["phone"]

    out = EMAIL.sub(replace("email"), text)
    out = EMAIL_SPELLED.sub(replace("email"), out)
    out = URL.sub(replace_url, out)
    out = BARE_DOMAIN.sub(replace_url, out)
    out = CASHTAG.sub(replace("payment_handle"), out)
    out = HANDLE.sub(replace("handle"), out)
    text_now = out
    out = PHONE.sub(replace_phone, out)
    for name in names:
        name = (name or "").strip()
        if len(name) >= 4:
            pattern = re.compile(re.escape(name), re.IGNORECASE)
            if pattern.search(out):
                out = pattern.sub(MARKERS["company_name"], out)
                note("company_name")
    if PAYMENT_WORDS.search(out):
        note("payment_off_platform")
    return Scrubbed(out, found)
