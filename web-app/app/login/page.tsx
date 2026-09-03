"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSupabase, supabaseConfigError } from "@/lib/supabase";

type Mode = "signin" | "signup";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  // Checked during render, so a missing env var shows a setup message rather
  // than throwing a React error overlay that buries the cause.
  const configError = supabaseConfigError();

  // Already signed in? Skip the form.
  useEffect(() => {
    if (configError) return;
    let active = true;
    getSupabase()
      .auth.getSession()
      .then(({ data }) => {
        if (!active) return;
        if (data.session) router.replace("/rota");
        else setChecking(false);
      });
    return () => {
      active = false;
    };
  }, [router, configError]);

  if (configError) {
    return (
      <main>
        <h1>Setup needed</h1>
        <p className="sub">The facility app is not configured yet.</p>
        <div className="notice error">{configError}</div>
      </main>
    );
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setInfo(null);

    try {
      if (mode === "signup") {
        const { data, error } = await getSupabase().auth.signUp({
          email,
          password,
        });
        if (error) throw error;
        if (!data.session) {
          // Email confirmation is on, so there is no session to redirect with.
          setInfo(
            "Account created. Check your inbox to confirm the address, then sign in.",
          );
          setMode("signin");
          return;
        }
      } else {
        const { error } = await getSupabase().auth.signInWithPassword({
          email,
          password,
        });
        if (error) throw error;
      }
      router.replace("/rota");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (checking) {
    return (
      <main>
        <p className="sub">Checking your session…</p>
      </main>
    );
  }

  return (
    <main>
      <h1>PSL facility sign in</h1>
      <p className="sub">
        {mode === "signin"
          ? "Sign in to manage your staff and rota."
          : "Create a facility account."}
      </p>

      <form onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={6}
            autoComplete={
              mode === "signin" ? "current-password" : "new-password"
            }
          />
        </div>

        {error && <div className="notice error">{error}</div>}
        {info && <div className="notice ok">{info}</div>}

        <div className="row">
          <button type="submit" disabled={busy}>
            {busy
              ? "Working…"
              : mode === "signin"
                ? "Sign in"
                : "Create account"}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => {
              setMode(mode === "signin" ? "signup" : "signin");
              setError(null);
              setInfo(null);
            }}
          >
            {mode === "signin" ? "Need an account?" : "Have an account?"}
          </button>
        </div>
      </form>
    </main>
  );
}
