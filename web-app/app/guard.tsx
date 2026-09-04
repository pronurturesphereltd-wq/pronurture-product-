"use client";

import Link from "next/link";
import NavBar from "@/app/nav";
import { Identity, homeFor, useIdentity } from "@/lib/identity";

/**
 * Renders a page only for the kind of account it was built for.
 *
 * Every page here calls endpoints that are facility-only or professional-only.
 * Without this the wrong audience gets a half-rendered page and a scatter of
 * 403s, which is exactly what a professional used to see on /rota.
 */
export default function RequireKind({
  kind,
  children,
}: {
  kind: Identity["kind"];
  children: (identity: Identity) => React.ReactNode;
}) {
  const { loading, identity, problem } = useIdentity();

  if (loading) {
    return (
      <main>
        <p className="sub">Checking your session…</p>
      </main>
    );
  }

  // Valid token, no usable PSL record — an account that signed up and got no
  // further, or a facility still waiting on approval. The API's message says
  // which, and it is more useful than anything this component could invent.
  if (problem) {
    return (
      <>
        <NavBar />
        <main>
          <h1>Nothing to show yet</h1>
          <div className="notice error">{problem}</div>
          <p className="sub">
            If you have just registered, PSL needs to approve the account before
            it can be used.
          </p>
        </main>
      </>
    );
  }

  if (!identity) return null;

  if (identity.kind !== kind) {
    const theirs = homeFor(identity.kind);
    return (
      <>
        <NavBar />
        <main>
          <h1>Not your page</h1>
          <p className="sub">
            This page is for {kind === "facility" ? "facilities" : "professionals"}
            , and you are signed in as{" "}
            {identity.kind === "facility"
              ? identity.facility?.name
              : identity.profile?.full_name}
            .
          </p>
          <Link href={theirs}>Go to your {theirs === "/rota" ? "rota" : "shifts"}</Link>
        </main>
      </>
    );
  }

  return (
    <>
      <NavBar identity={identity} />
      <main>{children(identity)}</main>
    </>
  );
}
