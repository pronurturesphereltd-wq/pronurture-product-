"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPostJson } from "@/lib/api";

type LeaveApplication = {
  id: number;
  professional_name: string;
  professional_email: string;
  start_date: string;
  end_date: string;
  days: number;
  reason: string;
  status: string;
  created_at: string;
  decided_at: string | null;
};

function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}

/**
 * The facility's leave approval queue.
 *
 * Unlike the swaps panel this one does act: approve and decline are the
 * facility's decision to make. A decision is final — the API refuses a second
 * one with a 409 rather than letting an approve quietly overwrite a decline —
 * so the buttons disappear once a row is settled.
 */
export default function LeaveQueue({
  reloadKey,
  onError,
  onNotice,
}: {
  reloadKey: number;
  onError: (err: unknown) => void;
  onNotice: (message: string) => void;
}) {
  const [applications, setApplications] = useState<LeaveApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [deciding, setDeciding] = useState<number | null>(null);
  // Bumped by a decision, so the queue refetches without the effect needing a
  // callback it would then have to declare as a dependency.
  const [afterDecision, setAfterDecision] = useState(0);

  // Every setState lands after the await, never in the synchronous body of the
  // effect — eslint rejects a synchronous write as a cascading render. `active`
  // drops a response whose trigger has already been superseded.
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const rows = await apiGet<LeaveApplication[]>("/api/leave/applications/");
        if (active) setApplications(rows);
      } catch (err) {
        if (active) onError(err);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [onError, reloadKey, afterDecision]);

  async function decide(
    application: LeaveApplication,
    decision: "approve" | "decline",
  ) {
    setDeciding(application.id);
    try {
      await apiPostJson(
        `/api/leave/applications/${application.id}/${decision}/`,
        {},
      );
      onNotice(
        `Leave for ${application.professional_name} ` +
          `${decision === "approve" ? "approved" : "declined"}.`,
      );
      setAfterDecision((n) => n + 1);
    } catch (err) {
      onError(err);
    } finally {
      setDeciding(null);
    }
  }

  const pending = applications.filter((a) => a.status === "submitted");
  const decided = applications.filter((a) => a.status !== "submitted");

  return (
    <>
      <h2>Leave requests</h2>
      {loading && <p className="sub">Loading…</p>}

      {!loading && applications.length === 0 && (
        <p className="sub">Nobody has applied for leave.</p>
      )}

      {pending.length > 0 && (
        <table>
          <thead>
            <tr>
              <th scope="col">Professional</th>
              <th scope="col">Dates</th>
              <th scope="col">Reason</th>
              <th scope="col"></th>
            </tr>
          </thead>
          <tbody>
            {pending.map((application) => (
              <tr key={application.id}>
                <td>
                  {application.professional_name}
                  <br />
                  <span className="sub">{application.professional_email}</span>
                </td>
                <td>
                  {formatDate(application.start_date)} –{" "}
                  {formatDate(application.end_date)}
                  <br />
                  <span className="sub">
                    {application.days} day{application.days === 1 ? "" : "s"}
                  </span>
                </td>
                <td>
                  {application.reason || <span className="sub">No reason given</span>}
                </td>
                <td>
                  <div className="row">
                    <button
                      className="small"
                      onClick={() => decide(application, "approve")}
                      disabled={deciding === application.id}
                    >
                      Approve
                    </button>
                    <button
                      className="secondary small"
                      onClick={() => decide(application, "decline")}
                      disabled={deciding === application.id}
                    >
                      Decline
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {decided.length > 0 && (
        <>
          <h3>Decided</h3>
          <table>
            <thead>
              <tr>
                <th scope="col">Professional</th>
                <th scope="col">Dates</th>
                <th scope="col">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {decided.map((application) => (
                <tr key={application.id}>
                  <td>{application.professional_name}</td>
                  <td>
                    {formatDate(application.start_date)} –{" "}
                    {formatDate(application.end_date)}
                  </td>
                  <td>
                    <span
                      className={`badge ${
                        application.status === "approved" ? "ok" : "muted"
                      }`}
                    >
                      {application.status}
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
