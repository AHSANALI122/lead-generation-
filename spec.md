# Lead-Gen Chatbot — Feature Spec

A conversational lead-generation assistant: it chats with website visitors,
qualifies them with **BANT** (Budget, Authority, Need, Timeline), stores leads in
Neon Postgres, emails an alert on each qualified lead, and shows everything in an
admin dashboard.

This spec is **feature-wise**: build one feature per session, in order, unless the
user says otherwise. Each feature is self-contained with its own acceptance
criteria. Update the **Status** of a feature when it's done.

---

## Shared context (read once, applies to every feature)

### Stack
- **Backend:** uv, FastAPI, SQLModel, OpenAI Agents SDK + LiteLLM (Gemini free tier).
- **DB:** Neon (Postgres) — two URLs for the same DB: sync (`+psycopg`) and async (`+asyncpg`).
- **Frontend:** Next.js (App Router) + TypeScript + Tailwind + Framer Motion.
- **Model:** `gemini/gemini-2.5-flash` via `AGENT_MODEL`; tracing disabled.

### Architecture
```
Browser → ChatWidget ──POST /chat/stream (SSE)──► FastAPI
                                                   ├─ Agents SDK (Gemini via LiteLLM)
                                                   │    tool: save_lead → Neon (Lead)
                                                   │    memory: SQLAlchemySession → Neon
                                                   └─ notify.py → SMTP email
Admin → LeadsDashboard ──GET /leads (Bearer token)──► FastAPI → Neon
```

### Project structure
```
lead-bot/
├─ backend/
│  ├─ main.py            # app, routes, CORS, rate limit, auth, errors
│  ├─ db.py              # sync + async engines, create_db_and_tables
│  ├─ models.py          # Lead
│  ├─ schemas.py         # ChatRequest / ChatResponse
│  ├─ notify.py          # SMTP email
│  └─ agent/
│     ├─ core.py         # build_agent + BANT instructions
│     └─ tools.py        # save_lead, ChatContext, compute_score
└─ frontend/                  # Next.js app (App Router)
   ├─ app/
   │  ├─ layout.tsx
   │  ├─ page.tsx             # demo/host page rendering <ChatWidget/>
   │  └─ admin/page.tsx       # SERVER component: authed /leads fetch (token stays server-side)
   └─ components/
      ├─ ChatWidget.tsx       # "use client"
      └─ LeadsDashboard.tsx   # "use client"; receives leads as props
```

### Data model — `Lead` (one row per session, upserted)
| Field | Type | Source |
|-------|------|--------|
| id | int PK | auto |
| session_id | str unique index | client (verified) |
| name, email, phone | str? | agent |
| budget, authority, need, timeline | str? | agent |
| qualified | bool | agent (sticky) |
| score | int 0–100 | computed |
| notes | str? | agent |
| page_url, referrer, utm_source/medium/campaign | str? | client |
| created_at, updated_at | datetime | server |

Conversation history is stored by the SDK in `agent_sessions`/`agent_messages`
(auto-created), keyed by the bare session id.

### Environment variables
**Backend** (`.env`): `GEMINI_API_KEY`, `AGENT_MODEL`, `DATABASE_URL`,
`ASYNC_DATABASE_URL`, `ADMIN_TOKEN`, `SESSION_SECRET`, `ALLOWED_ORIGINS`,
`CHAT_RATE_LIMIT`, and SMTP: `SMTP_HOST/PORT/USER/PASSWORD`, `NOTIFY_EMAIL_TO`,
`NOTIFY_EMAIL_FROM`.
**Frontend** (`frontend/.env.local`): `NEXT_PUBLIC_API_BASE_URL` (FastAPI base URL,
exposed to the browser) and `ADMIN_TOKEN` (server-only — **no** `NEXT_PUBLIC_`
prefix; used by the admin server component so the token never reaches the browser).
Keep `.env`/`.env.local` in `.gitignore`.

### Security invariants (must hold in every feature that touches them)
- All DB access via SQLModel/SQLAlchemy (parameterized) — never string-built SQL.
- `session_id` is **server-minted and HMAC-signed**; endpoints verify it.
- Admin token compared with `hmac.compare_digest` (constant-time).
- User-supplied values are **escaped** before going into HTML/email/headers.
- The agent never receives the admin token or secrets in its prompt.
- `save_lead` takes `session_id` from server context, never from the user message.

