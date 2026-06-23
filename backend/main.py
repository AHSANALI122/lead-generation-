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
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlmodel import Session, select

from backend.agent.core import build_agent
from backend.agent.tools import ChatContext
from backend.db import create_db_and_tables, get_async_engine, get_engine
from backend.models import Lead
from backend.schemas import ChatRequest, ChatResponse

load_dotenv()

logger = logging.getLogger(__name__)

# Shown to the user when the agent or provider fails — never a stack trace (F5/F6).
FALLBACK_REPLY = "Sorry — I hit a snag just now. Could you try again in a moment?"

# Per-IP rate limiting (F8). Chat endpoints share CHAT_RATE_LIMIT; /session is a bit
# looser. NB: this keys on the client IP, so behind a proxy it needs a trusted
# X-Forwarded-For; and rate limiting is *not* auth — non-browser clients also bypass
# CORS entirely (stronger bot protection is F15).
CHAT_RATE_LIMIT = os.getenv("CHAT_RATE_LIMIT", "20/minute")
limiter = Limiter(key_func=get_remote_address)


def _rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Friendly 429 — a calm message, never slowapi's raw default."""
    return JSONResponse(
        status_code=429,
        content={"detail": "You're sending messages too quickly — please wait a moment and try again."},
    )


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

# Wire the limiter onto the app and route 429s to the friendly handler (F8).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limited)


def _allowed_origins() -> list[str]:
    """Parse ALLOWED_ORIGINS (comma-separated).

    Defaults to the Next.js dev origin. ALLOWED_ORIGINS overrides this in every real
    deployment, so the default only matters for local dev.
    """
    raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]


# CORS restricts which *browser* origins may call us; it is **not** authentication.
# A non-browser client (curl, a script) ignores it entirely, so real protection comes
# from signed sessions (F5), rate limiting (F8 below), and bot defenses (F15).
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
@limiter.limit("30/minute")
async def create_session(request: Request) -> dict[str, str]:
    """Mint a fresh signed session token for a new visitor."""
    sid = secrets.token_urlsafe(16)
    return {"session_id": sign_session(sid)}


@app.post("/chat", response_model=ChatResponse)
@limiter.limit(CHAT_RATE_LIMIT)
async def chat(request: Request, req: ChatRequest) -> ChatResponse:
    """Non-streaming chat: verify the session, run the agent, persist history.

    Any failure below the auth check (provider 429, timeout, DB hiccup) is logged and
    turned into a friendly reply rather than a 500 — the visitor never sees a trace.
    """
    sid = verify_session(req.session_id)  # 401 before the try: auth stays 401.
    try:
        ctx = ChatContext(session_id=sid, source=req.source())
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
@limiter.limit(CHAT_RATE_LIMIT)
async def chat_stream(request: Request, req: ChatRequest) -> StreamingResponse:
    """Streaming chat (SSE): emit `delta` events token-by-token, then `done`.

    Same session verification as /chat. Iterating the stream to completion still runs
    tools (save_lead) and persists history, exactly like the non-streaming path. Any
    error streams a fallback delta followed by `done` so the client always terminates.
    """
    sid = verify_session(req.session_id)  # 401 before streaming begins.

    async def event_stream():
        try:
            ctx = ChatContext(session_id=sid, source=req.source())
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


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """Guard the admin API (F10). Fails closed and compares in constant time.

    ADMIN_TOKEN is read per request so an unset token always 503s (never silently
    open). The Bearer credential is matched with `hmac.compare_digest` to avoid
    timing side-channels; a mismatch (or missing header) is a 401.
    """
    token = os.getenv("ADMIN_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="Admin API is not configured.")
    if not hmac.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")


@app.get("/leads", response_model=list[Lead])
@limiter.limit("30/minute")
async def list_leads(
    request: Request, _: None = Depends(require_admin)
) -> list[Lead]:
    """Admin-only: every lead, best first (score desc, then newest).

    Returns full Lead rows (incl. BANT, notes, source) for the admin dashboard (F13).
    """
    with Session(get_engine()) as session:
        return session.exec(
            select(Lead).order_by(Lead.score.desc(), Lead.created_at.desc())
        ).all()
