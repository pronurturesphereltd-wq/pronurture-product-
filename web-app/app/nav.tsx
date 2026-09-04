"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getSupabase } from "@/lib/supabase";
import { Identity } from "@/lib/identity";

const FACILITY_LINKS = [
  { href: "/rota", label: "Rota" },
  { href: "/import", label: "Import staff" },
  { href: "/compliance", label: "Compliance" },
];

const PROFESSIONAL_LINKS = [{ href: "/me", label: "My shifts" }];

export default function NavBar({ identity }: { identity?: Identity | null }) {
  const pathname = usePathname();
  const router = useRouter();

  async function signOut() {
    await getSupabase().auth.signOut();
    router.replace("/login");
  }

  // Until identity is known, show no links rather than guessing. Offering a
  // professional the facility pages is how they ended up on a page of 403s.
  const links = !identity
    ? []
    : identity.kind === "facility"
      ? FACILITY_LINKS
      : PROFESSIONAL_LINKS;

  const who =
    identity?.kind === "facility"
      ? identity.facility?.name
      : identity?.profile?.full_name;

  return (
    <nav className="top">
      {links.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          aria-current={pathname === link.href ? "page" : undefined}
        >
          {link.label}
        </Link>
      ))}
      {who && <span className="sub who">{who}</span>}
      <button type="button" className="secondary" onClick={signOut}>
        Sign out
      </button>
    </nav>
  );
}