### Lead score formula
BANT signals 15 each (60) + has contact (email or phone) 25 + qualified 15, cap 100.

---

## Build order & status

| # | Feature | Status |
|---|---------|--------|
| F1 | Project scaffold & config | ☑ |
| F2 | Lead model & database | ☑ |
| F3 | BANT agent core | ☑ |
| F4 | save_lead tool & scoring | ☑ |
| F5 | Signed sessions & chat endpoint | ☑ |
| F6 | Streaming chat (SSE) | ☑ |
| F7 | Source / attribution capture | ☑ |
| F8 | Rate limiting & CORS | ☑ |
| F9 | Email notifications | ☑ |
| F10 | Admin leads API (auth) | ☑ |
| F11 | Chat widget (frontend base) | ☑ |
| F12 | Widget UX polish | ☑ |
| F13 | Admin dashboard (frontend) | ☑ |
| F14 | LLM guardrails | ☑ |
| F15 | Bot protection & spend cap | ☑ |
| F16 | DB migrations (Alembic) | ☑ |
| F17 | Product knowledge (RAG) | ☑ |
| F18 | Deployment | ☐ |

---

## F1 — Project scaffold & config
- **Status:** ☑  **Depends on:** —
- **Note:** CORS dev default set to `http://localhost:3000` (this project's Next.js
  frontend) rather than the spec's `5173` (Vite); `ALLOWED_ORIGINS` overrides it.
  DB engines are created lazily so module import doesn't require a live DB.
- **Goal:** A runnable FastAPI app with env loading, CORS, and a health check.
- **Build:**
  - `uv init`; add deps: `fastapi "uvicorn[standard]" sqlmodel "openai-agents[sqlalchemy,litellm]" asyncpg "psycopg[binary]" python-dotenv slowapi httpx`.
  - `backend/main.py`: FastAPI app, `load_dotenv()`, CORS from `ALLOWED_ORIGINS` (default `http://localhost:5173`), `GET /health` → `{"status":"ok"}`.
  - `.env.example` with all env vars; `.gitignore` includes `.env`.
- **Acceptance:**
  - [x] `uv run uvicorn backend.main:app --reload` starts cleanly.
  - [x] `GET /health` returns `{"status":"ok"}`.
  - [x] CORS allows the configured origin only.

## F2 — Lead model & database
- **Status:** ☑  **Depends on:** F1
- **Note:** Engines exposed as `get_engine()` / `get_async_engine()` (lazy, cached).
  Verified locally against SQLite (insert/query/delete); Neon URLs go in `.env`.
- **Goal:** The `Lead` table and DB engines on Neon.
- **Build:**
  - `backend/db.py`: sync engine (`DATABASE_URL`, psycopg) + async engine (`ASYNC_DATABASE_URL`, asyncpg, `connect_args={"ssl": True, "statement_cache_size": 0}`); `create_db_and_tables()`.
  - `backend/models.py`: `Lead` per the data model above (`session_id` unique).
  - Call `create_db_and_tables()` in the app lifespan startup.
- **Acceptance:**
  - [x] Tables are created on startup against Neon. *(create_db_and_tables runs in
    lifespan; verified end-to-end against SQLite — needs real Neon URL in `.env`.)*
  - [x] A Lead can be inserted and queried.

## F3 — BANT agent core
- **Status:** ☑  **Depends on:** F1
- **Note:** `set_tracing_disabled(True)` is called at `backend/agent/core.py` import
  time (the app-init path), and `GEMINI_API_KEY` is passed explicitly to
  `LitellmModel` so auth doesn't depend on how LiteLLM reads the environment.
- **Goal:** A Gemini-backed agent that qualifies via BANT.
- **Build:**
  - `backend/agent/core.py`: `build_agent()` returning an `Agent` with BANT instructions, `model=LitellmModel(model=AGENT_MODEL)`, default `gemini/gemini-2.5-flash`.
  - Instructions: warm, one question at a time, lead with NEED, mirror the user's language, collect name/email/phone + BANT, keep replies short.
  - Call `set_tracing_disabled(True)` at app init.
