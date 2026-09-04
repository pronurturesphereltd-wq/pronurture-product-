"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

type Swap = {
  id: number;
  shift: number;
  shift_role: string;
  shift_ward: string;
  shift_start_time: string;
  requesting_professional_name: string;
  target_professional_name: string | null;
  accepted_by_name: string | null;
  status: string;
  created_at: string;
  decided_at: string | null;
};

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Swap requests on this facility's shifts.
 *
 * Read-only by design. Swaps are peer-to-peer and complete without management
 * — the API will refuse a facility that tries to accept or cancel one. This
 * panel exists so a facility can still see who is actually working a shift,
 * which is the visibility the spec asks for rather than an approval gate.
 */
export default function SwapRequests({
  reloadKey,
  onError,
}: {
  reloadKey: number;
  onError: (err: unknown) => void;
}) {
  const [swaps, setSwaps] = useState<Swap[]>([]);
  const [loading, setLoading] = useState(true);

  // Every setState here lands after the await, never in the synchronous body
  // of the effect — React treats a synchronous write as a cascading render and
  // eslint rejects it. `active` drops the result of a fetch whose reloadKey has
  // already been superseded, so a slow response cannot overwrite a newer one.
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const rows = await apiGet<Swap[]>("/api/rota/swap-requests/");
        if (active) setSwaps(rows);
      } catch (err) {
        if (active) onError(err);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [onError, reloadKey]);

  const open = swaps.filter((s) => s.status === "pending");
  const settled = swaps.filter((s) => s.status !== "pending");

  return (
    <>
      <h2>Swap requests</h2>
      {loading && <p className="sub">Loading…</p>}

      {!loading && swaps.length === 0 && (
        <p className="sub">
          Nobody has offered a shift for swap. Professionals open these
          themselves; there is nothing to approve here. Only someone designated
          for the shift&apos;s exact role can accept one.
        </p>
      )}

      {open.length > 0 && (
        <table>
          <thead>
            <tr>
              <th scope="col">Shift</th>
              <th scope="col">Offered by</th>
              <th scope="col">Offered to</th>
              <th scope="col">Starts</th>
            </tr>
          </thead>
          <tbody>
            {open.map((swap) => (
              <tr key={swap.id}>
                <td>
                  {swap.shift_role}
                  {swap.shift_ward && (
                    <>
                      <br />
                      <span className="sub">{swap.shift_ward}</span>
                    </>
                  )}
                </td>
                <td>{swap.requesting_professional_name}</td>
                <td>
                  {swap.target_professional_name ?? (
                    <span className="sub">Anyone on the roster</span>
                  )}
                </td>
                <td>{formatWhen(swap.shift_start_time)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {settled.length > 0 && (
        <>
          <h3>Settled</h3>
          <table>
            <thead>
              <tr>
                <th scope="col">Shift</th>
                <th scope="col">Was</th>
                <th scope="col">Now</th>
                <th scope="col">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {settled.map((swap) => (
                <tr key={swap.id}>
                  <td>
                    {swap.shift_role}
                    <br />
                    <span className="sub">
                      {formatWhen(swap.shift_start_time)}
                    </span>
                  </td>
                  <td>{swap.requesting_professional_name}</td>
                  <td>
                    {swap.accepted_by_name ?? <span className="sub">—</span>}
                  </td>
                  <td>
                    <span
                      className={`badge ${
                        swap.status === "accepted" ? "ok" : "muted"
                      }`}
                    >
                      {swap.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
