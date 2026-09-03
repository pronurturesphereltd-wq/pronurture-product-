"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getSupabase } from "@/lib/supabase";

export default function NavBar() {
  const pathname = usePathname();
  const router = useRouter();

  async function signOut() {
    await getSupabase().auth.signOut();
    router.replace("/login");
  }

  return (
    <nav className="top">
      <Link
        href="/rota"
        aria-current={pathname === "/rota" ? "page" : undefined}
      >
        Rota
      </Link>
      <Link
        href="/import"
        aria-current={pathname === "/import" ? "page" : undefined}
      >
        Import staff
      </Link>
      <button type="button" className="secondary" onClick={signOut}>
        Sign out
      </button>
    </nav>
  );
}
