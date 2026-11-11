import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger("app.email")

# Captured messages when EMAIL_BACKEND=memory (used by the test-suite).
outbox: list[dict[str, str]] = []


def _send_smtp(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(msg)


def _send_smtp_message(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(msg)


async def send_message(msg: EmailMessage) -> None:
    """Deliver a fully prepared message (attachments included). Raises on failure so a job can retry."""
    if settings.email_backend == "memory":
        body = msg.get_body(preferencelist=("plain",))
        outbox.append({
            "to": str(msg["To"]), "subject": str(msg["Subject"]),
            "body": body.get_content() if body else "",
            "attachments": ",".join(part.get_filename() or "" for part in msg.iter_attachments()),
            "message_id": str(msg["Message-ID"]),
        })
    elif settings.email_backend == "console":
        logger.info("EMAIL to=%s subject=%s attachments=%d", msg["To"], msg["Subject"], len(list(msg.iter_attachments())))
    else:
        await asyncio.to_thread(_send_smtp_message, msg)


async def send_email(to: str, subject: str, body: str) -> None:
    """Never raises: a mail outage must not break the request that triggered the mail."""
    try:
        if settings.email_backend == "memory":
            outbox.append({"to": to, "subject": subject, "body": body})
        elif settings.email_backend == "console":
            logger.info("EMAIL to=%s subject=%s\n%s", to, subject, body)
        else:
            await asyncio.to_thread(_send_smtp, to, subject, body)
    except Exception:
        logger.exception("failed to send email to %s", to)


def verification_email(name: str, token: str) -> tuple[str, str]:
    url = f"{settings.app_base_url}/verify-email?token={token}"
    return (
        "Verify your email address",
        f"Hi {name},\n\nConfirm your email address to activate your account:\n{url}\n\n"
        f"The link expires in {settings.verify_token_hours} hours.\n",
    )


def reset_email(name: str, token: str) -> tuple[str, str]:
    url = f"{settings.app_base_url}/reset-password?token={token}"
    return (
        "Reset your password",
        f"Hi {name},\n\nChoose a new password:\n{url}\n\n"
        f"The link expires in {settings.reset_token_minutes} minutes. "
        "If you did not request this, you can ignore this email.\n",
    )


def invitation_email(org_name: str, role: str, token: str) -> tuple[str, str]:
    url = f"{settings.app_base_url}/accept-invite?token={token}"
    return (
        f"You have been invited to {org_name}",
        f"You were invited to join {org_name} as {role}.\n\nAccept the invitation:\n{url}\n\n"
        f"The invitation expires in {settings.invite_token_days} days.\n",
    )


def payment_failed_email(org_name: str) -> tuple[str, str]:
    url = f"{settings.app_base_url}/billing"
    return (
        "Your payment failed",
        f"We could not take the latest subscription payment for {org_name}.\n\n"
        "AI analysis is paused until a payment succeeds. Update your payment method here:\n"
        f"{url}\n",
    )
