"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * The client is built on first use, not at import.
 *
 * These pages are Client Components, but Next still evaluates their modules on
 * the server while prerendering. Constructing (and validating) at import time
 * therefore breaks `next build` on any machine without a .env.local — CI, a
 * fresh clone, a container. Deferring keeps the build independent of runtime
 * credentials while still failing loudly the moment something needs Supabase.
 */
let client: SupabaseClient | null = null;

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `${name} is not set. Copy web-app/.env.example to .env.local and fill it in.`,
    );
  }
  return value;
}

/**
 * Describe what is wrong with the environment, or null if it is usable.
 *
 * Pure and side-effect free so a component can call it during render to show a
 * setup message, rather than discovering the problem by throwing inside an
 * effect.
 */
export function supabaseConfigError(): string | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

  if (key?.startsWith("sb_secret_")) {
    return (
      "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY holds a SECRET key (sb_secret_). " +
      "Use the publishable key (sb_publishable_) — the secret key belongs only " +
      "in the Django backend's .env and must never reach a browser."
    );
  }
  for (const [name, value] of [
    ["NEXT_PUBLIC_SUPABASE_URL", url],
    ["NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", key],
  ] as const) {
    if (!value) {
      return `${name} is not set. Copy web-app/.env.example to .env.local, fill it in, and restart the dev server.`;
    }
  }
  return null;
}

export function getSupabase(): SupabaseClient {
  if (client) return client;

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

  if (publishableKey?.startsWith("sb_secret_")) {
    // The secret key bypasses row-level security. It must never reach a browser.
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY holds a SECRET key (sb_secret_). " +
        "Use the publishable key (sb_publishable_) — the secret key belongs " +
        "only in the Django backend's .env.",
    );
  }

  client = createClient(
    required("NEXT_PUBLIC_SUPABASE_URL", url),
    required("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", publishableKey),
  );
  return client;
}
