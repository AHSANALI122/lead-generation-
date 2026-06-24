"""Bot protection: Cloudflare Turnstile verification (F15).

`/session` is the one place a new visitor enters the system, so we gate session
minting on a Turnstile token. The signed token it returns then proves humanity for
every later chat message — no per-message captcha needed.

Like SMTP (notify.py), this is an **optional** feature: with `TURNSTILE_SECRET_KEY`
unset we treat the check as disabled and let minting through, so local dev and the
offline tests keep working without a Cloudflare account.

Failure policy is the opposite of the F14 guardrail. The guardrail fails *open* (an
infra hiccup must never block a real visitor mid-conversation), but here a token we
can't verify is rejected (fail *closed*): the gate only blocks the cheap, retryable
session mint, so refusing the unverifiable is the safer default against bots.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Cloudflare's server-side verification endpoint.
_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_enabled() -> bool:
    """True when a Turnstile secret is configured (bot protection is on)."""
    return bool(os.getenv("TURNSTILE_SECRET_KEY"))


async def verify_turnstile(token: str | None, remoteip: str | None = None) -> bool:
    """Verify a Turnstile token with Cloudflare. Returns whether to allow the mint.

    No-op (returns True) when `TURNSTILE_SECRET_KEY` is unset — bot protection is then
    disabled, which is fine for local dev. When configured, a missing token or a failed
    verification returns False; any network/parse error also returns False (fail closed).
    """
    secret = os.getenv("TURNSTILE_SECRET_KEY")
    if not secret:
        # Disabled: nothing to verify against, so don't block the visitor.
        return True
    if not token:
        return False

    data = {"secret": secret, "response": token}
    if remoteip:
        data["remoteip"] = remoteip

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(_SITEVERIFY_URL, data=data)
            resp.raise_for_status()
            return resp.json().get("success") is True
    except Exception:
        # Fail closed: an unverifiable token is treated as invalid (see module docstring).
        logger.warning("Turnstile verification call failed; rejecting token")
        return False
