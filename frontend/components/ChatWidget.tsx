"use client";

import { Turnstile, type TurnstileInstance } from "@marsidev/react-turnstile";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

type Role = "user" | "bot";
interface Message {
  role: Role;
  text: string;
}

interface ChatWidgetProps {
  /** Base URL of the FastAPI backend, e.g. http://localhost:8000. */
  apiBaseUrl: string;
  /** First bot bubble shown when the widget opens. */
  greeting?: string;
  /** Name shown in the header. */
  brandName?: string;
  /** Quick-reply chips shown before the first user message (F12). */
  suggestions?: string[];
  /** Seconds of inactivity before a proactive nudge appears (0/undefined = off, F12). */
  nudgeAfter?: number;
  /** Cloudflare Turnstile site key (F15). Omit to disable the bot check (dev). */
  turnstileSiteKey?: string;
}

const DEFAULT_GREETING = "Hi there 👋 I'm here to help. What brings you in today?";
const DEFAULT_BRAND = "Honeycomb";

// localStorage key for the signed session token. One thread per browser (the spec's
// "one lead per visitor").
const SESSION_KEY = "lead_session";
// Conversation history is persisted under a key namespaced by the session token, so a
// re-minted session (the 401 path) naturally starts a fresh thread (F12).
const chatKey = (token: string) => `lead_chat:${token}`;

// Shown if the network request itself fails before any tokens stream (the backend
// already turns provider errors into a friendly streamed reply, HTTP 200).
const FALLBACK = "Sorry — I hit a snag just now. Could you try again in a moment?";

/**
 * Attribution read from the browser. Only non-empty values are returned so we never
 * overwrite a stored Lead column with an empty string — this mirrors the backend's
 * `ChatRequest.source()`, which drops blanks before persisting on first save.
 */
function getSource(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const params = new URLSearchParams(window.location.search);
  const candidates: Record<string, string> = {
    page_url: window.location.href,
    referrer: document.referrer,
    utm_source: params.get("utm_source") ?? "",
    utm_medium: params.get("utm_medium") ?? "",
    utm_campaign: params.get("utm_campaign") ?? "",
  };
  return Object.fromEntries(Object.entries(candidates).filter(([, v]) => v));
}

/**
 * Mint a fresh signed session token, posting the Cloudflare Turnstile token so the
 * backend can verify the visitor is human before issuing a session (F15). `token` is
 * null when bot protection is disabled (no site key); the backend then no-ops the check.
 */
