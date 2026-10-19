"""Envío de correo. Resend por HTTPS si hay RESEND_API_KEY (lo normal en Railway,
que bloquea SMTP en varios planes); si no, SMTP clásico si hay SMTP_HOST.

Funciones síncronas y bloqueantes: quien las llame desde código async debe
usar `asyncio.to_thread`."""

import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from app.config import MAIL_FROM, RESEND_API_KEY, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER

TIMEOUT_SECONDS = 20


class MailError(Exception):
    pass


def provider() -> str | None:
    if not MAIL_FROM:
        return None
    if RESEND_API_KEY:
        return "Resend"
    if SMTP_HOST:
        return "SMTP"
    return None


def send_email(to: list[str], subject: str, html: str, text: str) -> None:
    if not provider():
        raise MailError("Correo sin configurar: faltan MAIL_FROM y RESEND_API_KEY (o SMTP_HOST) en las variables de Railway.")
    if not to:
        raise MailError("No hay destinatarios activos.")
    if RESEND_API_KEY:
        _send_resend(to, subject, html, text)
    else:
        _send_smtp(to, subject, html, text)


def _send_resend(to, subject, html, text) -> None:
    payload = json.dumps({"from": MAIL_FROM, "to": to, "subject": subject, "html": html, "text": text}).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json", "User-Agent": "dc-map/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS):
            pass
    except urllib.error.HTTPError as exc:
        raise MailError(f"Resend respondió {exc.code}: {exc.read().decode(errors='replace')[:300]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MailError(f"No se pudo contactar a Resend: {exc}") from exc


def _send_smtp(to, subject, html, text) -> None:
    msg = EmailMessage()
    msg["From"] = MAIL_FROM
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_SECONDS)
            server.starttls(context=ssl.create_default_context())
        with server:
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Falló el envío SMTP: {exc}") from exc
