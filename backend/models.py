"""Database models.

One `Lead` row per chat session (upserted by `session_id` in F4). Fields are mostly
optional because the agent fills them in progressively as the BANT conversation unfolds.
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Lead(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    # Server-minted session id (bare sid). Unique so we can upsert one lead per session.
    session_id: str = Field(unique=True, index=True)

    # Contact details — collected by the agent during the conversation.
    name: str | None = None
    email: str | None = None
    phone: str | None = None

    # BANT qualification signals.
    budget: str | None = None
    authority: str | None = None
    need: str | None = None
    timeline: str | None = None

    # qualified is the agent's judgment (sticky once true — handled in F4).
    qualified: bool = Field(default=False)
    # score is the computed 0–100 value (formula lives in F4).
    score: int = Field(default=0)
    notes: str | None = None

    # Attribution — written once on lead creation from the client request (F7).
    page_url: str | None = None
    referrer: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
