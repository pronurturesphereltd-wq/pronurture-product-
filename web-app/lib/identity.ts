"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSupabase } from "./supabase";
import { ApiError, NotAuthenticatedError, apiGet } from "./api";

export type Identity = {
  kind: "facility" | "professional";
  facility: { id: number; name: string; status: string } | null;
  profile: {
    id: number;
    full_name: string;
    email: string;
    role: string;
    verification_state: string;
  } | null;
};

export type IdentityState = {
  /** Still checking the session or asking the API. Render nothing yet. */
  loading: boolean;
  identity: Identity | null;
  /** Set when the token is valid but PSL has no usable record for it. */
  problem: string | null;
};

/**
 * Who is signed in, and what are they allowed to be shown?
 *
 * A Supabase token proves identity, not role. Before this existed the app
 * rendered facility controls to whoever held a token — a professional signing
 * in got the shift-creation form and a page-level 403, because two of the
 * four requests it fires are facility-only.
 *
 * A 403 from /api/me/ is information, not a failure: the token is good but
 * PSL has no record for it, or the facility is not approved yet. The message
 * says which, so it is surfaced rather than swallowed.
 */
export function useIdentity(): IdentityState {
  const router = useRouter();
  const [state, setState] = useState<IdentityState>({
    loading: true,
    identity: null,
    problem: null,
  });

  useEffect(() => {
    let active = true;

    void (async () => {
      const { data } = await getSupabase().auth.getSession();
      if (!active) return;
      if (!data.session) {
        router.replace("/login");
        return;
      }
      try {
        const identity = await apiGet<Identity>("/api/me/");
        if (active) setState({ loading: false, identity, problem: null });
      } catch (err) {
        if (!active) return;
        if (err instanceof NotAuthenticatedError) {
          router.replace("/login");
          return;
        }
        setState({
          loading: false,
          identity: null,
          problem:
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : String(err),
        });
      }
    })();

    return () => {
      active = false;
    };
  }, [router]);

  return state;
}

/** The page each kind of account should land on. */
export function homeFor(kind: Identity["kind"]): string {
  return kind === "facility" ? "/rota" : "/me";
}

/**
 * Shared error handling for the API calls a page makes after identity is
 * known. Kept here so every page reports a 403 the same way.
 */
export function useApiErrorHandler(setError: (message: string) => void) {
  const router = useRouter();
  return useCallback(
    (err: unknown) => {
      if (err instanceof NotAuthenticatedError) {
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    },
    [router, setError],
  );
}
