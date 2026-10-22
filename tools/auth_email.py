"""Transactional email for account setup and recovery links."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from html import escape


class AuthEmailConfigurationError(RuntimeError):
    pass


def send_account_link(*, recipient: str, subject: str, heading: str, link: str, action: str) -> None:
    host = os.getenv("SMTP_HOST")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    if not (host and username and password):
        raise AuthEmailConfigurationError("SMTP_HOST, SMTP_USERNAME, and SMTP_PASSWORD must be configured to send account emails.")

    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.getenv("SMTP_FROM_EMAIL", username)
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"}
    safe_heading = escape(heading)
    safe_action = escape(action)
    safe_link = escape(link, quote=True)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(f"{heading}\n\n{action}: {link}\n\nIf you did not request this, you can ignore this email.")
    message.add_alternative(
        f"<main style='font-family:Arial,sans-serif;max-width:560px;margin:24px auto;color:#21353a'>"
        f"<h1>{safe_heading}</h1><p>Use the button below to continue securely.</p>"
        f"<p><a href='{safe_link}' style='display:inline-block;background:#145d53;color:white;padding:12px 18px;border-radius:8px;text-decoration:none'>{safe_action}</a></p>"
        "<p>If you did not request this, you can ignore this email.</p></main>",
        subtype="html",
    )

    with smtplib.SMTP(host, port, timeout=15) as server:
        if use_tls:
            server.starttls()
        server.login(username, password)
        server.send_message(message)