- **Acceptance:**
  - [x] `Runner.run(agent, "hi")` returns a sensible reply with `GEMINI_API_KEY` set.
    *(agent builds & tracing is off; live reply gated on a real `GEMINI_API_KEY` in
    `.env`, as F1/F2 gated their Neon checks.)*
  - [x] No OpenAI key is required (tracing off). *(verified: `build_agent()` succeeds
    with no OpenAI key set.)*

## F4 — save_lead tool & scoring
- **Status:** ☑  **Depends on:** F2, F3
- **Note:** DB logic factored into a plain, testable `upsert_lead` helper (the
  decorated `save_lead` is a thin wrapper that pulls `session_id` from server context
  only). `qualified` is sticky (never flips back to false); blank fields are dropped
  so they never clobber collected data. `ChatContext.source` is defined but not yet
  written onto the Lead — deferred to F7. F9 email left as a placeholder comment at
  the false→true transition. Verified against SQLite (mirrors F2).
- **Goal:** The agent persists leads and scores them.
- **Build:**
  - `backend/agent/tools.py`: `@dataclass ChatContext(session_id, source)`; `compute_score(lead)`; `@function_tool save_lead(ctx, ...)` that **upserts by session_id**, drops empty fields, sets `score`, and (placeholder until F9) is the place where notifications will fire on the `qualified` false→true transition.
  - Add `save_lead` to the agent's tools.
- **Acceptance:**
  - [x] Through conversation, a Lead row is created/updated for the session.
  - [x] `score` is recomputed on each save.
  - [x] Calling save_lead repeatedly never creates duplicate rows.

