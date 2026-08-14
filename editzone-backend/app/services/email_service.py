import asyncio
import smtplib
from email.message import EmailMessage

from app.config import settings


def _send_message(recipient: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.EMAIL_FROM
    message["To"] = recipient
    message.set_content(body)

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(message)


async def send_otp_email(recipient: str, otp: str, purpose: str) -> None:
    """Deliver an OTP without blocking FastAPI's event loop."""
    if settings.ENV.lower() == "development" and not all(
        (settings.SMTP_HOST, settings.SMTP_USER, settings.SMTP_PASSWORD)
    ):
        print(f"[DEV EMAIL] {purpose} OTP for {recipient}: {otp}")
        return
    if not all((settings.SMTP_HOST, settings.SMTP_USER, settings.SMTP_PASSWORD, settings.EMAIL_FROM)):
        raise RuntimeError("Email delivery is not configured")

    label = "password reset" if purpose == "reset_password" else "email verification"
    await asyncio.to_thread(
        _send_message,
        recipient,
        f"Your EditZone {label} code",
        (
            f"Your EditZone {label} code is {otp}.\n\n"
            "This code expires in 5 minutes. If you did not request it, ignore this email."
        ),
    )


async def send_account_deletion_email(recipient: str) -> None:
    if settings.ENV.lower() == "development" and not all(
        (settings.SMTP_HOST, settings.SMTP_USER, settings.SMTP_PASSWORD)
    ):
        print(f"[DEV EMAIL] Account deletion confirmation sent to {recipient}")
        return
    if not all((settings.SMTP_HOST, settings.SMTP_USER, settings.SMTP_PASSWORD, settings.EMAIL_FROM)):
        return
    await asyncio.to_thread(
        _send_message,
        recipient,
        "Your EditZone account has been deleted",
        "Your EditZone account has been permanently deactivated and personal profile data has been removed. Financial and audit records are retained where legally required.",
    )
