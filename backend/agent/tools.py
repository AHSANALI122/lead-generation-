"""Agent tools — persisting and scoring leads.

The agent (F3) can talk BANT; this module lets it *remember*. `save_lead` is the one
tool exposed to the model: as the conversation reveals contact details and BANT
signals, the agent calls it and we upsert a single `Lead` row per session, keeping a
computed 0–100 `score` current.

The DB work lives in a plain `upsert_lead` helper (not inside the decorated tool) so
the logic stays unit-testable without driving the LLM. Lead CRUD uses the *sync*
psycopg engine, per project convention.
"""

from dataclasses import dataclass, field

from agents import RunContextWrapper, function_tool
from sqlmodel import Session, select

from backend.db import get_engine
from backend.models import Lead, _utcnow


@dataclass
class ChatContext:
    """Per-run server context, never exposed to the LLM.

    `session_id` is the bare (verified) session id used to upsert the lead. `source`
    holds attribution (page_url / referrer / utm_*) collected by the endpoint; it's
    written onto the Lead on first save (F7), so it defaults to empty.
    """

    session_id: str
    source: dict[str, str | None] = field(default_factory=dict)


def _has_text(value: str | None) -> bool:
    """True only for a non-blank string (whitespace-only counts as empty)."""
    return bool(value and value.strip())


def compute_score(lead: Lead) -> int:
    """Lead score 0–100 (spec formula).

    15 each for the four BANT signals (max 60) + 25 for any contact (email or phone)
    + 15 if qualified, capped at 100.
    """
    score = 0
    for signal in (lead.budget, lead.authority, lead.need, lead.timeline):
        if _has_text(signal):
            score += 15
    if _has_text(lead.email) or _has_text(lead.phone):
        score += 25
    if lead.qualified:
        score += 15
    return min(score, 100)


def upsert_lead(
    session_id: str,
    *,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    budget: str | None = None,
    authority: str | None = None,
    need: str | None = None,
    timeline: str | None = None,
    qualified: bool | None = None,
    notes: str | None = None,
    source: dict[str, str | None] | None = None,
) -> Lead:
    """Create or update the lead for `session_id`, recompute its score, and persist.

    Upserts on the unique `session_id`, so repeated calls never create duplicate rows.
    Only non-blank fields are applied — the agent passing a blank never clobbers data
    already collected. `qualified` is sticky: once True it never flips back to False.
    `source` (attribution) is written **only when the lead is first created**, so a
    visitor's original referrer/UTMs survive every later save.
    """
    # Text fields: name maps straight through; the rest mirror the BANT/contact model.
    text_updates = {
        "name": name,
        "email": email,
        "phone": phone,
        "budget": budget,
        "authority": authority,
        "need": need,
        "timeline": timeline,
        "notes": notes,
    }

    with Session(get_engine()) as session:
        lead = session.exec(
            select(Lead).where(Lead.session_id == session_id)
        ).first()
        is_new = lead is None
        if lead is None:
            lead = Lead(session_id=session_id)

        for attrname, value in text_updates.items():
            if _has_text(value):
                setattr(lead, attrname, value.strip())

        # Attribution is set once, at creation, so later saves can't overwrite it.
        if is_new and source:
            for attrname, value in source.items():
                setattr(lead, attrname, value)

        # Sticky qualification: detect the false→true transition for F9's email.
        became_qualified = False
        if qualified and not lead.qualified:
            lead.qualified = True
            became_qualified = True

        lead.updated_at = _utcnow()
        lead.score = compute_score(lead)

        session.add(lead)
        session.commit()
        session.refresh(lead)

    if became_qualified:
        # F9: notify_qualified_lead(lead) fires here (once per session, best-effort).
        pass

    return lead


@function_tool
def save_lead(
    ctx: RunContextWrapper[ChatContext],
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    budget: str | None = None,
    authority: str | None = None,
    need: str | None = None,
    timeline: str | None = None,
    qualified: bool | None = None,
    notes: str | None = None,
) -> str:
    """Save or update what we've learned about this visitor.

    Call this whenever you collect or revise any of: the visitor's name, email, or
    phone; their BANT signals (budget, authority, need, timeline); or short internal
    notes. Set `qualified` to true once they have a genuine need and have shared a way
    to reach them. Pass only the fields you actually learned — omit the rest. You may
    call this multiple times in a conversation; it updates the same lead.
    """
    # Security: session id and attribution come from server context, never the model.
    lead = upsert_lead(
        ctx.context.session_id,
        name=name,
        email=email,
        phone=phone,
        budget=budget,
        authority=authority,
        need=need,
        timeline=timeline,
        qualified=qualified,
        notes=notes,
        source=ctx.context.source,
    )
    return f"Saved lead (score {lead.score})."
