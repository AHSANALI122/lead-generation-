# Lead-Gen Chatbot

A conversational lead-generation assistant. It chats with website visitors, qualifies
them with **BANT** (Budget, Authority, Need, Timeline), stores leads in Neon Postgres,
emails the team when a lead qualifies, and shows everything in a token-protected admin
dashboard.

The agent is grounded in a product/FAQ corpus (RAG) so it can answer real questions, and
it's hardened with signed sessions, rate limiting, bot protection, a daily spend cap, and
LLM guardrails.

---

## How it works

```
Visitor → ChatWidget ──POST /session──► FastAPI         (mint signed token; Turnstile-gated)
                     ──POST /chat/stream (SSE)──► FastAPI
                                                  ├─ Agents SDK (Gemini via LiteLLM)
                                                  │    tool: save_lead       → Neon (Lead, upsert)
                                                  │    tool: search_knowledge → FAQ corpus (RAG)
                                                  │    memory: SQLAlchemySession → Neon
                                                  └─ notify.py → SMTP email (on first qualify)
Admin → /admin (server component) ──GET /leads (Bearer)──► FastAPI → Neon
```

1. The widget loads and calls `POST /session`; the backend mints an HMAC-signed token
   (forge-proof). A Cloudflare Turnstile check gates this step to block bots.
2. Each message goes to `POST /chat/stream` with the token plus attribution (page URL,
   referrer, UTM params). The reply streams back token-by-token over SSE.
3. The Gemini agent asks one question at a time, leads with *need*, and collects
   name/email/phone + BANT. It calls `save_lead` to **upsert one row per session**
   (recomputing a 0–100 score) and `search_knowledge` to ground product answers.
4. When a lead first becomes `qualified`, `notify.py` emails the team — exactly once.
5. You review leads at `/admin`, sorted best-first, with filters and expandable detail.
   The admin token never reaches the browser (server-side fetch only).

**A lead = one session token per browser.** The lead score is `BANT signals × 15 (max 60)
+ has email-or-phone 25 + qualified 15`, capped at 100.

---

## Stack

