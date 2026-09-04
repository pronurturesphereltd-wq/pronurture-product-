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

type Colleague = {
  id: number;
  full_name: string;
  email: string;
  role: string;
  verification_state: string;
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

/**
 * Step one of offering a shift: choose who it goes to.
 *
 * The empty case is handled explicitly rather than left as a select with no
 * options. Nobody at the facility sharing this shift's role is a real and
 * unremarkable situation, and a silently empty dropdown reads as broken.
 */
function OfferPicker({
  shift,
  colleagues,
  chosen,
  setChosen,
  busy,
  onSend,
  onCancel,
}: {
  shift: Shift;
  colleagues: Colleague[] | null;
  chosen: string;
  setChosen: (value: string) => void;
  busy: boolean;
  onSend: () => void;
  onCancel: () => void;
}) {
  if (colleagues === null) return <span className="sub">Loading…</span>;

  if (colleagues.length === 0) {
    return (
      <>
        <span className="sub">
          No eligible colleague found for this role. A swap can only go to
          someone designated &lsquo;{shift.role}&rsquo; at your facility.
        </span>
        <br />
        <button className="secondary small" onClick={onCancel}>
          Close
        </button>
      </>
    );
  }

  return (
    <>
      <label htmlFor={`target-${shift.id}`} className="sr-only">
        Offer to
      </label>
      <select
        id={`target-${shift.id}`}
        value={chosen}
        onChange={(e) => setChosen(e.target.value)}
      >
        <option value="">Choose a colleague…</option>
        {colleagues.map((c) => (
          <option key={c.id} value={c.id}>
            {c.full_name}
          </option>
        ))}
      </select>
      <div className="row" style={{ marginTop: "0.4rem" }}>
        <button className="small" onClick={onSend} disabled={busy || !chosen}>
          {busy ? "Sending…" : "Send offer"}
        </button>
        <button className="secondary small" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </>
  );
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
  // "Now" as of the last fetch, not as of this render. Reading the clock
  // during render is impure — it changes between renders for no reason the
  // data reflects — and eslint rejects it. Every action refetches, so this
  // stays current with what the server would decide.
  const [asOf, setAsOf] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Offering is two steps now: pick a colleague, then send. `offering` holds
  // the shift whose picker is open; null means no picker is showing.
  const [offering, setOffering] = useState<Shift | null>(null);
  const [colleagues, setColleagues] = useState<Colleague[] | null>(null);
  const [chosen, setChosen] = useState("");

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
    setAsOf(Date.now());
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

  /** Step one: open the picker and load who this shift can go to. */
  async function startOffer(shift: Shift) {
    setBusy(`offer-${shift.id}`);
    setError(null);
    setNotice(null);
    setOffering(shift);
    setColleagues(null);
    setChosen("");
    try {
      const rows = await apiGet<Colleague[]>(
        `/api/rota/shifts/${shift.id}/eligible-colleagues/`,
      );
      setColleagues(rows);
      if (rows.length === 1) setChosen(String(rows[0].id));
    } catch (err) {
      setOffering(null);
      handleError(err);
    } finally {
      setBusy(null);
    }
  }

  /** Step two: send it to the colleague they picked. */
  function sendOffer(shift: Shift) {
    const target = colleagues?.find((c) => String(c.id) === chosen);
    if (!target) return;
    return act(
      `send-${shift.id}`,
      async () => {
        await apiPostJson(`/api/rota/shifts/${shift.id}/swap-request/`, {
          target_professional: target.id,
        });
        setOffering(null);
        setColleagues(null);
        setChosen("");
      },
      `Offered your ${shift.role} shift to ${target.full_name}. Only they can accept it.`,
    );
  }

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

  // Split on the same rule the API enforces. Offering a shift that has already
  // started is refused server-side, so rendering the button on one is showing
  // an action that cannot work — which is exactly how this page contradicted
  // itself on a shift that had finished hours earlier.
  const upcoming = shifts.filter((s) => new Date(s.start_time).getTime() > asOf);
  const past = shifts.filter((s) => new Date(s.start_time).getTime() <= asOf);

  // Same rule again: a pending offer whose shift has begun can no longer be
  // accepted, so it is not shown as acceptable. Nothing expires these
  // server-side — they stay pending until someone withdraws them.
  const offersFromOthers = swaps.filter(
    (s) =>
      s.status === "pending" &&
      s.requesting_professional !== me.id &&
      new Date(s.shift_start_time).getTime() > asOf,
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

      <h2>Coming up</h2>
      {loading && <p className="sub">Loading…</p>}
      {!loading && upcoming.length === 0 && (
        <p className="sub">
          No upcoming shifts.
          {past.length > 0 && " Your past shifts are below."} Draft shifts are
          not shown until your facility publishes them.
        </p>
      )}
      {upcoming.length > 0 && (
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
            {upcoming.map((shift) => {
              const offered = openOfferFor(shift.id);
              return (
                <tr key={shift.id}>
                  <td>{shift.role}</td>
                  <td>{shift.ward || <span className="sub">—</span>}</td>
                  <td>{formatWhen(shift.start_time)}</td>
                  <td>{formatWhen(shift.end_time)}</td>
                  <td>
                    {offered ? (
                      <>
                        <span className="sub">
                          Offered to{" "}
                          {offered.target_professional_name ?? "a colleague"}
                        </span>
                        <br />
                        <button
                          className="secondary small"
                          onClick={() => withdraw(offered)}
                          disabled={busy === `withdraw-${offered.id}`}
                        >
                          Withdraw offer
                        </button>
                      </>
                    ) : offering?.id === shift.id ? (
                      <OfferPicker
                        shift={shift}
                        colleagues={colleagues}
                        chosen={chosen}
                        setChosen={setChosen}
                        busy={busy === `send-${shift.id}`}
                        onSend={() => sendOffer(shift)}
                        onCancel={() => {
                          setOffering(null);
                          setColleagues(null);
                        }}
                      />
                    ) : (
                      <button
                        className="secondary small"
                        onClick={() => startOffer(shift)}
                        disabled={busy === `offer-${shift.id}`}
                      >
                        {busy === `offer-${shift.id}`
                          ? "Loading…"
                          : "Offer for swap"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {past.length > 0 && (
        <>
          <h3>Already started or finished</h3>
          <p className="sub">
            These cannot be offered for swap — the API refuses a shift that has
            started, so no button is shown rather than one that would fail.
          </p>
          <table>
            <thead>
              <tr>
                <th scope="col">Role</th>
                <th scope="col">Ward</th>
                <th scope="col">Started</th>
                <th scope="col">Ended</th>
              </tr>
            </thead>
            <tbody>
              {past.map((shift) => (
                <tr key={shift.id}>
                  <td>{shift.role}</td>
                  <td>{shift.ward || <span className="sub">—</span>}</td>
                  <td>{formatWhen(shift.start_time)}</td>
                  <td>{formatWhen(shift.end_time)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>Shifts offered by colleagues</h2>
      {!loading && offersFromOthers.length === 0 && (
        <p className="sub">
          Nobody has offered you a shift. Offers name one colleague, so you
          only ever see the ones meant for you.
        </p>
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
                  <br />
                  {/* Every offer is targeted now, and the list only ever
                      contains offers aimed at you — the API scopes it. */}
                  <span className="sub">offered to you</span>
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
