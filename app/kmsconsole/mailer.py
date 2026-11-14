"""Outgoing mail through a configurable SMTP server (STARTTLS, implicit TLS or plain)."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape

from . import config, store


class MailError(Exception):
    pass


def _tls_context(verify):
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _html(title, body_lines):
    paras = "".join(f'<p style="margin:0 0 12px">{escape(line)}</p>' for line in body_lines)
    return f"""<!doctype html><html><body style="margin:0;background:#F3F6F7;font-family:Inter,Segoe UI,Arial,sans-serif;color:#0F2027">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;background:#FFFFFF;border:1px solid #DDE5E7;border-radius:16px">
<tr><td style="background:#0B1F24;border-radius:16px 16px 0 0;padding:20px 28px;color:#FFFFFF;font-weight:800;font-size:18px">
<span style="color:#0E9F8E">&#9632;</span> KMS Console</td></tr>
<tr><td style="padding:28px"><h1 style="margin:0 0 16px;font-size:20px">{escape(title)}</h1>{paras}</td></tr>
<tr><td style="padding:16px 28px;border-top:1px solid #EEF2F3;color:#52646B;font-size:12px">KMS Console {escape(config.version())}</td></tr>
</table></td></tr></table></body></html>"""


def send(subject, lines, to=None, settings=None):
    s = settings or store.section("mail")
    if not s.get("host"):
        raise MailError("No SMTP server configured")
    recipients = [r.strip() for r in (to or s.get("recipients") or "").replace(";", ",").split(",") if r.strip()]
    if not recipients:
        raise MailError("No recipients configured")
    sender = s.get("from_address") or s.get("username")
    if not sender:
        raise MailError("No sender address configured")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((s.get("from_name") or "KMS Console", sender))
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
    if s.get("reply_to"):
        msg["Reply-To"] = s["reply_to"]
    msg.set_content("\n\n".join(lines) + f"\n\n-- \nKMS Console {config.version()}\n")
    msg.add_alternative(_html(subject, lines), subtype="html")

    security = s.get("security", "starttls")
    port = int(s.get("port") or (465 if security == "ssl" else 587))
    ctx = _tls_context(bool(s.get("verify_tls", True)))
    try:
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(s["host"], port, timeout=20, context=ctx)
        else:
            smtp = smtplib.SMTP(s["host"], port, timeout=20)
        with smtp:
            smtp.ehlo()
            if security == "starttls":
                smtp.starttls(context=ctx)
                smtp.ehlo()
            if s.get("username"):
                smtp.login(s["username"], s.get("password") or "")
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailError(str(exc)) from exc


def notify(event, subject, lines):
    """Send a notification if mail is enabled and the event type is switched on. Never raises."""
    try:
        if not store.get("mail.enabled") or not store.get(f"mail.notify_{event}"):
            return False
        send(subject, lines)
        return True
    except Exception as exc:  # noqa: BLE001 - notifications must never break the caller
        store.log_event("warning", f"Mail notification failed: {exc}")
        return False
