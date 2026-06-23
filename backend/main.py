"""FastAPI application entry point.

Holds the app, CORS, session signing, and the chat endpoints. Sessions are
server-minted and HMAC-signed (F5) so they can't be forged; both the plain (`/chat`)
and streaming (`/chat/stream`, F6) endpoints verify the token, then run the BANT
agent with per-session conversation memory persisted in Neon by the Agents SDK.

Rate limiting (F8), source persistence (F7), and email (F9) layer on later.
"""

import hmac
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from functools import lru_cache
from hashlib import sha256

from agents import Agent, Runner
from agents.extensions.memory.sqlalchemy_session import SQLAlchemySession
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.agent.core import build_agent
from backend.agent.tools import ChatContext
from backend.db import create_db_and_tables, get_async_engine
from backend.schemas import ChatRequest, ChatResponse

load_dotenv()

logger = logging.getLogger(__name__)

# Shown to the user when the agent or provider fails — never a stack trace (F5/F6).
FALLBACK_REPLY = "Sorry — I hit a snag just now. Could you try again in a moment?"


def _session_secret() -> bytes:
    """The HMAC key for signing session ids.

    Prefer SESSION_SECRET from the environment. If it's unset we fall back to an
    ephemeral random key and warn: chat still works, but tokens minted before a
    restart stop verifying after it. Fine for local dev, never for production.
    """
    secret = os.getenv("SESSION_SECRET")
    if not secret:
        logger.warning(
            "SESSION_SECRET is not set — using an ephemeral key; sessions will "
            "not survive a restart. Set SESSION_SECRET in .env for stable sessions."
        )
        secret = secrets.token_hex(32)
    return secret.encode()


# Resolve the signing key once at import so every request shares the same key.
_SECRET = _session_secret()


def _sign(sid: str) -> str:
    """HMAC-SHA256 signature of a bare session id, hex-encoded."""
    return hmac.new(_SECRET, sid.encode(), sha256).hexdigest()


def sign_session(sid: str) -> str:
    """Wrap a bare session id into a signed token `"<sid>.<sig>"`."""
    return f"{sid}.{_sign(sid)}"


def verify_session(token: str) -> str:
    """Return the bare session id from a signed token, or raise 401.

    Splits on the last dot, recomputes the signature, and compares in constant time
    (`hmac.compare_digest`) so a bad or forged token never gets past this point.
    """
    sid, _, sig = (token or "").rpartition(".")
    if not sid or not sig or not hmac.compare_digest(sig, _sign(sid)):
        raise HTTPException(status_code=401, detail="Invalid or missing session.")
    return sid


@lru_cache(maxsize=1)
def get_agent() -> Agent:
    """The shared BANT agent, built once.

    The agent itself is stateless — per-session memory lives in the SDK session — so
    a single instance serves every request.
    """
    return build_agent()


def _request_source(req: ChatRequest) -> dict[str, str | None]:
    """Collect the non-empty attribution fields from a request.

    Passed into ChatContext now; only written onto the Lead in F7 (save_lead still
    ignores it), so this is harmless plumbing that F7 will start using.
    """
    fields = ("page_url", "referrer", "utm_source", "utm_medium", "utm_campaign")
    return {f: getattr(req, f) for f in fields if getattr(req, f)}


def _chat_session(sid: str) -> SQLAlchemySession:
    """SDK conversation memory for a session, keyed by the bare sid.

    `create_tables=True` lets the SDK create `agent_sessions`/`agent_messages` on
    first use; the async (asyncpg) engine is required for this store.
    """
    return SQLAlchemySession(sid, engine=get_async_engine(), create_tables=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (F2). No-op if they already exist.
    create_db_and_tables()
    yield


app = FastAPI(title="Lead-Gen Chatbot API", lifespan=lifespan)


def _allowed_origins() -> list[str]:
    """Parse ALLOWED_ORIGINS (comma-separated).

    Defaults to the Next.js dev origin. ALLOWED_ORIGINS overrides this in every real
    deployment, so the default only matters for local dev.
    """
    raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/session")
async def create_session() -> dict[str, str]:
    """Mint a fresh signed session token for a new visitor."""
    sid = secrets.token_urlsafe(16)
    return {"session_id": sign_session(sid)}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Non-streaming chat: verify the session, run the agent, persist history.

    Any failure below the auth check (provider 429, timeout, DB hiccup) is logged and
    turned into a friendly reply rather than a 500 — the visitor never sees a trace.
    """
    sid = verify_session(req.session_id)  # 401 before the try: auth stays 401.
    try:
        ctx = ChatContext(session_id=sid, source=_request_source(req))
        result = await Runner.run(
            get_agent(), req.message, context=ctx, session=_chat_session(sid)
        )
        return ChatResponse(session_id=req.session_id, reply=result.final_output)
    except Exception:
        logger.exception("chat failed for session %s", sid)
        return ChatResponse(session_id=req.session_id, reply=FALLBACK_REPLY)


def _sse(payload: dict) -> str:
    """Format one Server-Sent Event frame (json.dumps escapes quotes/newlines)."""
    return f"data: {json.dumps(payload)}\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streaming chat (SSE): emit `delta` events token-by-token, then `done`.

    Same session verification as /chat. Iterating the stream to completion still runs
    tools (save_lead) and persists history, exactly like the non-streaming path. Any
    error streams a fallback delta followed by `done` so the client always terminates.
    """
    sid = verify_session(req.session_id)  # 401 before streaming begins.

    async def event_stream():
        try:
            ctx = ChatContext(session_id=sid, source=_request_source(req))
            result = Runner.run_streamed(
                get_agent(), req.message, context=ctx, session=_chat_session(sid)
            )
            async for event in result.stream_events():
                if event.type == "raw_response_event" and (
                    event.data.type == "response.output_text.delta"
                ):
                    yield _sse({"delta": event.data.delta})
        except Exception:
            logger.exception("chat stream failed for session %s", sid)
            yield _sse({"delta": FALLBACK_REPLY})
        yield _sse({"done": True})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
