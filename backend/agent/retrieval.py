"""Product-knowledge retrieval (F17 — RAG).

Gives the agent a small, curated FAQ corpus it can ground answers in. The flow is
deliberately lightweight and in-memory:

1. `faqs.md` is split on `## ` headings into chunks (heading = citation label).
2. Every chunk is embedded **once per process** (Gemini via LiteLLM, free tier) and
   cached; a process that never gets a product question spends nothing, and importing
   this module needs no API key — same laziness as the DB engines in `db.py`.
3. `search()` embeds the query, scores chunks by cosine similarity, and returns the
   top-k above a confidence threshold. Nothing above the threshold → `[]`, which is the
   signal the tool/agent use to fall back to "the team will follow up".

Embedding failures **fail open** (return `[]`, log server-side) rather than raising —
mirroring the guardrail's classifier fallback — so a provider hiccup never surfaces a
stack trace to the visitor.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import litellm

logger = logging.getLogger(__name__)

# Gemini's embedding model, via LiteLLM. Overridable so the model can be swapped without
# code changes; the key is the same GEMINI_API_KEY the chat model already uses.
DEFAULT_EMBED_MODEL = "gemini/text-embedding-004"

# Corpus lives next to this package, under backend/knowledge/.
_CORPUS_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "faqs.md"


@dataclass(frozen=True)
class Chunk:
    """One retrievable FAQ entry. `title` is shown to the visitor as the citation."""

    title: str
    text: str


@dataclass(frozen=True)
class Hit:
    """A retrieved chunk with its similarity score (1.0 = identical direction)."""

    title: str
    text: str
    score: float


def _top_k() -> int:
    """How many chunks to return at most (KNOWLEDGE_TOP_K, default 3)."""
    try:
        return max(1, int(os.getenv("KNOWLEDGE_TOP_K", "3")))
    except ValueError:
        return 3


def _min_score() -> float:
    """Cosine threshold a chunk must clear to count as relevant.

    KNOWLEDGE_MIN_SCORE (default 0.55): raise to be stricter (fewer, surer hits), lower
    to retrieve more. Below this, `search` returns nothing and the agent falls back.
    """
    try:
        return float(os.getenv("KNOWLEDGE_MIN_SCORE", "0.55"))
    except ValueError:
        return 0.55


def load_chunks() -> list[Chunk]:
    """Parse `faqs.md` into chunks, one per `## ` heading.

    Anything before the first H2 (the `# title`, HTML comments) is ignored. Empty
    sections are skipped so a stray heading never produces a blank chunk.
    """
    raw = _CORPUS_PATH.read_text(encoding="utf-8")
    chunks: list[Chunk] = []
    title: str | None = None
    body: list[str] = []

    def flush() -> None:
        if title and (text := "\n".join(body).strip()):
            chunks.append(Chunk(title=title, text=text))

    for line in raw.splitlines():
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            body = []
        elif title is not None:  # only collect once we're inside a section
            body.append(line)
    flush()
    return chunks


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with the configured Gemini embedding model.

    Raises on provider/auth errors; callers decide whether to fail open.
    """
    model = os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL)
    api_key = os.getenv("GEMINI_API_KEY")
    resp = litellm.embedding(model=model, input=texts, api_key=api_key)
    # LiteLLM normalizes to OpenAI shape: resp.data[i]["embedding"].
    return [item["embedding"] for item in resp.data]


@lru_cache(maxsize=1)
def _embedded_corpus() -> list[tuple[Chunk, list[float]]]:
    """Load + embed every chunk once per process (cached).

    On any embedding failure we log and cache an **empty** corpus for this call; the
    lru_cache means we won't hammer the provider on every message. (Restart to retry
    after fixing the key/outage.)
    """
    chunks = load_chunks()
    if not chunks:
        return []
    try:
        vectors = _embed([c.text for c in chunks])
    except Exception:
        logger.exception("knowledge corpus embedding failed; retrieval disabled this run")
        return []
    return list(zip(chunks, vectors))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (pure Python, no numpy)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def search(
    query: str,
    *,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[Hit]:
    """Return the most relevant corpus chunks for `query`, best first.

    Returns `[]` when the corpus is empty/unavailable, the query is blank, or nothing
    clears `min_score` — every one of which means the agent should fall back rather than
    answer. Query-embedding failures fail open the same way.
    """
    query = (query or "").strip()
    if not query:
        return []

    corpus = _embedded_corpus()
    if not corpus:
        return []

    try:
        query_vec = _embed([query])[0]
    except Exception:
        logger.exception("knowledge query embedding failed; falling back")
        return []

    threshold = _min_score() if min_score is None else min_score
    k = _top_k() if top_k is None else top_k

    scored = [
        Hit(title=chunk.title, text=chunk.text, score=_cosine(query_vec, vec))
        for chunk, vec in corpus
    ]
    scored.sort(key=lambda h: h.score, reverse=True)
    return [h for h in scored if h.score >= threshold][:k]