- **Backend:** [uv](https://docs.astral.sh/uv/) · FastAPI · SQLModel · OpenAI Agents SDK +
  LiteLLM (Gemini free tier) · Neon Postgres · Alembic
- **Frontend:** Next.js (App Router) + TypeScript + Tailwind v4 + Framer Motion
- **Model:** `gemini/gemini-2.5-flash` via LiteLLM; embeddings `gemini-embedding-001`

---

## Project structure

```
lead-generation/
├─ backend/
│  ├─ main.py            # app, routes, CORS, rate limit, auth, errors
│  ├─ db.py              # sync + async engines
│  ├─ models.py          # Lead, DailyUsage
│  ├─ schemas.py         # ChatRequest / ChatResponse / SessionRequest
│  ├─ notify.py          # SMTP email on qualify
│  ├─ botcheck.py        # Cloudflare Turnstile verification
│  ├─ spend.py           # daily LLM-call cap (DB-backed counter)
│  ├─ knowledge/faqs.md  # product/FAQ corpus (RAG)
│  └─ agent/
│     ├─ core.py         # build_agent + BANT instructions
│     ├─ tools.py        # save_lead, search_knowledge, scoring
│     ├─ retrieval.py    # in-memory Gemini-embedding search
│     └─ guardrails.py   # on-topic / jailbreak input guardrail
├─ alembic/              # migrations (schema managed here, not create_all)
└─ frontend/
   ├─ app/
   │  ├─ page.tsx            # host page rendering <ChatWidget/>
   │  └─ admin/page.tsx      # SERVER component: authed /leads fetch
   └─ components/
      ├─ ChatWidget.tsx      # "use client"
      └─ LeadsDashboard.tsx  # "use client"; leads passed as props
```

---

## Setup

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 18+ and npm
- A [Neon](https://neon.tech) Postgres database
- A [Gemini API key](https://aistudio.google.com/apikey)

### 1. Backend

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# then fill in .env (see "Environment variables" below)

# Apply the database schema
uv run alembic upgrade head

# Run the API (http://localhost:8000)
uv run uvicorn backend.main:app --reload
```

### 2. Frontend

```bash
cd frontend
npm install

# Configure environment
cp .env.local.example .env.local
# set NEXT_PUBLIC_API_BASE_URL and the server-only ADMIN_TOKEN

# Run the app (http://localhost:3000)
npm run dev
```

Open <http://localhost:3000> for the chat widget and <http://localhost:3000/admin> for the
dashboard.

---

## Environment variables

Copy `.env.example` → `.env` (backend) and `frontend/.env.local.example` →
`frontend/.env.local` (frontend). Never commit either; both are gitignored.

### Backend (`.env`)

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Gemini auth (chat + embeddings) |
| `AGENT_MODEL` | Chat model (default `gemini/gemini-2.5-flash`) |
| `DATABASE_URL` | Neon sync URL (`+psycopg`) for Lead CRUD |
| `ASYNC_DATABASE_URL` | Neon async URL (`+asyncpg`) for agent memory |
| `ADMIN_TOKEN` | Bearer token for `/leads` (fails closed if unset) |
| `SESSION_SECRET` | HMAC key for signing sessions (set this in prod) |
| `ALLOWED_ORIGINS` | CORS allowlist (default `http://localhost:3000`) |
| `CHAT_RATE_LIMIT` | Per-IP chat limit (default `20/minute`) |
| `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile secret (blank = bot check off) |
| `DAILY_LLM_CALL_CAP` | Global daily call ceiling (blank = no cap) |
| `EMBED_MODEL` | Embedding model (default `gemini/gemini-embedding-001`) |
| `KNOWLEDGE_TOP_K` / `KNOWLEDGE_MIN_SCORE` | RAG retrieval tuning |
| `SMTP_*`, `NOTIFY_EMAIL_TO/FROM` | Email alerts (blank = disabled; Gmail example in `.env.example`) |

### Frontend (`frontend/.env.local`)

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | FastAPI base URL (exposed to the browser) |
| `ADMIN_TOKEN` | **Server-only** — used by the `/admin` server component; no `NEXT_PUBLIC_` prefix |
| `NEXT_PUBLIC_TURNSTILE_SITE_KEY` | Cloudflare Turnstile public site key |

> Email is **off until configured**: if any of `SMTP_HOST/USER/PASSWORD` or
> `NOTIFY_EMAIL_TO` is blank, `notify.py` silently no-ops and chat is unaffected. For Gmail,
> `SMTP_PASSWORD` must be a 16-char **App Password** (not your normal password). See the
> documented block in `.env.example`.

---

## API

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`  | `/health` | — | `{"status":"ok"}` |
| `POST` | `/session` | Turnstile | mint a signed session token (30/min) |
| `POST` | `/chat` | signed session | non-streaming reply |
| `POST` | `/chat/stream` | signed session | SSE `delta` events then `done` |
| `GET`  | `/leads` | `Bearer ADMIN_TOKEN` | all leads, best-first (30/min) |

---

## Security

- `session_id` is server-minted and HMAC-signed; every chat endpoint verifies it.
- Admin token compared with `hmac.compare_digest`; `/leads` fails closed (503) if unset.
- All user values are escaped before HTML/email/headers; the agent never sees secrets.
- `save_lead` reads `session_id` from server context, never from the user message.
- Rate limiting, CORS allowlist, Turnstile bot check, and a daily spend cap layer on top.
- LLM guardrails deflect off-topic/jailbreak attempts and never reveal the system prompt.

---

## Development

```bash
# Backend syntax check
python -m py_compile backend/*.py backend/agent/*.py

# Create a migration after editing models.py (review the generated file)
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head

# Frontend lint + build
cd frontend && npm run lint && npm run build
```

The build is organized feature-by-feature (F1–F18). See **`spec.md`** for the full design,
feature plan, and status, and **`CLAUDE.md`** for how each work session is structured.

---

## Status

Features F1–F17 are built and verified (chat, qualification, scoring, email, dashboard,
guardrails, bot/spend protection, migrations, RAG). **F18 — Deployment** is the remaining
work: backend on Railway/Render/Fly and frontend on Vercel, smoke-tested over HTTPS.
