"""Request/response schemas for the chat endpoints.

`ChatRequest` is what the widget posts; the attribution fields (page_url / referrer
/ utm_*) ride along on every message and are persisted onto a Lead on first save (F7,
via `source()`). The `message` length is capped here as cheap input hygiene — deeper
guardrails are F14/F15.
"""

from pydantic import BaseModel, Field

# Attribution fields, in the order they're stored on the Lead.
_SOURCE_FIELDS = ("page_url", "referrer", "utm_source", "utm_medium", "utm_campaign")


class ChatRequest(BaseModel):
    # Signed token ("<sid>.<sig>") minted by /session and verified server-side.
    session_id: str
    message: str = Field(min_length=1, max_length=4000)

    # Attribution carried from the browser; written onto the Lead on creation (F7).
    page_url: str | None = None
    referrer: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None

    def source(self) -> dict[str, str | None]:
        """The non-empty attribution fields, keyed by their Lead column name.

        Lives on the model so the attribution shape stays with the data; the chat
        endpoints thread the result into `ChatContext` and `save_lead` writes it onto
        the Lead once, on creation (F7). Empty/missing fields are dropped so they
        never overwrite a column with ``None``.
        """
        return {f: getattr(self, f) for f in _SOURCE_FIELDS if getattr(self, f)}


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class SessionRequest(BaseModel):
    # Cloudflare Turnstile token from the widget (F15). Optional so the no-body dev
    # path still works when bot protection is disabled (TURNSTILE_SECRET_KEY unset).
    turnstile_token: str | None = None
