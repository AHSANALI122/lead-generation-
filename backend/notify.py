"""Email notifications for newly qualified leads (F9).

When a lead crosses the `qualified` false→true line, `upsert_lead` (F4) calls
`notify_qualified_lead` here so the team gets a heads-up while the visitor is still
warm. Everything is **best-effort**: if SMTP isn't configured, or the send fails, we
log and move on — a mail problem must never break the chat.

Security: every user-supplied value is `html.escape`d before it enters the HTML body,
and any value interpolated into the Subject has newlines stripped (header injection).
"""

import html
import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from backend.models import Lead

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    to: str
    from_: str


def _smtp_config() -> _SmtpConfig | None:
    """Read SMTP settings from the environment, or None if not fully configured.

    Requires HOST, USER, PASSWORD and a recipient (NOTIFY_EMAIL_TO); anything missing
    means "email disabled" and we no-op. The From address defaults to the SMTP user,
    which is what most providers (e.g. Gmail) expect anyway.
    """
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    to = os.getenv("NOTIFY_EMAIL_TO")
    if not (host and user and password and to):
        return None

    return _SmtpConfig(
        host=host,
        port=int(os.getenv("SMTP_PORT", "465")),
        user=user,
        password=password,
        to=to,
        from_=os.getenv("NOTIFY_EMAIL_FROM") or user,
    )


# Lead fields shown in the alert, in display order: (label, attribute).
_REPORT_FIELDS: tuple[tuple[str, str], ...] = (
    ("Name", "name"),
    ("Email", "email"),
    ("Phone", "phone"),
    ("Budget", "budget"),
    ("Authority", "authority"),
    ("Need", "need"),
    ("Timeline", "timeline"),
    ("Qualified", "qualified"),
    ("Score", "score"),
    ("Notes", "notes"),
    ("Page URL", "page_url"),
    ("Referrer", "referrer"),
    ("UTM source", "utm_source"),
    ("UTM medium", "utm_medium"),
    ("UTM campaign", "utm_campaign"),
    ("Created", "created_at"),
)


def _strip_header(value: str) -> str:
    """Collapse newlines so a value can't inject extra email headers."""
    return value.replace("\r", " ").replace("\n", " ").strip()


def _build_message(lead: Lead, *, from_: str, to: str) -> EmailMessage:
    """Render the alert email (plain-text + escaped HTML alternative).

    Kept separate from the SMTP send so escaping/formatting is testable on its own.
    """
    # Subject identifies the lead without leaking much; strip newlines to be safe.
    who = lead.name or lead.email or lead.session_id
    subject = _strip_header(f"New qualified lead: {who} (score {lead.score})")

    rows = [(label, getattr(lead, attr)) for label, attr in _REPORT_FIELDS]

    text_lines = [f"{label}: {value}" for label, value in rows if value is not None]
    text_body = "A lead just qualified.\n\n" + "\n".join(text_lines)

    html_rows = "".join(
        f"<tr><th align='left' style='padding:4px 12px 4px 0'>{html.escape(label)}</th>"
        f"<td style='padding:4px 0'>{html.escape(str(value))}</td></tr>"
        for label, value in rows
        if value is not None
    )
    html_body = (
        "<div style='font-family:system-ui,sans-serif'>"
        "<h2>A lead just qualified</h2>"
        f"<table cellspacing='0' cellpadding='0'>{html_rows}</table>"
        "</div>"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


def notify_qualified_lead(lead: Lead) -> None:
    """Email the team about a newly qualified lead. Best-effort, never raises.

    No-op when SMTP isn't configured. Uses implicit TLS on port 465 (SMTP_SSL) and
    STARTTLS otherwise. Any failure is logged and swallowed so chat is unaffected.
    """
    config = _smtp_config()
    if config is None:
        return

    try:
        msg = _build_message(lead, from_=config.from_, to=config.to)
        if config.port == 465:
            with smtplib.SMTP_SSL(config.host, config.port) as server:
                server.login(config.user, config.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(config.host, config.port) as server:
                server.starttls()
                server.login(config.user, config.password)
                server.send_message(msg)
    except Exception:
        # Best-effort: a mail failure must not surface to the visitor.
        logger.exception("Failed to send qualified-lead notification")
