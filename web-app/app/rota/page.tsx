"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSupabase } from "@/lib/supabase";
import {
  ApiError,
  NotAuthenticatedError,
  apiGet,
  apiPostJson,
} from "@/lib/api";
import NavBar from "@/app/nav";
import SwapRequests from "./swaps";
import LeaveQueue from "./leave";

type Shift = {
  id: number;
  professional: number | null;
  professional_name: string | null;
  role: string;
  ward: string;
  start_time: string;
  end_time: string;
  is_published: boolean;
  published_at: string | null;
  reminder_sent: boolean;
};

type Staff = {
  id: number;
  full_name: string;
  email: string;
  role: string;
  verification_state: string;
};

type PublishResult = {
  published: number;
  notifications_queued: number;
  unassigned_shifts: number;
  not_published: number[];
};

/**
 * <input type="datetime-local"> yields a zone-less local string. The API stores
 * absolute instants, so convert before sending or a shift lands hours out.
 */
function toIso(localValue: string): string {
  return new Date(localValue).toISOString();
}

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function RotaPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [publishing, setPublishing] = useState(false);
  // Bumped whenever the shift list is reloaded, so the swap and leave panels
  // refetch alongside it. An accepted swap reassigns a shift, so the two views
  // would otherwise disagree until the page was reloaded by hand.
  const [reloadKey, setReloadKey] = useState(0);

  const [role, setRole] = useState("");
  const [ward, setWard] = useState("");
  const [professional, setProfessional] = useState("");
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");

  const handleError = useCallback(
    (err: unknown) => {
      if (err instanceof NotAuthenticatedError) {
        router.replace("/login");
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setError(
          `${err.message} An account must own an approved facility to manage a rota.`,
        );
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    },
    [router],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [shiftRows, staffRows] = await Promise.all([
        apiGet<Shift[]>("/api/rota/shifts/"),
        apiGet<Staff[]>("/api/facilities/staff/"),
      ]);
      setShifts(shiftRows);
      setStaff(staffRows);
      // Drop selections for shifts that vanished or are now published.
      setSelected((prev) => {
        const stillDraft = new Set(
          shiftRows.filter((s) => !s.is_published).map((s) => s.id),
        );
        return new Set([...prev].filter((id) => stillDraft.has(id)));
      });
      setReloadKey((n) => n + 1);
    } catch (err) {
      handleError(err);
    } finally {
      setLoading(false);
    }
  }, [handleError]);

  useEffect(() => {
    let active = true;
    getSupabase()
      .auth.getSession()
      .then(({ data }) => {
        if (!active) return;
        if (!data.session) {
          router.replace("/login");
          return;
        }
        setChecking(false);
        void load();
      });
    return () => {
      active = false;
    };
  }, [router, load]);

  async function createShift(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      await apiPostJson("/api/rota/shifts/", {
        role,
        ward,
        professional: professional ? Number(professional) : null,
        start_time: toIso(startTime),
        end_time: toIso(endTime),
      });
      setRole("");
      setWard("");
      setProfessional("");
      setStartTime("");
      setEndTime("");
      setNotice("Draft shift created.");
      await load();
    } catch (err) {
      handleError(err);
    } finally {
      setCreating(false);
    }
  }

  async function publish() {
    if (selected.size === 0) return;
    setPublishing(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiPostJson<PublishResult>(
        "/api/rota/shifts/publish/",
        { shift_ids: [...selected] },
      );
      const bits = [`${result.published} shift(s) published`];
      if (result.notifications_queued) {
        // "queued", never "sent": the endpoint hands the job to django-q2 and
        // returns. Whether anything reaches a handset depends on the
        // professional having registered a device, which is only known later.
        bits.push(
          `${result.notifications_queued} notification(s) queued for assigned staff` +
            " (delivered only to those with a registered device)",
        );
      }
      if (result.unassigned_shifts) {
        bits.push(
          `${result.unassigned_shifts} had nobody assigned, so nobody was notified`,
        );
      }
      setNotice(`${bits.join(". ")}.`);
      setSelected(new Set());
      await load();
    } catch (err) {
      handleError(err);
    } finally {
      setPublishing(false);
    }
  }

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (checking) {
    return (
      <main>
        <p className="sub">Checking your session…</p>
      </main>
    );
  }

  const drafts = shifts.filter((s) => !s.is_published);
  const published = shifts.filter((s) => s.is_published);
  const allDraftsSelected =
    drafts.length > 0 && drafts.every((s) => selected.has(s.id));

  return (
    <>
      <NavBar />
      <main>
        <h1>Rota</h1>
        <p className="sub">
          Create draft shifts, then publish them. Publishing notifies every
          assigned professional who has registered a device.
        </p>

        {error && <div className="notice error">{error}</div>}
        {notice && <div className="notice ok">{notice}</div>}

        <h2>New shift</h2>
        <form onSubmit={createShift}>
          <div className="field">
            <label htmlFor="role">Role</label>
            <input
              id="role"
              type="text"
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="Night nurse"
              required
            />
            <p className="sub" style={{ margin: "0.3rem 0 0" }}>
              Only a professional designated for this exact role can accept a
              swap on it. Roles are set by PSL in the admin console.
            </p>
          </div>

          <div className="field">
            <label htmlFor="ward">Ward</label>
            <input
              id="ward"
              type="text"
              value={ward}
              onChange={(e) => setWard(e.target.value)}
              placeholder="Ward 4 (optional)"
            />
          </div>

          <div className="field">
            <label htmlFor="professional">Assign to</label>
            <select
              id="professional"
              value={professional}
              onChange={(e) => setProfessional(e.target.value)}
            >
              <option value="">Unassigned</option>
              {staff.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.full_name}
                  {s.role ? ` — ${s.role}` : " — no role set"} (
                  {s.verification_state.replace(/_/g, " ")})
                </option>
              ))}
            </select>
            {staff.length === 0 && (
              <p className="sub">
                No staff yet — import some on the Import staff page.
              </p>
            )}
            {staff.length > 0 && staff.some((s) => !s.role) && (
              <p className="sub">
                Some staff have no role set. They can still be assigned shifts,
                but cannot accept a swap from anyone until PSL sets one.
              </p>
            )}
          </div>

          <div className="field">
            <label htmlFor="start">Starts</label>
            <input
              id="start"
              type="datetime-local"
              value={startTime}
              onChange={(e) => setStartTime(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="end">Ends</label>
            <input
              id="end"
              type="datetime-local"
              value={endTime}
              onChange={(e) => setEndTime(e.target.value)}
              required
            />
          </div>

          <button type="submit" disabled={creating}>
            {creating ? "Creating…" : "Create draft shift"}
          </button>
        </form>

        <h2>Draft shifts</h2>
        {loading && <p className="sub">Loading…</p>}
        {!loading && drafts.length === 0 && (
          <p className="sub">No draft shifts. Create one above.</p>
        )}

        {drafts.length > 0 && (
          <>
            <table>
              <thead>
                <tr>
                  <th scope="col">
                    <input
                      type="checkbox"
                      aria-label="Select all draft shifts"
                      checked={allDraftsSelected}
                      onChange={() =>
                        setSelected(
                          allDraftsSelected
                            ? new Set()
                            : new Set(drafts.map((s) => s.id)),
                        )
                      }
                    />
                  </th>
                  <th scope="col">Role</th>
                  <th scope="col">Ward</th>
                  <th scope="col">Assigned to</th>
                  <th scope="col">Starts</th>
                  <th scope="col">Ends</th>
                </tr>
              </thead>
              <tbody>
                {drafts.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${s.role}`}
                        checked={selected.has(s.id)}
                        onChange={() => toggle(s.id)}
                      />
                    </td>
                    <td>{s.role}</td>
                    <td>{s.ward || <span className="sub">—</span>}</td>
                    <td>
                      {s.professional_name ?? (
                        <span className="sub">Unassigned</span>
                      )}
                    </td>
                    <td>{formatWhen(s.start_time)}</td>
                    <td>{formatWhen(s.end_time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="row">
              <button
                onClick={publish}
                disabled={publishing || selected.size === 0}
              >
                {publishing
                  ? "Publishing…"
                  : `Publish ${selected.size} selected shift(s)`}
              </button>
              <button className="secondary" onClick={load} disabled={loading}>
                Refresh
              </button>
            </div>
          </>
        )}

        <h2>Published</h2>
        {published.length === 0 ? (
          <p className="sub">Nothing published yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">Role</th>
                <th scope="col">Ward</th>
                <th scope="col">Assigned to</th>
                <th scope="col">Starts</th>
                <th scope="col">Reminder</th>
              </tr>
            </thead>
            <tbody>
              {published.map((s) => (
                <tr key={s.id}>
                  <td>{s.role}</td>
                  <td>{s.ward || <span className="sub">—</span>}</td>
                  <td>
                    {s.professional_name ?? (
                      <span className="sub">Unassigned</span>
                    )}
                  </td>
                  <td>{formatWhen(s.start_time)}</td>
                  <td>{s.reminder_sent ? "sent" : "not yet"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <SwapRequests reloadKey={reloadKey} onError={handleError} />
        <LeaveQueue
          reloadKey={reloadKey}
          onError={handleError}
          onNotice={setNotice}
        />
      </main>
    </>
  );
}
