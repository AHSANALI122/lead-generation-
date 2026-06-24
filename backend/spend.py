"""Global daily spend cap on LLM-backed chat requests (F15).

A scripted client that slips past the bot check could still drain the Gemini quota, so
we keep a per-UTC-day counter in Postgres and refuse new chat requests once it crosses
`DAILY_LLM_CALL_CAP`. The bump is an atomic `INSERT ... ON CONFLICT (day)`, so the
ceiling holds even with several app instances sharing one Neon database. (slowapi's
per-IP limiter, by contrast, is in-memory per instance — shared/Redis-backed limiting
is the F18 scale follow-up.)

The cap is checked once per chat request. A request can fan out to one or two model
calls — the agent plus the F14 guardrail classifier — so counting requests is a simple,
good-enough proxy for "LLM calls" as a spend ceiling.

An unset/blank `DAILY_LLM_CALL_CAP` means *no cap*, so the common case adds zero extra
DB work. A value of N caps the day at N requests; 0 blocks everything.
"""

import logging
import os
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session

from backend.db import get_engine
from backend.models import DailyUsage

logger = logging.getLogger(__name__)


def _daily_cap() -> int | None:
    """The configured daily request cap, or None when disabled (unset/blank)."""
    raw = os.getenv("DAILY_LLM_CALL_CAP", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("DAILY_LLM_CALL_CAP=%r is not an integer; ignoring cap", raw)
        return None


def check_and_reserve_call() -> bool:
    """Reserve one LLM call against today's budget. Returns True if it's allowed.

    No cap configured → always True (and no DB write). Otherwise atomically increment
    today's counter and allow the call only if the new total stays within the cap.

    Fails *open* (returns True) on any DB error: a counter hiccup must never take chat
    down. This is the opposite of the bot gate, which fails closed — here the cost of a
    false block (turning a real visitor away) outweighs letting one extra call through.
    """
    cap = _daily_cap()
    if cap is None:
        return True

    today = datetime.now(UTC).date()
    # Atomic upsert: insert today's row at 1, or bump the existing count by 1, and read
    # back the new total in the same statement so the comparison can't race.
    stmt = (
        insert(DailyUsage)
        .values(day=today, count=1)
        .on_conflict_do_update(
            index_elements=["day"],
            set_={"count": DailyUsage.count + 1},
        )
        .returning(DailyUsage.count)
    )
    try:
        with Session(get_engine()) as session:
            count = session.execute(stmt).scalar_one()
            session.commit()
    except Exception:
        logger.exception("daily cap check failed; allowing the call")
        return True

    return count <= cap
