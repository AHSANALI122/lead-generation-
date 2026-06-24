// Shared types for the admin side of the app.

/**
 * One lead, mirroring the backend `Lead` model (backend/models.py). Optional columns
 * arrive as `null` (not `undefined`) over JSON, so they're typed `string | null`.
 * `created_at` / `updated_at` are ISO strings as serialized by FastAPI.
 */
export interface Lead {
  id: number;
  session_id: string;

  name: string | null;
  email: string | null;
  phone: string | null;

  budget: string | null;
  authority: string | null;
  need: string | null;
  timeline: string | null;

  qualified: boolean;
  score: number;
  notes: string | null;

  page_url: string | null;
  referrer: string | null;
  utm_source: string | null;
  utm_medium: string | null;
  utm_campaign: string | null;

  created_at: string;
  updated_at: string;
}
