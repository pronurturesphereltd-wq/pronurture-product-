"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  NotAuthenticatedError,
  apiGet,
  apiPostForm,
} from "@/lib/api";
import RequireKind from "@/app/guard";

type ImportError = { row: number | null; error: string };

type ImportReport = {
  filename?: string;
  total_rows?: number;
  created: number;
  skipped_existing: number;
  failed: number;
  accounts_provisioned: number;
  accounts_configured: boolean;
  errors: ImportError[];
};

type StatusResponse = {
  status: "pending" | "success" | "failed";
  result?: ImportReport | string;
};

const POLL_MS = 1500;
const POLL_TIMEOUT_MS = 120_000;

export default function ImportPage() {
  return <RequireKind kind="facility">{() => <StaffImport />}</RequireKind>;
}

function StaffImport() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploading" | "waiting" | "done">(
    "idle",
  );
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const pollUntilDone = useCallback(async (taskId: string) => {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      const status = await apiGet<StatusResponse>(
        `/api/facilities/bulk-import/${encodeURIComponent(taskId)}/`,
      );
      if (status.status === "pending") continue;
      if (status.status === "failed") {
        throw new Error(
          typeof status.result === "string"
            ? status.result
            : "The import job failed. Check the worker log.",
        );
      }
      return status.result as ImportReport;
    }
    throw new Error(
      "The import is taking longer than expected. It may still be running — " +
        "check the Profiles list in Django Admin.",
    );
  }, []);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;

    setPhase("uploading");
    setError(null);
    setReport(null);

    try {
      const form = new FormData();
      form.append("file", file);
      const accepted = await apiPostForm<{ task_id: string }>(
        "/api/facilities/bulk-import/",
        form,
      );

      setPhase("waiting");
      const finished = await pollUntilDone(accepted.task_id);
      setReport(finished);
      setPhase("done");
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    } catch (err) {
      if (err instanceof NotAuthenticatedError) {
        router.replace("/login");
        return;
      }
      setError(
        err instanceof ApiError || err instanceof Error
          ? err.message
          : String(err),
      );
      setPhase("idle");
    }
  }

  const busy = phase === "uploading" || phase === "waiting";

  return (
    <>
      <h1>Import staff</h1>
        <p className="sub">
          Upload a CSV or Excel file. Each person becomes a pending profile for
          PSL to verify, and receives a login email.
        </p>

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="file">Staff file</label>
            <input
              id="file"
              ref={fileInput}
              type="file"
              accept=".csv,.xlsx,.xlsm"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={busy}
            />
          </div>

          <details className="panel">
            <summary>Required columns</summary>
            <p>
              <code>full_name</code>, <code>email</code>,{" "}
              <code>license_number</code>, <code>license_body</code>. Optional:{" "}
              <code>phone</code>. Common spreadsheet headings such as
              &ldquo;Full Name&rdquo; or &ldquo;Licence Number&rdquo; are
              recognised too.
            </p>
          </details>

          <div className="row">
            <button type="submit" disabled={!file || busy}>
              {phase === "uploading"
                ? "Uploading…"
                : phase === "waiting"
                  ? "Importing…"
                  : "Start import"}
            </button>
            {file && !busy && <span className="sub">{file.name}</span>}
          </div>
        </form>

        {phase === "waiting" && (
          <div className="notice">
            Upload accepted. The import runs in the background — waiting for the
            per-row report…
          </div>
        )}

        {error && <div className="notice error">{error}</div>}

        {report && <Report report={report} />}
    </>
  );
}

function Report({ report }: { report: ImportReport }) {
  const clean = report.failed === 0;
  return (
    <>
      <div className={`notice ${clean ? "ok" : "error"}`}>
        {clean
          ? `Import finished. ${report.created} profile(s) created.`
          : `Import finished with ${report.failed} problem row(s).`}
      </div>

      <table>
        <tbody>
          <tr>
            <th scope="row">Rows in file</th>
            <td>{report.total_rows ?? "—"}</td>
          </tr>
          <tr>
            <th scope="row">Created</th>
            <td>{report.created}</td>
          </tr>
          <tr>
            <th scope="row">Skipped (already on file)</th>
            <td>{report.skipped_existing}</td>
          </tr>
          <tr>
            <th scope="row">Failed</th>
            <td>{report.failed}</td>
          </tr>
          <tr>
            <th scope="row">Login accounts created</th>
            <td>
              {report.accounts_provisioned}
              {!report.accounts_configured && (
                <span className="sub">
                  {" "}
                  — account provisioning is not configured, so no login emails
                  were sent
                </span>
              )}
            </td>
          </tr>
        </tbody>
      </table>

      {report.errors?.length > 0 && (
        <>
          <h2>Rows that need attention</h2>
          <table>
            <thead>
              <tr>
                <th scope="col">Row</th>
                <th scope="col">Problem</th>
              </tr>
            </thead>
            <tbody>
              {report.errors.map((e, i) => (
                <tr key={i}>
                  <td>{e.row ?? "—"}</td>
                  <td>{e.error}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="sub">
            Row numbers match the spreadsheet, counting the header as row 1.
          </p>
        </>
      )}
    </>
  );
}
