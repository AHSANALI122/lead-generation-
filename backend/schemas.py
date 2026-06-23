"""Request/response schemas for the chat endpoints.

`ChatRequest` is what the widget posts; the attribution fields (page_url / referrer
/ utm_*) ride along on every message but are only persisted onto a Lead in F7. The
`message` length is capped here as cheap input hygiene — deeper guardrails are F14/F15.
"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    # Signed token ("<sid>.<sig>") minted by /session and verified server-side.
    session_id: str
    message: str = Field(min_length=1, max_length=4000)

    # Attribution carried from the browser; written onto the Lead in F7, ignored here.
    page_url: str | None = None
    referrer: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
