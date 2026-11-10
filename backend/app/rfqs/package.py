"""Supplier email packages: a complete, stored RFC 5322 message that is sent byte-for-byte as approved."""
import hashlib
import html
import json
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, formataddr, make_msgid
from typing import Any

from app.rfqs.content import format_number, parameter_text


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean_header(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def body_text(document: dict[str, Any], contact_name: str | None, sender_name: str) -> str:
    lines = [f"Dear {contact_name}," if contact_name else "Hello,", ""]
    lines.append(f"{document['organization']} invites you to quote for: {document['title']}.")
    if document.get("message"):
        lines += ["", document["message"]]
    lines += ["", "Requirements:"]
    for item in document["items"]:
        qty = ""
        if item.get("quantity") is not None:
            qty = f" ({format_number(float(item['quantity']))}{' ' + item['unit'] if item.get('unit') else ''})"
        lines.append(f"  {item['line']}. {item['statement']}{qty}")
        for p in item.get("parameters") or []:
            lines.append(f"     - {parameter_text(p)}")
    lines += [
        "",
        "Please reply with:",
        f"  - prices in {document['currency']}, per line, using the line numbers above",
        "  - quantity, unit and lead time in days for each line",
        "  - your Incoterms, payment terms and quotation validity",
    ]
    if document.get("response_due_date"):
        lines.append(f"  - by {document['response_due_date']}")
    lines += [
        "",
        f"Please keep the reference {document['number']} in the subject of your reply.",
        "The full request is attached as a PDF, with a machine-readable copy (JSON).",
        "",
        "Kind regards,",
        sender_name,
        document["organization"],
    ]
    return "\n".join(lines) + "\n"


def build_package(
    document: dict[str, Any], *, content_hash: str, pdf: bytes, sender: str, reply_to: str | None,
    reply_to_name: str | None, recipients: list[dict[str, Any]], message_domain: str, generated_at: datetime | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Build the message for one supplier. recipients: [{name, email, is_primary}] (primary goes in To, others Cc)."""
    if not recipients:
        raise ValueError("A package needs at least one recipient")
    ordered = sorted(recipients, key=lambda r: (not r.get("is_primary"), r["email"]))
    primary, others = ordered[0], ordered[1:]
    generated_at = generated_at or datetime.now(UTC)
    stem = f"{document['number']}-v{document['version']}"
    machine = json.dumps({"rfq": document, "content_sha256": content_hash}, indent=2, ensure_ascii=False).encode()

    msg = EmailMessage(policy=policy.SMTP)
    msg["Subject"] = _clean_header(f"{document['number']} - Request for quotation: {document['title']}")[:400]
    msg["From"] = sender
    msg["To"] = formataddr((_clean_header(primary.get("name") or ""), primary["email"]))
    if others:
        msg["Cc"] = ", ".join(formataddr((_clean_header(r.get("name") or ""), r["email"])) for r in others)
    if reply_to:
        msg["Reply-To"] = formataddr((_clean_header(reply_to_name or ""), reply_to))
    msg["Date"] = format_datetime(generated_at)
    msg["Message-ID"] = make_msgid(idstring=stem, domain=message_domain)
    msg["X-RFQ-Number"] = document["number"]
    msg["X-RFQ-Version"] = str(document["version"])
    msg["X-RFQ-Content-SHA256"] = content_hash
    text = body_text(document, primary.get("name"), reply_to_name or document["organization"])
    msg.set_content(text)
    msg.add_alternative(
        "<html><body style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#0f1b2d\">"
        f"<pre style=\"font-family:inherit;white-space:pre-wrap\">{html.escape(text)}</pre></body></html>",
        subtype="html",
    )
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"{stem}.pdf")
    msg.add_attachment(machine, maintype="application", subtype="json", filename=f"{stem}.json")
    raw = msg.as_bytes()
    manifest = {
        "rfq": document["number"],
        "version": document["version"],
        "content_sha256": content_hash,
        "message_id": msg["Message-ID"],
        "to": [primary["email"]],
        "cc": [r["email"] for r in others],
        "generated_at": generated_at.isoformat(),
        "attachments": [
            {"filename": f"{stem}.pdf", "content_type": "application/pdf", "sha256": _sha(pdf), "size": len(pdf)},
            {"filename": f"{stem}.json", "content_type": "application/json", "sha256": _sha(machine), "size": len(machine)},
        ],
    }
    return raw, manifest


def parse_package(raw: bytes) -> EmailMessage:
    return BytesParser(policy=policy.SMTP).parsebytes(raw)  # type: ignore[return-value]


def verify_package(raw: bytes, expected_sha256: str) -> bool:
    return _sha(raw) == expected_sha256


def recipients_of(msg: EmailMessage) -> list[str]:
    from email.utils import getaddresses

    return [addr for _, addr in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", [])) if addr]
