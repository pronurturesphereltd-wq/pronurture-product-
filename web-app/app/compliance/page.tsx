"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSupabase } from "@/lib/supabase";
import { ApiError, NotAuthenticatedError, apiGet, apiPostJson } from "@/lib/api";
import NavBar from "@/app/nav";

type Alert = {
  id: number;
  professional_name: string;
  professional_email: string;
  license_number: string;
  license_body: string;
  alert_type: string;
  alert_type_display: string;
  due_date: string;
  days_until_due: number;
  status: string;
  created_at: string;
  resolved_at: string | null;
};

function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * The number the facility actually needs: whether this licence has already
 * lapsed, and by how much. A negative count is the urgent case, so it reads as
 * "expired" rather than "-3 days".
 */
function describeDue(days: number): { text: string; tone: string } {
  if (days < 0) {
    const n = Math.abs(days);
    return { text: `Expired ${n} day${n === 1 ? "" : "s"} ago`, tone: "bad" };
  }
  if (days === 0) return { text: "Expires today", tone: "bad" };
  return { text: `In ${days} day${days === 1 ? "" : "s"}`, tone: "warn" };
}

export default function CompliancePage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [showResolved, setShowResolved] = useState(false);
  const [loading, setLoading] = useState(false);
  const [resolving, setResolving] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const handleError = useCallback(
    (err: unknown) => {
      if (err instanceof NotAuthenticatedError) {
        router.replace("/login");
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setError(
          `${err.message} An account must own an approved facility to see compliance alerts.`,
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
      const query = showResolved ? "?status=all" : "";
      setAlerts(await apiGet<Alert[]>(`/api/facilities/compliance-alerts/${query}`));
    } catch (err) {
      handleError(err);
    } finally {
      setLoading(false);
    }
  }, [handleError, showResolved]);

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

  async function resolve(alert: Alert) {
    setResolving(alert.id);
    setError(null);
    setNotice(null);
    try {
      await apiPostJson(
        `/api/facilities/compliance-alerts/${alert.id}/resolve/`,
        {},
      );
      setNotice(
        `Marked the ${alert.alert_type_display.toLowerCase()} alert for ` +
          `${alert.professional_name} as resolved.`,
      );
      await load();
    } catch (err) {
      handleError(err);
    } finally {
      setResolving(null);
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
    <>
      <NavBar />
      <main>
        <h1>Compliance</h1>
        <p className="sub">
          Licences on your roster expiring within 30 days, or already expired.
          Checked once a day.
        </p>

        {error && <div className="notice error">{error}</div>}
        {notice && <div className="notice ok">{notice}</div>}

        <div className="row">
          <button className="secondary" onClick={load} disabled={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
          <label className="inline">
            <input
              type="checkbox"
              checked={showResolved}
              onChange={(e) => setShowResolved(e.target.checked)}
            />
            Include resolved
          </label>
        </div>

        {!loading && alerts.length === 0 && (
          <div className="panel">
            <p style={{ margin: 0 }}>
              No {showResolved ? "" : "open "}alerts.
            </p>
            <p className="sub" style={{ margin: "0.4rem 0 0" }}>
              Alerts appear once PSL records a licence expiry date during
              verification. Nothing here does not mean nothing to check — it
              means no expiry dates are on file yet.
            </p>
          </div>
        )}

        {alerts.length > 0 && (
          <table>
            <thead>
              <tr>
                <th scope="col">Professional</th>
                <th scope="col">Licence</th>
                <th scope="col">Expires</th>
                <th scope="col">Status</th>
                <th scope="col"></th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((alert) => {
                const due = describeDue(alert.days_until_due);
                return (
                  <tr key={alert.id}>
                    <td>
                      {alert.professional_name}
                      <br />
                      <span className="sub">{alert.professional_email}</span>
                    </td>
                    <td>
                      {alert.license_number || <span className="sub">—</span>}
                      <br />
                      <span className="sub">{alert.license_body}</span>
                    </td>
                    <td>
                      {formatDate(alert.due_date)}
                      <br />
                      {alert.status === "open" ? (
                        <span className={`badge ${due.tone}`}>{due.text}</span>
                      ) : (
                        <span className="sub">{due.text}</span>
                      )}
                    </td>
                    <td>{alert.status}</td>
                    <td>
                      {alert.status === "open" && (
                        <button
                          className="secondary small"
                          onClick={() => resolve(alert)}
                          disabled={resolving === alert.id}
                        >
                          {resolving === alert.id ? "Resolving…" : "Resolve"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <details className="panel">
          <summary>What resolving does, and does not, do</summary>
          <p>
            Resolving clears the alert from this list. It does not renew the
            licence — if the expiry date on file is still inside the window, the
            next daily check raises the alert again. The alert stops coming back
            when PSL records a new expiry date during licence verification.
          </p>
        </details>
      </main>
    </>
  );
}
