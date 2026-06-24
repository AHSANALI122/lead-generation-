import LeadsDashboard from "@/components/LeadsDashboard";
import type { Lead } from "@/lib/types";

// Admin dashboard (F13). This is a SERVER component: it reads the admin token from the
// server-only env (no NEXT_PUBLIC_ prefix) and fetches /leads here, so the token is
// used to sign the request on the server and never reaches the browser. The rendered
// LeadsDashboard is a client component that only receives the lead data as a prop.
//
// Always render at request time with fresh data — never cache an authed admin list.
export const dynamic = "force-dynamic";

// A clear, friendly message shown instead of the dashboard when we can't load leads.
// Mirrors the backend's "never a stack trace" rule.
function Notice({ title, body }: { title: string; body: string }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-cream px-6 text-center">
      <div className="max-w-md space-y-3">
        <h1 className="font-display text-3xl font-semibold text-forest">{title}</h1>
        <p className="text-forest/70">{body}</p>
      </div>
    </main>
  );
}

export default async function AdminPage() {
  const token = process.env.ADMIN_TOKEN;
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL;

  // Fail closed and clearly if the dashboard isn't wired up yet.
  if (!token) {
    return (
      <Notice
        title="Admin dashboard isn't configured"
        body="Set ADMIN_TOKEN (server-only) in frontend/.env.local to view leads."
      />
    );
  }
  if (!apiBase) {
    return (
      <Notice
        title="Admin dashboard isn't configured"
        body="Set NEXT_PUBLIC_API_BASE_URL in frontend/.env.local to reach the backend."
      />
    );
  }

  let res: Response;
  try {
    res = await fetch(`${apiBase}/leads`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store", // per-request authed data; never cache it
    });
  } catch {
    return (
      <Notice
        title="Couldn't reach the backend"
        body="The leads API didn't respond. Is the backend running and NEXT_PUBLIC_API_BASE_URL correct?"
      />
    );
  }

  if (res.status === 401) {
    return (
      <Notice
        title="Unauthorized"
        body="The admin token was rejected. Check that ADMIN_TOKEN matches the backend's."
      />
    );
  }
  if (res.status === 503) {
    return (
      <Notice
        title="Admin API is unavailable"
        body="The backend has no ADMIN_TOKEN configured, so the leads API is disabled."
      />
    );
  }
  if (!res.ok) {
    return (
      <Notice
        title="Couldn't load leads"
        body={`The leads API returned an unexpected status (${res.status}). Please try again.`}
      />
    );
  }

  const leads: Lead[] = await res.json();
  return <LeadsDashboard leads={leads} />;
}
