"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { homeFor, useIdentity } from "@/lib/identity";

/**
 * Send each kind of account to its own home.
 *
 * The two audiences share an entry point but not a landing page: a facility
 * belongs on the rota, a professional on their own shifts. Guessing wrong is
 * how a professional ended up looking at the facility's shift-creation form.
 */
export default function Home() {
  const router = useRouter();
  const { loading, identity, problem } = useIdentity();

  useEffect(() => {
    if (loading || !identity) return;
    router.replace(homeFor(identity.kind));
  }, [loading, identity, router]);

  // useIdentity has already redirected to /login if there is no session. A
  // problem here means a valid token with no usable PSL record; /rota renders
  // the explanation rather than duplicating it.
  useEffect(() => {
    if (!loading && problem) router.replace("/rota");
  }, [loading, problem, router]);

  return (
    <main>
      <p className="sub">Signing you in…</p>
    </main>
  );
}
