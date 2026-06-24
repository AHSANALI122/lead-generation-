"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";

import type { Lead } from "@/lib/types";

// Client dashboard (F13). Receives leads as a prop from the admin server component —
// it never fetches and never sees the admin token. The list arrives already sorted
// best-first (score desc, created_at desc) by the backend.
interface LeadsDashboardProps {
  leads: Lead[];
}

const BANT_FIELDS = ["budget", "authority", "need", "timeline"] as const;
type BantField = (typeof BANT_FIELDS)[number];

const hasText = (v: string | null) => Boolean(v && v.trim());

// How many of the four BANT signals are filled in — drives the 4-dot indicator.
function bantCount(lead: Lead): number {
  return BANT_FIELDS.filter((f) => hasText(lead[f])).length;
}

function formatDate(iso: string): string {
  // created_at/updated_at arrive as naive UTC from the backend (no offset). Append "Z" when
  // there's no timezone marker so it parses as UTC identically on server and client, then
  // format with a fixed locale + UTC zone. A bare `undefined` locale resolves to the
  // runtime default (Node → en-US, browser → the user's locale), which mismatches on
  // hydration ("Jun 24, 2026" vs "24 Jun 2026").
  const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(normalized);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      });
}

export default function LeadsDashboard({ leads }: LeadsDashboardProps) {
  const router = useRouter();
  const [isRefreshing, startRefresh] = useTransition();
  const [filter, setFilter] = useState<"all" | "qualified">("all");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  // Capture "now" once at mount (lazy init runs a single time) — calling Date.now()
  // directly during render is impure. A Refresh remounts the data path anyway.
  const [mountedAt] = useState(() => Date.now());

  // Headline metrics. Memoized so they only recompute when the lead set changes.
  const metrics = useMemo(() => {
    const total = leads.length;
    const qualified = leads.filter((l) => l.qualified).length;
    const rate = total ? Math.round((qualified / total) * 100) : 0;
    const weekAgo = mountedAt - 7 * 24 * 60 * 60 * 1000;
    const lastWeek = leads.filter((l) => {
      const t = new Date(l.created_at).getTime();
      return !Number.isNaN(t) && t >= weekAgo;
    }).length;
    return { total, qualified, rate, lastWeek };
  }, [leads, mountedAt]);

  const visible = useMemo(
    () => (filter === "qualified" ? leads.filter((l) => l.qualified) : leads),
    [leads, filter],
  );

  const refresh = () => startRefresh(() => router.refresh());

  return (
    <main className="min-h-screen bg-cream px-4 py-8 text-forest sm:px-8">
      <div className="mx-auto max-w-5xl space-y-8">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="font-display text-3xl font-semibold text-forest">Leads</h1>
            <p className="text-sm text-forest/60">
              Visitors qualified by the assistant, best first.
            </p>
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={isRefreshing}
            className="rounded-full bg-forest px-4 py-2 text-sm font-medium text-cream transition hover:bg-forest/90 disabled:opacity-50"
          >
            {isRefreshing ? "Refreshing…" : "Refresh"}
          </button>
        </header>

        {/* Metric cards */}
        <section className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <MetricCard label="Total leads" value={metrics.total} />
          <MetricCard label="Qualified" value={metrics.qualified} />
          <MetricCard label="Qualified rate" value={`${metrics.rate}%`} />
          <MetricCard label="Last 7 days" value={metrics.lastWeek} />
        </section>

        {/* Filter */}
        <div className="flex gap-2">
          <FilterButton
            active={filter === "all"}
            onClick={() => setFilter("all")}
            label={`All (${metrics.total})`}
          />
          <FilterButton
            active={filter === "qualified"}
            onClick={() => setFilter("qualified")}
            label={`Qualified (${metrics.qualified})`}
          />
        </div>

        {/* Rows */}
        <section className="space-y-2">
          {visible.length === 0 ? (
            <p className="rounded-2xl bg-sage/60 px-4 py-8 text-center text-sm text-forest/60">
              No leads to show yet.
            </p>
          ) : (
            visible.map((lead) => (
              <LeadRow
                key={lead.id}
                lead={lead}
                expanded={expandedId === lead.id}
                onToggle={() =>
                  setExpandedId((cur) => (cur === lead.id ? null : lead.id))
                }
              />
            ))
          )}
        </section>
      </div>
    </main>
  );
}

function MetricCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-2xl bg-white px-4 py-4 shadow-sm ring-1 ring-black/5">
      <div className="font-display text-2xl font-semibold text-forest">{value}</div>
      <div className="text-xs text-forest/60">{label}</div>
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
        active
          ? "bg-forest text-cream"
          : "bg-white text-forest ring-1 ring-black/10 hover:bg-sage"
      }`}
    >
      {label}
    </button>
  );
}

function LeadRow({
  lead,
  expanded,
  onToggle,
}: {
  lead: Lead;
  expanded: boolean;
  onToggle: () => void;
}) {
  // Prefer a name, then email; fall back to a short slice of the session id so every
  // row has a stable, readable label.
  const title =
    lead.name?.trim() ||
    lead.email?.trim() ||
    `Session ${lead.session_id.slice(0, 8)}…`;

  return (
    <div className="overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-black/5">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-4 px-4 py-3 text-left transition hover:bg-sage/40"
      >
        <ScoreBadge score={lead.score} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium text-forest">{title}</span>
            {lead.qualified && (
              <span className="shrink-0 rounded-full bg-honey/20 px-2 py-0.5 text-xs font-medium text-honey">
                Qualified
              </span>
            )}
          </div>
          {lead.email && lead.name && (
            <div className="truncate text-xs text-forest/50">{lead.email}</div>
          )}
        </div>
        <BantDots count={bantCount(lead)} />
        <span className="hidden shrink-0 text-xs text-forest/50 sm:block">
          {formatDate(lead.created_at)}
        </span>
      </button>

      {expanded && <LeadDetail lead={lead} />}
    </div>
  );
}

function ScoreBadge({ score }: { score: number }) {
  return (
    <div className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-forest text-cream">
      <span className="font-display text-sm font-semibold">{score}</span>
    </div>
  );
}

function BantDots({ count }: { count: number }) {
  return (
    <div
      className="hidden shrink-0 items-center gap-1 sm:flex"
      aria-label={`${count} of 4 BANT signals collected`}
      title={`${count}/4 BANT`}
    >
      {BANT_FIELDS.map((_, i) => (
        <span
          key={i}
          className={`h-2 w-2 rounded-full ${i < count ? "bg-honey" : "bg-sage"}`}
        />
      ))}
    </div>
  );
}

function LeadDetail({ lead }: { lead: Lead }) {
  return (
    <div className="space-y-4 border-t border-black/5 px-4 py-4 text-sm">
      {/* BANT */}
      <div className="grid gap-3 sm:grid-cols-2">
        {BANT_FIELDS.map((f) => (
          <Field key={f} label={capitalize(f)} value={lead[f as BantField]} />
        ))}
      </div>

      {/* Contact + notes */}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Email" value={lead.email} />
        <Field label="Phone" value={lead.phone} />
      </div>
      <Field label="Notes" value={lead.notes} />

      {/* Source / attribution */}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Page URL" value={lead.page_url} />
        <Field label="Referrer" value={lead.referrer} />
        <Field label="UTM source" value={lead.utm_source} />
        <Field label="UTM medium" value={lead.utm_medium} />
        <Field label="UTM campaign" value={lead.utm_campaign} />
      </div>

      <div className="text-xs text-forest/40">
        Updated {formatDate(lead.updated_at)} · session {lead.session_id.slice(0, 12)}…
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-forest/40">
        {label}
      </div>
      <div className="break-words text-forest/80">
        {hasText(value) ? value : <span className="text-forest/30">—</span>}
      </div>
    </div>
  );
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
