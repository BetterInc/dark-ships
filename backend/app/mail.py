"""Minimal outbound mail via SMTP. In development this points at the Mailpit
service (MAIL_HOST=mailpit, MAIL_PORT=1025), which captures every message in a
web UI at http://localhost:8025 instead of delivering it."""

import logging
from email.message import EmailMessage

import aiosmtplib

from .config import get_settings

logger = logging.getLogger(__name__)


async def _send(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        await aiosmtplib.send(
            message,
            hostname=settings.mail_host,
            port=settings.mail_port,
        )
        logger.info("Sent mail to %s (%s)", to, subject)
    except Exception:
        logger.exception("Failed to send mail to %s", to)


async def send_reset_password_email(to: str, token: str) -> None:
    link = f"{get_settings().frontend_base_url}/reset-password?token={token}"
    body = (
        "You requested a password reset for your Dark Ships account.\n\n"
        f"Open this link to choose a new password:\n{link}\n\n"
        "If you did not request this, you can ignore this email."
    )
    await _send(to, "Reset your Dark Ships password", body)


async def send_verification_email(to: str, token: str) -> None:
    link = f"{get_settings().frontend_base_url}/verify?token={token}"
    body = (
        "Welcome to Dark Ships. Please confirm your email address.\n\n"
        f"Verify your account here:\n{link}\n"
    )
    await _send(to, "Verify your Dark Ships account", body)
