"use client";

import { getSupabase } from "./supabase";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export class NotAuthenticatedError extends Error {
  constructor(message = "You are not signed in.") {
    super(message);
    this.name = "NotAuthenticatedError";
  }
}

export class ApiError extends Error {
  status: number;
  /** Parsed body, when the backend returned JSON. DRF field errors land here. */
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Turn a DRF error body into one readable line.
 *
 * DRF answers with either {"detail": "..."} or {"field": ["msg", ...]}, and
 * showing the raw JSON to a facility admin is useless.
 */
function describe(status: number, body: unknown): string {
  if (typeof body === "string" && body.trim()) return body;
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    if (typeof record.detail === "string") return record.detail;
    const parts: string[] = [];
    for (const [field, value] of Object.entries(record)) {
      const text = Array.isArray(value) ? value.join(", ") : String(value);
      parts.push(field === "non_field_errors" ? text : `${field}: ${text}`);
    }
    if (parts.length) return parts.join(" · ");
  }
  return `Request failed with HTTP ${status}.`;
}

async function accessToken(forceRefresh = false): Promise<string> {
  if (forceRefresh) {
    const { data, error } = await getSupabase().auth.refreshSession();
    if (error || !data.session) {
      throw new NotAuthenticatedError("Your session expired. Sign in again.");
    }
    return data.session.access_token;
  }

  const { data, error } = await getSupabase().auth.getSession();
  if (error || !data.session) {
    throw new NotAuthenticatedError();
  }
  return data.session.access_token;
}

async function send(
  path: string,
  init: RequestInit,
  token: string,
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);

  // Only declare JSON for bodies that are actually JSON. Setting it on a
  // FormData body strips the multipart boundary the browser generates, and
  // the upload arrives unparseable — which is how the /import page would
  // break if this helper were careless.
  const isFormData =
    typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body !== undefined && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  return fetch(`${API_BASE}${path}`, { ...init, headers });
}

/**
 * Call the Django API with the current Supabase session attached as a Bearer
 * token — the same token `core/authentication.py` verifies against the
 * project's JWKS endpoint.
 *
 * On a 401 the session is refreshed once and the call retried, because an
 * access token expires after an hour and a facility admin should not be
 * bounced to the login page mid-import over it.
 */
export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await send(path, init, await accessToken());
  } catch (err) {
    if (err instanceof NotAuthenticatedError) throw err;
    throw new ApiError(
      0,
      `Could not reach the API at ${API_BASE}. Is the Django server running?`,
      null,
    );
  }

  if (response.status === 401) {
    response = await send(path, init, await accessToken(true));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let body: unknown = text;
  if (text && response.headers.get("content-type")?.includes("json")) {
    try {
      body = JSON.parse(text);
    } catch {
      /* keep the raw text */
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, describe(response.status, body), body);
  }
  return body as T;
}

export function apiGet<T = unknown>(path: string) {
  return apiFetch<T>(path, { method: "GET" });
}

export function apiPostJson<T = unknown>(path: string, data: unknown) {
  return apiFetch<T>(path, { method: "POST", body: JSON.stringify(data) });
}

export function apiPostForm<T = unknown>(path: string, form: FormData) {
  return apiFetch<T>(path, { method: "POST", body: form });
}
