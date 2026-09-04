"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPostJson } from "@/lib/api";
import { Identity, useApiErrorHandler } from "@/lib/identity";
import RequireKind from "@/app/guard";

type Shift = {
  id: number;
  role: string;
  ward: string;
  start_time: string;
  end_time: string;
};

type Swap = {
  id: number;
  shift: number;
  shift_role: string;
  shift_ward: string;
  shift_start_time: string;
  requesting_professional: number;
  requesting_professional_name: string;
  target_professional_name: string | null;
  accepted_by_name: string | null;
  status: string;
};

type Leave = {
  id: number;
  start_date: string;
  end_date: string;
  days: number;
  reason: string;
  status: string;
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

function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}

export default function MyShiftsPage() {
  return (
    <RequireKind kind="professional">
      {(identity) => <ProfessionalView identity={identity} />}
    </RequireKind>
  );
}

function ProfessionalView({ identity }: { identity: Identity }) {
  const me = identity.profile!;
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [swaps, setSwaps] = useState<Swap[]>([]);
  const [leave, setLeave] = useState<Leave[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [reason, setReason] = useState("");

  const handleError = useApiErrorHandler(setError);

  const load = useCallback(async () => {
    setError(null);
    const [shiftRows, swapRows, leaveRows] = await Promise.all([
      apiGet<Shift[]>("/api/rota/shifts/"),
      apiGet<Swap[]>("/api/rota/swap-requests/"),
      apiGet<Leave[]>("/api/leave/applications/"),
    ]);
    setShifts(shiftRows);
    setSwaps(swapRows);
    setLeave(leaveRows);
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        await load();
      } catch (err) {
        if (active) handleError(err);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [load, handleError]);

  async function act(key: string, run: () => Promise<void>, done: string) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await run();
      setNotice(done);
      await load();
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(null);
    }
  }

  const offer = (shift: Shift) =>
    act(
      `offer-${shift.id}`,
      () => apiPostJson(`/api/rota/shifts/${shift.id}/swap-request/`, {}),
      `Offered your ${shift.role} shift for swap.`,
    );

  const withdraw = (swap: Swap) =>
    act(
      `withdraw-${swap.id}`,
      () => apiPostJson(`/api/rota/swap-requests/${swap.id}/cancel/`, {}),
      "Offer withdrawn.",
    );

  const accept = (swap: Swap) =>
    act(
      `accept-${swap.id}`,
      () => apiPostJson(`/api/rota/swap-requests/${swap.id}/accept/`, {}),
      `You have taken the ${swap.shift_role} shift.`,
    );

  async function applyForLeave(event: React.FormEvent) {
    event.preventDefault();
    await act(
      "leave",
      async () => {
        await apiPostJson("/api/leave/applications/", {
          start_date: startDate,
          end_date: endDate,
          reason,
        });
        setStartDate("");
        setEndDate("");
        setReason("");
      },
      "Leave application submitted. Your facility will approve or decline it.",
    );
  }

  // A shift already offered should show "withdraw", not "offer" — the API
  // would refuse a second open request on it anyway.
  const openOfferFor = (shiftId: number) =>
    swaps.find((s) => s.shift === shiftId && s.status === "pending");

  const offersFromOthers = swaps.filter(
    (s) => s.status === "pending" && s.requesting_professional !== me.id,
  );
  const myHistory = swaps.filter((s) => s.status !== "pending");

  return (
    <>
      <h1>My shifts</h1>
      <p className="sub">
        {me.full_name}
        {me.role ? ` — ${me.role}` : ""}
        {identity.facility ? ` at ${identity.facility.name}` : ""}
      </p>

      {error && <div className="notice error">{error}</div>}
      {notice && <div className="notice ok">{notice}</div>}

      {!me.role && (
        <div className="notice error">
          You have no designated role yet, so you cannot accept a shift swap
          from anyone. Ask your facility to set one.
        </div>
      )}

      <h2>Assigned to me</h2>
      {loading && <p className="sub">Loading…</p>}
      {!loading && shifts.length === 0 && (
        <p className="sub">
          No published shifts. Draft shifts are not shown until your facility
          publishes them.
        </p>
      )}
      {shifts.length > 0 && (
        <table>
          <thead>
            <tr>
              <th scope="col">Role</th>
              <th scope="col">Ward</th>
              <th scope="col">Starts</th>
              <th scope="col">Ends</th>
              <th scope="col"></th>
            </tr>
          </thead>
          <tbody>
            {shifts.map((shift) => {
              const offered = openOfferFor(shift.id);
              return (
                <tr key={shift.id}>
                  <td>{shift.role}</td>
                  <td>{shift.ward || <span className="sub">—</span>}</td>
                  <td>{formatWhen(shift.start_time)}</td>
                  <td>{formatWhen(shift.end_time)}</td>
                  <td>
                    {offered ? (
                      <button
                        className="secondary small"
                        onClick={() => withdraw(offered)}
                        disabled={busy === `withdraw-${offered.id}`}
                      >
                        Withdraw offer
                      </button>
                    ) : (
                      <button
                        className="secondary small"
                        onClick={() => offer(shift)}
                        disabled={busy === `offer-${shift.id}`}
                      >
                        Offer for swap
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <h2>Shifts offered by colleagues</h2>
      {!loading && offersFromOthers.length === 0 && (
        <p className="sub">Nobody has offered a shift you could take.</p>
      )}
      {offersFromOthers.length > 0 && (
        <table>
          <thead>
            <tr>
              <th scope="col">Role</th>
              <th scope="col">Offered by</th>
              <th scope="col">Starts</th>
              <th scope="col"></th>
            </tr>
          </thead>
          <tbody>
            {offersFromOthers.map((swap) => (
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
                <td>
                  {swap.requesting_professional_name}
                  {swap.target_professional_name && (
                    <>
                      <br />
                      <span className="sub">offered to you directly</span>
                    </>
                  )}
                </td>
                <td>{formatWhen(swap.shift_start_time)}</td>
                <td>
                  <button
                    className="small"
                    onClick={() => accept(swap)}
                    disabled={busy === `accept-${swap.id}`}
                  >
                    Accept
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {myHistory.length > 0 && (
        <>
          <h3>Settled swaps</h3>
          <table>
            <thead>
              <tr>
                <th scope="col">Role</th>
                <th scope="col">Offered by</th>
                <th scope="col">Taken by</th>
                <th scope="col">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {myHistory.map((swap) => (
                <tr key={swap.id}>
                  <td>{swap.shift_role}</td>
                  <td>{swap.requesting_professional_name}</td>
                  <td>{swap.accepted_by_name ?? <span className="sub">—</span>}</td>
                  <td>
                    <span
                      className={`badge ${swap.status === "accepted" ? "ok" : "muted"}`}
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

      <h2>Leave</h2>
      <form onSubmit={applyForLeave}>
        <div className="field">
          <label htmlFor="start">First day</label>
          <input
            id="start"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="end">Last day</label>
          <input
            id="end"
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="reason">Reason</label>
          <input
            id="reason"
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Optional"
          />
        </div>
        <button type="submit" disabled={busy === "leave"}>
          {busy === "leave" ? "Submitting…" : "Apply for leave"}
        </button>
      </form>

      {leave.length > 0 && (
        <table>
          <thead>
            <tr>
              <th scope="col">Dates</th>
              <th scope="col">Days</th>
              <th scope="col">Reason</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {leave.map((row) => (
              <tr key={row.id}>
                <td>
                  {formatDate(row.start_date)} – {formatDate(row.end_date)}
                </td>
                <td>{row.days}</td>
                <td>{row.reason || <span className="sub">—</span>}</td>
                <td>
                  <span
                    className={`badge ${
                      row.status === "approved"
                        ? "ok"
                        : row.status === "declined"
                          ? "bad"
                          : "muted"
                    }`}
                  >
                    {row.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
