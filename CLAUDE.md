# CLAUDE.md — Lead-Gen Chatbot

Project memory for Claude Code. Read this top-to-bottom at the start of **every**
session before doing anything.

## How we work in each session (important)

1. **Read `spec.md` first.** It is the source of truth: shared context + a
   feature-wise build plan (F1…F18) with a status table.
2. **Pick exactly one feature** to build this session:
   - the feature the user names, OR
   - if they don't name one, the **first feature whose Status is ☐** and whose
     dependencies are all done.
3. **Build only that feature.** Don't scope-creep into later features. If you find
   something that belongs to another feature, note it in `spec.md`, don't build it.
4. **Verify** against that feature's Acceptance checklist (see "Definition of done").
5. **Update `spec.md`:** set the feature's Status to ☑ in both the build-order
   table and its section, and tick its acceptance boxes. Briefly note any decision
   or deviation.
6. Keep changes small and reviewable; prefer one feature = one commit.

If a feature is already ☑ but the user wants changes, treat it as a focused edit to
that feature only.

## What this is

A conversational lead-gen assistant that qualifies website visitors via **BANT**,
stores leads in Neon Postgres, emails an alert on each qualified lead, and shows a
small admin dashboard. Full design is in `spec.md`.

## Stack & structure

- **Backend:** uv · FastAPI · SQLModel · OpenAI Agents SDK + LiteLLM (Gemini free tier) · Neon Postgres.
- **Frontend:** Next.js (App Router) + TypeScript + Tailwind + Framer Motion.
- Layout: `backend/` (`main.py`, `db.py`, `models.py`, `schemas.py`, `notify.py`,
  `agent/core.py`, `agent/tools.py`) and `frontend/` (Next.js: `app/` with
  `page.tsx` + `admin/page.tsx`, and `components/ChatWidget.tsx` +
  `components/LeadsDashboard.tsx`). See `spec.md` → "Project structure".

## Commands

```bash
# Backend deps (first time)
uv add fastapi "uvicorn[standard]" sqlmodel "openai-agents[sqlalchemy,litellm]" \
       asyncpg "psycopg[binary]" python-dotenv slowapi httpx

# Run backend
uv run uvicorn backend.main:app --reload

# Database migrations (F16 — schema is managed by Alembic, not create_all)
uv run alembic upgrade head                       # apply migrations (run before serving)
uv run alembic revision --autogenerate -m "..."   # after a models.py change; review the file
uv run alembic stamp head                          # mark an already-populated DB as up to date

# Syntax check before finishing
python -m py_compile backend/*.py backend/agent/*.py

# Frontend (first time)
npx create-next-app@latest frontend   # TypeScript + Tailwind + App Router
cd frontend && npm install framer-motion

# Run frontend
cd frontend && npm run dev
```

Copy `.env.example` to `.env` and fill it before running. Never commit `.env`.

## Conventions

- **Model:** `gemini/gemini-2.5-flash` via `AGENT_MODEL`; call `set_tracing_disabled(True)`.
- **DB:** always go through SQLModel/SQLAlchemy (parameterized). Two Neon URLs for the
  same DB: sync (`+psycopg`) for Lead CRUD, async (`+asyncpg`) for agent memory.
- **Types:** modern Python typing (`str | None`), full type hints on functions.
- **Style:** small modules, clear names, short comments that explain *why*. Prose
  comments, not walls of text. No secrets in code or in the agent's prompt.
- **Frontend:** Next.js App Router. Any component using hooks, browser APIs, or
  Framer Motion must start with `"use client";`. Browser-exposed env vars need the
  `NEXT_PUBLIC_` prefix; secrets (e.g. `ADMIN_TOKEN`) stay **server-only** and are
  used in server components / route handlers — never shipped to the browser.
  Tailwind utility classes; Framer Motion for animation; brand palette — forest
  `#1B4332`, honey `#E0A458`, cream `#FBF8F3`, sage `#EDEFE9`.
- **Errors:** user-facing failures return a friendly message, never a stack trace;
  log the real error server-side.

## Security invariants (never regress these)

- `session_id` is **server-minted and HMAC-signed** (`/session`); every chat
  endpoint verifies it before use.
- Admin token compared with `hmac.compare_digest`; `/leads` is rate-limited and
  fails closed (503) when `ADMIN_TOKEN` is unset.
- **Escape** all user-supplied values before HTML/email/headers (`html.escape`,
  strip newlines from headers).
- `save_lead` reads `session_id` from server context, never from the user message.
- Rate-limit `/chat`, `/chat/stream`, `/session`, `/leads`. CORS is locked to
  `ALLOWED_ORIGINS` (and CORS is not auth — don't rely on it for protection).

## Definition of done (per feature)

- [ ] All of that feature's Acceptance boxes in `spec.md` pass.
- [ ] `python -m py_compile` is clean for changed backend files; frontend builds.
- [ ] No security invariant above is broken.
- [ ] `.env.example` updated if new env vars were introduced.
- [ ] Feature Status set to ☑ in `spec.md`, with a one-line note on any decision.

## Environment variables

See `.env.example`. Backend keys: `GEMINI_API_KEY`, `AGENT_MODEL`, `DATABASE_URL`,
`ASYNC_DATABASE_URL`, `ADMIN_TOKEN`, `SESSION_SECRET`, `ALLOWED_ORIGINS`,
`CHAT_RATE_LIMIT`, and SMTP (`SMTP_HOST/PORT/USER/PASSWORD`, `NOTIFY_EMAIL_TO`,
`NOTIFY_EMAIL_FROM`). Frontend (`frontend/.env.local`): `NEXT_PUBLIC_API_BASE_URL`
(browser) and `ADMIN_TOKEN` (server-only, for the `/admin` server component).