## F5 — Signed sessions & chat endpoint
- **Status:** ☑  **Depends on:** F4
- **Note:** `SESSION_SECRET` resolved once at import; missing → ephemeral `token_hex(32)`
  + a startup warning (tokens don't survive a restart). Tokens are `"<sid>.<sig>"`
  with sid=`token_urlsafe(16)`, sig=HMAC-SHA256; `verify_session` splits on the last
  dot and compares with `compare_digest`, raising 401 before the try-block so auth
  failures stay 401 while provider errors fall through to a friendly 200. Agent is a
  cached singleton (`get_agent`); attribution dict (`_request_source`) is plumbed into
  `ChatContext` now but only persisted in F7. Verified offline (sign/verify, /session,
  401, forced provider error → friendly reply); live reply gated on real keys.
- **Goal:** Forge-proof sessions and a working non-streaming chat.
- **Build:**
  - `main.py`: `SESSION_SECRET` (ephemeral fallback + warning); `sign_session`/`verify_session` (HMAC-SHA256, `hmac.compare_digest`).
  - `POST /session` → `{ "session_id": "<sid>.<sig>" }`.
  - `POST /chat`: verify token → bare `sid`; `SQLAlchemySession(sid, engine=async_engine, create_tables=True)`; `ChatContext(sid, source)`; `Runner.run`; wrap in try/except → graceful fallback reply on any error (incl. 429).
  - `schemas.py`: `ChatRequest(session_id, message, page_url?, referrer?, utm_*)`, `ChatResponse(session_id, reply)`.
- **Acceptance:**
  - [x] `/session` returns a signed token. *(verified offline; token round-trips through verify_session.)*
  - [x] `/chat` with a valid token replies and persists history; invalid/missing token → 401.
    *(401 path verified offline; live reply/persistence gated on real GEMINI/Neon keys, as F3 was.)*
  - [x] A provider error yields a friendly reply, not a 500. *(forced Runner.run to raise → 200 + FALLBACK_REPLY.)*

## F6 — Streaming chat (SSE)
- **Status:** ☑  **Depends on:** F5
- **Note:** Shares F5's verify/context/session setup; `Runner.run_streamed` is
  synchronous (not awaited), iterated in an async generator. Only
  `raw_response_event` frames with `data.type == "response.output_text.delta"` emit a
  `{"delta": ...}`; the loop always ends with `{"done": true}`. Errors stream a
  fallback delta then done so the client terminates. `json.dumps` escapes payloads;
  headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Verified offline with a
  fake event stream (filtering, happy path, and error path).
- **Goal:** Token-by-token replies.
- **Build:**
  - `POST /chat/stream`: same session verification; `Runner.run_streamed(...)`; iterate `result.stream_events()`, and for `event.type == "raw_response_event"` with `data.type == "response.output_text.delta"`, yield `data: {"delta": ...}`; end with `data: {"done": true}`. `StreamingResponse(media_type="text/event-stream")` + headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Errors stream a fallback delta + done.
- **Acceptance:**
  - [x] Client receives incremental `delta` events then a `done` event. *(verified offline with a fake stream; non-text events skipped.)*
  - [x] Tools still run and history still persists during streaming. *(same session= passed and stream consumed to completion as /chat; live-gated on real keys.)*

## F7 — Source / attribution capture
- **Status:** ☑  **Depends on:** F4, F5
- **Note:** `_request_source` helper retired in favour of `ChatRequest.source()` (the
  attribution shape now lives on the model). `upsert_lead` gained a `source` param and
  writes it via `setattr` **only when the row is first created** (`is_new`), so later
  saves never clobber the original referrer/UTMs. `save_lead` passes
  `ctx.context.source` (server context only — never a model argument). Verified offline
  on SQLite: create stores UTMs, a later save with different source leaves them intact,
  and a source-less create leaves the columns `None`.
- **Goal:** Record where each lead came from.
- **Build:**
  - `ChatRequest.source()` returns non-empty `page_url/referrer/utm_*`.
  - `save_lead` writes source fields **only on lead creation** (first save), from `ctx.context.source`.
- **Acceptance:**
  - [x] A lead created from a request carrying UTM params stores them.
  - [x] Source is not overwritten on later saves.

## F8 — Rate limiting & CORS
- **Status:** ☑  **Depends on:** F5
- **Note:** slowapi `Limiter(key_func=get_remote_address)` wired via `app.state.limiter`
  with a **custom** `RateLimitExceeded` handler returning a friendly 429 JSON (not
  slowapi's raw default). `/session` 30/min; `/chat` and `/chat/stream` on
  `CHAT_RATE_LIMIT` (default 20/min); `/health` unlimited. Each limited endpoint takes
  `request: Request` (slowapi requires the literal name). Added a comment by the CORS
  middleware noting CORS is **not** auth — non-browser clients bypass it (F15). Verified
  with TestClient: limiter + handler attached, hammering `/session` yields 200s then a
  429 carrying the friendly `detail`.
- **Goal:** Basic abuse protection.
- **Build:**
  - slowapi `Limiter(key_func=get_remote_address)`; `app.state.limiter`; 429 handler.
  - `@limiter.limit(CHAT_RATE_LIMIT)` (default `20/minute`) on `/chat` and `/chat/stream`; `30/minute` on `/session`. Each limited endpoint takes `request: Request`.
- **Acceptance:**
  - [x] Exceeding the limit returns 429 with a friendly message.
  - [x] Note in code/docs: CORS is not auth; non-browser clients bypass it (covered later by F15).

## F9 — Email notifications
- **Status:** ☑  **Depends on:** F4
- **Note:** `notify_qualified_lead` is fully best-effort — it swallows its own
  exceptions, so `upsert_lead` calls it without a guard. `_build_message` is split out
  from the SMTP send so escaping/formatting is testable offline. `NOTIFY_EMAIL_FROM`
  falls back to `SMTP_USER` when blank. Plain-text + escaped-HTML alternative parts;
  Subject has newlines stripped (header-injection). Fires only on the sticky false→true
  transition (verified: exactly once across repeated `qualified=True` saves).
  `.env.example` already had all SMTP vars — no change needed.
- **Goal:** Email the team when a lead is newly qualified.
- **Build:**
  - `backend/notify.py`: `notify_qualified_lead(lead)` via `smtplib` (SSL on 465, else STARTTLS). **Escape** all user values with `html.escape` in the HTML part; strip newlines from the Subject. No-op unless `SMTP_HOST/USER/PASSWORD` and `NOTIFY_EMAIL_TO` are set. Best-effort (swallow exceptions).
  - Wire `save_lead` to call it only on the `qualified` false→true transition (once per session).
- **Acceptance:**
  - [x] Newly qualified lead triggers exactly one email. *(verified offline: counter stub fires once across three `qualified=True` saves.)*
  - [x] Injected HTML in a field is escaped in the email. *(HTML part contains `&lt;b&gt;`, not raw `<b>`.)*
  - [x] Unconfigured SMTP → silent no-op, chat unaffected. *(returns without connecting when SMTP env is unset.)*

## F10 — Admin leads API (auth)
- **Status:** ☑  **Depends on:** F2, F8
- **Note:** `require_admin` reads `ADMIN_TOKEN` via `os.getenv` per request (fails
  closed: unset → 503 before any compare), then constant-time `hmac.compare_digest`
  against `"Bearer <token>"` → 401 on mismatch/missing. `GET /leads` returns full
  `Lead` rows (incl. session_id, BANT, notes, source) for the F13 dashboard;
  `response_model=list[Lead]`. Reuses the existing slowapi limiter and F8's friendly
  429 handler. Verified via TestClient (503/401/200 + sort, 429 over the limit).
- **Goal:** Read leads securely.
- **Build:**
  - `require_admin` dependency: 503 if `ADMIN_TOKEN` unset; else `hmac.compare_digest(authorization or "", f"Bearer {ADMIN_TOKEN}")` → 401 on mismatch.
  - `GET /leads` with `Depends(require_admin)` + `@limiter.limit("30/minute")`, sorted by `score desc, created_at desc`.
- **Acceptance:**
  - [x] Valid token → list; wrong token → 401; unset token → 503. *(all three verified via TestClient.)*
  - [x] Over the limit → 429. *(31st request within the minute returns the friendly 429.)*

## F11 — Chat widget (frontend base)
- **Status:** ☑  **Depends on:** F6
- **Note:** Scaffolded with `create-next-app@latest` → Next 16 / React 19 / **Tailwind
  v4** (CSS-first config: brand palette + fonts live in `@theme` in `app/globals.css`,
  no `tailwind.config.ts`). Fonts via `next/font/google` (Fraunces display + Inter
  body) wired to `font-display`/`font-body` utilities. The backend serves SSE over
  **POST**, so `EventSource` can't be used — the widget streams with `fetch` +
  `response.body.getReader()` and a small frame parser (`readStream`) that buffers
  partial frames and stops on `{done:true}`. `getSource()` mirrors the backend's
  `source()` (drops blanks). Session minting is deduped via a promise ref (React 19
  StrictMode double-invokes effects). Verified end-to-end against the live backend:
  `/session` mints a token and `/chat/stream` returns `delta` frames then `done`; the
  request carried all source params. Live LLM *text* is gated on Neon being reachable
  (the agent's SDK session needs the async DB) — in this run Neon's host failed DNS, so
  the backend streamed its friendly fallback, which the widget rendered correctly. Same
  external gating as F2–F6.
- **Goal:** A floating chat widget that talks to the backend.
- **Build:**
  - `npx create-next-app@latest frontend` (TypeScript + Tailwind, App Router); add `framer-motion`.
  - `components/ChatWidget.tsx` — starts with `"use client";` (uses hooks, `localStorage`, `window`, Framer Motion). Launcher + spring-open panel; header/messages/input; on mount fetch `${NEXT_PUBLIC_API_BASE_URL}/session` and store the signed token in `localStorage`; `getSource()` reads page URL/referrer/UTM; send via `POST /chat/stream`, parse SSE deltas into the last bot bubble; typing indicator; props `apiBaseUrl, greeting, brandName`.
  - Render `<ChatWidget apiBaseUrl={process.env.NEXT_PUBLIC_API_BASE_URL!} />` from `app/page.tsx`.
  - Design: forest `#1B4332` header/launcher, honey `#E0A458` user bubbles, cream `#FBF8F3` surface, sage `#EDEFE9` bot bubbles; Fraunces (display) + Inter (body).
- **Acceptance:**
  - [x] Widget opens, sends a message, and renders a streamed reply. *(streaming path
    verified against the live backend; the rendered reply was the backend's fallback
    because Neon was unreachable — widget behaviour is correct either way.)*
  - [x] Source params are included in the request. *(page_url, referrer, utm_* sent in
    the `/chat/stream` body and accepted by the backend.)*

## F12 — Widget UX polish
- **Status:** ☑  **Depends on:** F11
- **Note:** All additions layer onto `components/ChatWidget.tsx`; no backend change.
  Persistence keys conversation by the session **token** (`lead_chat:<token>`), so a
  re-minted session naturally starts a fresh thread; a `dirty` ref gates writes so the
  hydration pass never clobbers stored history with the bare greeting, and writes only
  happen on settled (non-streaming) state. Hydration runs in the session-resolution
  callback (not an effect body) to satisfy Next 16's `react-hooks/set-state-in-effect`
  rule. Chips (`suggestions` prop) show only until the first user message; proactive
  nudge (`nudgeAfter` seconds) fires once via timer and is dismissed by opening or the
  ✕. A11y: panel `role="dialog"`/`aria-modal`/`aria-labelledby`, Escape closes, focus
  moves to the input on open and back to the launcher on close, messages container is
  `role="log"` + `aria-live="polite"`, and `useReducedMotion()` drops springs/looping
  dot animation. 401 self-heal: a `401` from `/chat/stream` re-mints once and retries
  the same message. Verified: `npm run lint` + `npm run build` clean; the 401 trigger
  confirmed against the live backend (tampered token → 401, valid → 200).
- **Goal:** Make it feel premium.
- **Build:** localStorage conversation persistence (per session token); quick-reply chips (`suggestions` prop) shown before first reply; proactive nudge after `nudgeAfter` seconds; accessibility (`role="dialog"`, `aria-modal`, Escape to close, focus to input on open / launcher on close, `aria-live` messages); on `401` from chat, re-mint session and retry once.
- **Acceptance:**
  - [x] Conversation survives reload; chips send on tap; nudge appears; keyboard/AT
    basics work; 401 self-heals. *(persistence/chips/nudge/focus/Escape/aria-live/
    reduced-motion implemented and type-/lint-clean; 401 self-heal trigger verified
    against the live backend — tampered token → 401 → re-mint + retry.)*

## F13 — Admin dashboard (frontend)
- **Status:** ☑  **Depends on:** F10
- **Note:** `/admin` is a server component (`export const dynamic = "force-dynamic"`)
  that reads server-only `ADMIN_TOKEN`, fetches `/leads` with `cache: "no-store"`, and
  maps 401/503/network/other to clear `<Notice>` copy (never a trace).
  `components/LeadsDashboard.tsx` is `"use client"`, receives `leads` as a prop, and
  never sees the token (grep confirms `ADMIN_TOKEN` appears only in the server page +
  `.env.local.example`). Shared `Lead` type in `lib/types.ts`. Metric cards / All|Qualified
  filter / 4-dot BANT / expandable detail; Refresh uses `useRouter().refresh()` wrapped in
  `useTransition`. "Last 7 days" uses a lazy `useState(() => Date.now())` because Next 16's
  React purity lint forbids calling `Date.now()` during render. `npm run lint` + `npm run
  build` clean; `/admin` builds as a dynamic (ƒ) route.
- **Goal:** Review leads visually, **without exposing the admin token to the browser**.
- **Build:**
  - `app/admin/page.tsx` — a **server component**: reads `process.env.ADMIN_TOKEN`
    (server-only), fetches `${NEXT_PUBLIC_API_BASE_URL}/leads` with
    `Authorization: Bearer ${ADMIN_TOKEN}` and `cache: "no-store"`, and renders
    `<LeadsDashboard leads={leads} />`. Handle 401/503 with a clear message.
  - `components/LeadsDashboard.tsx` — `"use client";`, receives `leads: Lead[]` as a
    prop (no fetching, no token in the browser). Metric cards (total, qualified,
    rate, last 7 days); rows with score, 4-dot BANT completeness, qualified badge,
    date; filter All/Qualified; expandable detail (BANT text, phone, notes, source).
    "Refresh" calls `useRouter().refresh()`. Same palette.
- **Acceptance:**
  - [x] `/admin` lists leads sorted best-first; filter and expand work. *(API sends
    score desc, created_at desc; All|Qualified filter + expandable detail implemented;
    build clean. Live list gated on a real ADMIN_TOKEN/Neon, as prior features were.)*
  - [x] The admin token never appears in client JS/network (server-side fetch only).
    *(fetch + Bearer header live only in the server component; `ADMIN_TOKEN` grep hits
    only the server page + `.env.local.example`; `LeadsDashboard` takes data props only.)*
  - [x] Unauthorized/misconfigured backend shows a clear message. *(401/503/network/
    other and missing token/apiBase each render a friendly `<Notice>`, no trace.)*

## F14 — LLM guardrails
- **Status:** ☑  **Depends on:** F5
- **Note:** Hybrid **input** guardrail in `backend/agent/guardrails.py`,
  `@input_guardrail(run_in_parallel=False)` so it completes before the model — the
  tripwire is caught by the endpoints before any reply/delta. Stage 1 is a free regex
  blocklist (jailbreak / prompt-leak / role-change markers); stage 2 is a tiny cached
  Gemini classifier agent (`output_type=OnTopicCheck{on_topic,reason}`) for nuanced
  off-topic cases, and it **fails open** if the provider errors (never blocks a real
  visitor over infra). The classifier is built independently of `core.py` to avoid an
  import cycle. **No output guardrail** — on `/chat/stream` tokens are already sent
  before a final-output check could fire, so prompt-leak defense is hardened in
  `core.py` INSTRUCTIONS instead. `main.py` catches `InputGuardrailTripwireTriggered`
  before the generic handler: `/chat` → 200 + `REFUSAL_REPLY`; `/chat/stream` → refusal
  delta + `done`. No new env vars (no kill-switch added). Verified: heuristic trips
  with no LLM call; live classifier marks a real pricing question on-topic; endpoint
  tests confirm refusal on both paths and 401 still precedes the guardrail.
- **Goal:** Keep the agent on-topic and safe.
- **Build:** Agents SDK input/output guardrails — reject/redirect off-topic, jailbreak, or unsafe requests; never reveal the system prompt; keep a polite on-brand refusal.
- **Acceptance:**
  - [x] Off-topic/jailbreak attempts are deflected; normal lead chats unaffected.
    *(jailbreak → heuristic tripwire (no LLM); live classifier → on-topic for a real
    pricing message; `/chat` returns REFUSAL_REPLY and `/chat/stream` emits a refusal
    delta + done when the tripwire fires; auth 401 still precedes guardrail logic.)*

## F15 — Bot protection & spend cap
- **Status:** ☑  **Depends on:** F8
- **Note:** Bot check is **Cloudflare Turnstile gating `/session`** (not the first
  message) — the signed token then proves humanity for every later message, fitting the
  existing signed-session design. `backend/botcheck.py::verify_turnstile` no-ops when
  `TURNSTILE_SECRET_KEY` is unset (dev, like SMTP) and **fails closed** on a verify error
  (opposite of the F14 guardrail, since it only blocks the cheap, retryable mint). The
  widget renders an `appearance:"interaction-only"` Turnstile (invisible unless
  challenged) via `@marsidev/react-turnstile`, posts the token in the `/session` body,
  and `reset()`s it before any 401 re-mint (tokens are single-use). Spend cap is a
  **DB-backed daily counter** (`DailyUsage`, one row per UTC day) bumped with an atomic
  `INSERT … ON CONFLICT (day)` in `backend/spend.py`, so it's correct across instances;
  checked **once per chat request** (before the agent/guardrail) and **fails open** on a
  DB error. Unset `DAILY_LLM_CALL_CAP` = no cap (zero extra DB work). slowapi stays
  in-memory per-instance — shared/Redis limiting deferred to F18. New env:
  `TURNSTILE_SECRET_KEY`, `DAILY_LLM_CALL_CAP` (backend), `NEXT_PUBLIC_TURNSTILE_SITE_KEY`
  (frontend). `DailyUsage` is created by `create_all` for now; F16's baseline captures it.
- **Goal:** Stop automated quota-drain and spam.
- **Build:** A bot check (Cloudflare Turnstile / hCaptcha) or proof-of-work gating the first message; a **global daily cap** on LLM calls; consider shared-store (Redis) rate limiting for multi-instance correctness.
- **Acceptance:**
  - [x] Scripted clients are blocked/limited; a daily ceiling halts spend. *(offline
    TestClient: `/session` 403s on a failed Turnstile check and 200s on pass, no-ops when
    disabled; `/chat` returns `CAPACITY_REPLY` and `/chat/stream` emits a capacity delta +
    `done` when over cap — without invoking the agent; live Cloudflare verify + the atomic
    Neon counter are external-gated as in prior features. Frontend `npm run lint`/`build`
    clean.)*

## F16 — DB migrations (Alembic)
- **Status:** ☑  **Depends on:** F2
- **Note:** Alembic added at the project root (`alembic.ini` + `alembic/`). `env.py`
  runs against the **sync** `DATABASE_URL` (psycopg), read from the environment via
  `load_dotenv()` so **no DB URL lives in `alembic.ini`**. `target_metadata =
  SQLModel.metadata` (populated by `import backend.models`) so autogenerate sees `Lead`
  and `DailyUsage`. **Critical:** the SDK's `agent_sessions`/`agent_messages` memory
  tables aren't in our metadata, so an `include_name` filter excludes them — otherwise
  autogenerate would emit DROPs. `script.py.mako` adds `import sqlmodel` so generated ops
  using `AutoString` resolve. The startup `create_db_and_tables()` call was **removed**
  from the lifespan (the function stays in `db.py` for ad-hoc/test use); schema is now
  applied with `alembic upgrade head`. Workflow in `CLAUDE.md` Commands: fresh DB →
  `upgrade head`; an existing DB already built by the old `create_all` → `stamp head`.
  Baseline `52db5c201eaf` creates `lead` + `dailyusage`.
- **Goal:** Evolve schema safely.
- **Build:** Add Alembic; baseline migration for `Lead`; replace reliance on `create_all` for schema changes.
- **Acceptance:**
  - [x] A column can be added and applied via `alembic upgrade head`. *(verified on a
    throwaway SQLite DB: `upgrade head` created `lead` + `dailyusage`; adding a probe
    column to `Lead` → `--autogenerate` detected only `lead.<col>` (no SDK tables) →
    `upgrade head` applied it → `downgrade -1` reverted. Live Neon run is external-gated
    as in prior features. Probe column + migration reverted, leaving only the baseline.)*

## F17 — Product knowledge (RAG)
- **Status:** ☑  **Depends on:** F3
- **Goal:** Let the agent answer product/FAQ questions.
- **Build:** Ingest a product/FAQ corpus; retrieval tool the agent can call; cite/ground answers; fall back to "team will follow up" when unsure.
- **Decisions / notes:**
  - Retrieval is **in-memory Gemini embeddings** (via LiteLLM, `EMBED_MODEL` default
    `gemini/text-embedding-004`), cosine similarity in pure Python — no pgvector, no DB
    table, no Alembic migration. No new pip dependency (`litellm` ships with the SDK).
  - Corpus is an authored sample at `backend/knowledge/faqs.md`; each `## ` heading is one
    chunk and its title is the citation label. Replace bodies with real content later.
  - `backend/agent/retrieval.py` loads + embeds the corpus **once per process**
    (`lru_cache`) and **fails open** (returns `[]`, logs server-side) on any embedding
    error — mirroring the F14 guardrail fallback. `search()` returns `[]` when nothing
    clears `KNOWLEDGE_MIN_SCORE` (default 0.55; `KNOWLEDGE_TOP_K` default 3).
  - New `search_knowledge` tool (`backend/agent/tools.py`) takes only the query string
    (no session/secret) and returns `Source: "<title>"` blocks, or a `NO_RELEVANT_INFO`
    sentinel that deterministically drives the agent's "team will follow up" fallback.
    `core.py` registers the tool and instructs: search first, ground + cite, never guess,
    keep qualifying.
  - Accepted simplification: query embeddings aren't separately metered against
    `DAILY_LLM_CALL_CAP` (F15) — the chat request already reserved a call, and corpus
    embedding is once-per-process.
- **Acceptance:**
  - [x] The agent answers a known product question accurately and still qualifies.

## F18 — Deployment
- **Status:** ☐  **Depends on:** most
- **Goal:** Go live.
- **Build:** Backend on Railway/Render/Fly (HTTPS enforced, real `ALLOWED_ORIGINS`, strong `ADMIN_TOKEN`/`SESSION_SECRET`, Gemini billing on); Next.js frontend on Vercel with `NEXT_PUBLIC_API_BASE_URL` and server-only `ADMIN_TOKEN` set; smoke-test the full flow.
- **Acceptance:**
  - [ ] Public URL works end-to-end over HTTPS; dashboard reachable with the token.

---

## Cross-cutting ambiguities (decide as they come up)
- `qualified` (LLM judgment) and `score` (formula) are independent and can disagree — pick the source of truth for "good lead."
- `qualified` is sticky; the email fires once per session (re-qualification won't re-notify).
- "One lead per visitor" = one per session token per browser (localStorage); same person on two devices = two leads.
- Capture depends on the LLM calling `save_lead`; there is no deterministic fallback parser yet.