async function mintSession(
  apiBaseUrl: string,
  token: string | null,
): Promise<string> {
  const res = await fetch(`${apiBaseUrl}/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ turnstile_token: token }),
  });
  if (!res.ok) throw new Error(`session mint failed: ${res.status}`);
  const data: { session_id: string } = await res.json();
  localStorage.setItem(SESSION_KEY, data.session_id);
  return data.session_id;
}

/**
 * Parse the SSE stream the backend serves over POST (so EventSource can't be used).
 * Frames are `data: {...}\n\n`; we buffer across chunks, emit each `delta`, and stop
 * on the terminal `{done:true}` frame.
 */
async function readStream(
  res: Response,
  onDelta: (delta: string) => void,
): Promise<void> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? ""; // keep the trailing partial frame for next chunk
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      const payload = JSON.parse(line.slice(5).trim());
      if (payload.done) return;
      if (typeof payload.delta === "string") onDelta(payload.delta);
    }
  }
}

export default function ChatWidget({
  apiBaseUrl,
  greeting = DEFAULT_GREETING,
  brandName = DEFAULT_BRAND,
  suggestions = [],
  nudgeAfter,
  turnstileSiteKey,
}: ChatWidgetProps) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    { role: "bot", text: greeting },
  ]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [showNudge, setShowNudge] = useState(false);
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  // The widget is entirely browser-driven (localStorage, window, document.referrer) and
  // Framer Motion injects client-only attributes (e.g. tabindex) on its elements. Render
  // nothing until mounted so the server HTML and first client render agree — otherwise
  // hydration mismatches on the launcher button. useSyncExternalStore gives a stable
  // server snapshot (false) and client snapshot (true) without a setState-in-effect.
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );

  // Dedupe concurrent mints (React StrictMode double-invokes effects in dev).
  const sessionPromise = useRef<Promise<string> | null>(null);
  const turnstileRef = useRef<TurnstileInstance | null>(null);
  const scrollAnchor = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const wasOpen = useRef(false);
  const hydratedFor = useRef<string | null>(null); // token we've loaded history for
  const dirty = useRef(false); // only persist after a real user action
  const nudgeDone = useRef(false);

  const reduce = useReducedMotion();

  // Resolve a fresh Turnstile token, or null when bot protection is disabled (F15).
  // Failures resolve to null so /session returns 403 and the error surfaces on send.
  const getTurnstileToken = async (): Promise<string | null> => {
    if (!turnstileSiteKey) return null;
    try {
      return (await turnstileRef.current?.getResponsePromise()) ?? null;
    } catch {
      return null;
    }
  };

  const ensureSession = (forceNew = false): Promise<string> => {
    if (forceNew) {
      sessionPromise.current = null;
      localStorage.removeItem(SESSION_KEY);
      // Turnstile tokens are single-use, so reset for a fresh one before re-minting.
      turnstileRef.current?.reset();
    }
    if (!sessionPromise.current) {
      const existing = forceNew ? null : localStorage.getItem(SESSION_KEY);
      sessionPromise.current = (
        existing
          ? Promise.resolve(existing)
          : getTurnstileToken().then((t) => mintSession(apiBaseUrl, t))
      ).then((sid) => {
        setSessionToken(sid);
        hydrate(sid);
        return sid;
      });
    }
    return sessionPromise.current;
  };

  // Restore any persisted conversation for this token, once (F12). Runs in the session
  // resolution callback (not an effect body), and before any user action sets `dirty`,
  // so the persist effect below never clobbers stored history with the bare greeting.
  const hydrate = (token: string) => {
    if (hydratedFor.current === token) return;
    hydratedFor.current = token;
    try {
      const stored = localStorage.getItem(chatKey(token));
      if (stored) {
        const parsed = JSON.parse(stored) as Message[];
        if (Array.isArray(parsed) && parsed.length) setMessages(parsed);
      }
    } catch {
      /* ignore corrupt storage */
    }
  };

  const openPanel = () => {
    setShowNudge(false);
    setOpen(true);
  };
  const toggleOpen = () => {
    setShowNudge(false);
    setOpen((v) => !v);
  };

  // Mint the session as soon as the widget loads, so the first message is instant.
  useEffect(() => {
    ensureSession().catch(() => {
      /* swallowed; a failed mint surfaces when the visitor sends a message */
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist after a settled exchange (skip the hydration commit and mid-stream churn).
  useEffect(() => {
    if (!dirty.current || !sessionToken || isStreaming) return;
    try {
      localStorage.setItem(chatKey(sessionToken), JSON.stringify(messages));
    } catch {
      /* storage full / unavailable — non-fatal */
    }
  }, [messages, sessionToken, isStreaming]);

  // Keep the latest message in view as replies stream in.
  useEffect(() => {
    scrollAnchor.current?.scrollIntoView({
      behavior: reduce ? "auto" : "smooth",
    });
  }, [messages, open, reduce]);

  // Escape closes the panel (F12 a11y).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Focus to the input on open, back to the launcher on close (F12 a11y).
  useEffect(() => {
    if (open) inputRef.current?.focus();
    else if (wasOpen.current) launcherRef.current?.focus();
    wasOpen.current = open;
  }, [open]);

  // Proactive nudge: fire once after `nudgeAfter` seconds if still closed (F12).
  useEffect(() => {
    if (!nudgeAfter || open || nudgeDone.current) return;
    const t = setTimeout(() => {
      nudgeDone.current = true;
      setShowNudge(true);
    }, nudgeAfter * 1000);
    return () => clearTimeout(t);
  }, [nudgeAfter, open]);

  const send = async (raw: string) => {
    const text = raw.trim();
    if (!text || isStreaming) return;
    dirty.current = true;
    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", text },
      { role: "bot", text: "" }, // placeholder the stream fills in
    ]);
    setIsStreaming(true);

    const stream = (token: string) =>
      fetch(`${apiBaseUrl}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: token, message: text, ...getSource() }),
      });

    try {
      let sid = await ensureSession();
      let res = await stream(sid);
      // 401 self-heal: the token no longer verifies — re-mint once and retry (F12).
      if (res.status === 401) {
        sid = await ensureSession(true);
        res = await stream(sid);
      }
      if (!res.ok) throw new Error(`chat failed: ${res.status}`);
      await readStream(res, (delta) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { role: "bot", text: last.text + delta };
          return next;
        });
      });
    } catch {
      // Only replace the bubble if nothing streamed; keep a partial reply otherwise.
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last.role === "bot" && last.text === "") {
          next[next.length - 1] = { role: "bot", text: FALLBACK };
        }
        return next;
      });
    } finally {
      setIsStreaming(false);
    }
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  // Typing dots show while we're awaiting the first token of a reply.
  const awaitingReply =
    isStreaming &&
    messages.length > 0 &&
    messages[messages.length - 1].role === "bot" &&
    messages[messages.length - 1].text === "";

  // Chips show only before the visitor's first message.
  const showChips =
    suggestions.length > 0 && !messages.some((m) => m.role === "user");

  const panelMotion = reduce
    ? {
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        exit: { opacity: 0 },
        transition: { duration: 0.15 },
      }
    : {
        initial: { opacity: 0, y: 20, scale: 0.95 },
        animate: { opacity: 1, y: 0, scale: 1 },
        exit: { opacity: 0, y: 20, scale: 0.95 },
        transition: { type: "spring" as const, stiffness: 260, damping: 24 },
      };

  // Render nothing on the server and the first client pass; all hooks above still run, so
  // this only gates output (keeps the Rules of Hooks intact) and avoids the hydration diff.
  if (!mounted) return null;

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-4">
      <AnimatePresence>
        {open && (
          <motion.div
            key="panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="chat-widget-title"
            {...panelMotion}
            className="flex h-[32rem] w-[22rem] max-w-[calc(100vw-3rem)] flex-col overflow-hidden rounded-2xl bg-cream shadow-2xl ring-1 ring-black/5"
          >
            {/* Header */}
            <div className="flex items-center justify-between bg-forest px-4 py-3 text-cream">
              <span
                id="chat-widget-title"
                className="font-display text-lg font-semibold"
              >
                {brandName}
              </span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close chat"
                className="rounded-full p-1 text-cream/80 transition hover:bg-white/10 hover:text-cream"
              >
                <CloseIcon />
              </button>
            </div>

            {/* Messages */}
            <div
              role="log"
              aria-live="polite"
              aria-atomic="false"
              className="flex-1 space-y-3 overflow-y-auto px-4 py-4"
            >
              {messages.map((m, i) => (
                <MessageBubble key={i} role={m.role} text={m.text} />
              ))}
              {awaitingReply && <TypingDots reduce={!!reduce} />}

              {showChips && (
                <div className="flex flex-wrap gap-2 pt-1">
                  {suggestions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => void send(s)}
                      className="rounded-full border border-forest/20 bg-white px-3 py-1.5 text-xs font-medium text-forest transition hover:border-honey hover:bg-honey/10"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
              <div ref={scrollAnchor} />
            </div>

            {/* Composer */}
            <form
              onSubmit={onSubmit}
              className="flex items-center gap-2 border-t border-black/5 bg-cream px-3 py-3"
            >
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Type a message…"
                aria-label="Message"
                className="flex-1 rounded-full border border-black/10 bg-white px-4 py-2 text-sm text-forest outline-none placeholder:text-forest/40 focus:border-honey"
              />
              <button
                type="submit"
                disabled={!input.trim() || isStreaming}
                aria-label="Send message"
                className="grid h-9 w-9 place-items-center rounded-full bg-forest text-cream transition hover:bg-forest/90 disabled:opacity-40"
              >
                <SendIcon />
              </button>
            </form>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Proactive nudge */}
      <AnimatePresence>
        {showNudge && !open && (
          <motion.div
            key="nudge"
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className="flex max-w-[16rem] items-start gap-2 rounded-2xl rounded-br-sm bg-white px-4 py-3 text-sm text-forest shadow-xl ring-1 ring-black/5"
          >
            <button
              type="button"
              onClick={openPanel}
              className="text-left leading-snug"
            >
              Need a hand? I can answer questions and point you the right way. 🍯
            </button>
            <button
              type="button"
              onClick={() => setShowNudge(false)}
              aria-label="Dismiss"
              className="-mr-1 -mt-1 shrink-0 rounded-full p-1 text-forest/40 transition hover:text-forest"
            >
              <CloseIcon />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Bot check (F15): Turnstile runs on mount (execution defaults to "render") so a
          token is ready by the time the visitor sends; the signed session it unlocks
          proves humanity thereafter. "interaction-only" keeps it invisible unless a
          challenge is actually needed. Rendered only when a site key is configured. */}
      {turnstileSiteKey && (
        <Turnstile
          ref={turnstileRef}
          siteKey={turnstileSiteKey}
          options={{ appearance: "interaction-only" }}
        />
      )}

      {/* Launcher */}
      <motion.button
        ref={launcherRef}
        type="button"
        onClick={toggleOpen}
        whileHover={reduce ? undefined : { scale: 1.05 }}
        whileTap={reduce ? undefined : { scale: 0.95 }}
        aria-label={open ? "Close chat" : "Open chat"}
        aria-expanded={open}
        className="grid h-14 w-14 place-items-center rounded-full bg-forest text-cream shadow-lg"
      >
        {open ? <CloseIcon /> : <ChatIcon />}
      </motion.button>
    </div>
  );
}

function MessageBubble({ role, text }: Message) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
          isUser
            ? "rounded-br-sm bg-honey text-forest"
            : "rounded-bl-sm bg-sage text-forest"
        }`}
      >
        {text}
      </div>
    </div>
  );
}

function TypingDots({ reduce }: { reduce: boolean }) {
  return (
    <div className="flex justify-start">
      <div className="flex gap-1 rounded-2xl rounded-bl-sm bg-sage px-3.5 py-3">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-forest/50"
            animate={reduce ? undefined : { opacity: [0.3, 1, 0.3] }}
            transition={
              reduce ? undefined : { duration: 1, repeat: Infinity, delay: i * 0.2 }
            }
          />
        ))}
      </div>
    </div>
  );
}

function ChatIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}
