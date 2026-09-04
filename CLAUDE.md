# PSL Backend — Project Context for Claude Code

## What this is
The PronurtureSphere Ltd (PSL) Django backend. Phase 0 proved the platform's core loop — facility approval and professional licence verification — with a full audit trail. Phase 1A adds workforce management: bulk import, invite-link onboarding, and the rota. See "Current status" below for what is built; do not build past it.

## Stack (do not substitute without asking)
- Django + Django REST Framework
- PostgreSQL via Supabase (connection string in `.env` as `DATABASE_URL`)
- `django-simple-history` for audit trail (via `HistoricalRecords()` on models — do not hand-write audit logging)
- Django's built-in auth + Django Admin for PSL staff (internal only)
- Supabase Auth for professional/facility-facing identity — Django never issues these users tokens, it only verifies Supabase-issued JWTs
- `drf-spectacular` for OpenAPI schema generation
- `pyjwt` for verifying Supabase JWTs

## Folder structure
```
psl_backend/
├── manage.py
├── psl_backend/          # settings.py, urls.py
├── facilities/            # models, admin, serializers, views, importing.py, tasks.py
├── profiles/               # models.py, admin.py, serializers.py, views.py
├── rota/                    # Shift model, publish endpoint, django-q2 tasks
└── core/                     # Supabase JWT auth, permissions, push, history helpers
```

## Data model
**Facility:** name, registration_number, contact_email (unique), supabase_user_id (UUID, unique, nullable), status (pending/approved/rejected/suspended), approved_at, approved_by (FK to Django User), created_at. Include `history = HistoricalRecords()`.

**Profile:** full_name, email (unique), phone, license_number, license_body, supabase_user_id (UUID, unique, nullable), facility (FK, nullable), verification_state (pending/self_registered_unverified/verified/rejected), onboarding_path (bulk_import/invite_link), verified_at, verified_by (FK to Django User), created_at, updated_at. Include `history = HistoricalRecords()`.

Added in Phase 1A:

**InviteLink** (facilities): facility FK, unguessable UUID `token`, created_by, expires_at. **PushDevice** (profiles): profile FK, unique fcm_token, device_type. **Shift** (rota): facility FK, nullable professional FK, role, start/end_time, is_published, published_at, plus `reminder_sent`/`reminder_sent_at` as the idempotency guard for the reminder sweep.

`HistoricalRecords` is passed `get_user=core.history.get_history_user`. Public API callers are Supabase identities with no Django User row, and simple_history's default raises ValueError on them.

## Admin console (this IS the internal UI — no separate frontend)
- `FacilityAdmin`: list_display on name/contact_email/status/created_at, list_filter on status, bulk actions `approve_facilities` / `reject_facilities` that set status + approved_at + approved_by (from `request.user`).
- `ProfileAdmin`: list_display on full_name/email/verification_state/onboarding_path/created_at, list_filter on verification_state and onboarding_path, bulk actions `verify_profiles` / `reject_profiles` that set verification_state + verified_at + verified_by.

## RBAC
Two Django Groups: `admin` (full permissions on both Facility and Profile) and `verification_officer` (permissions on Profile only — no Facility access at all). Set this up as a data migration, not a manual admin-UI step, so it's reproducible.

## Public API (facility/professional-facing, authenticated via Supabase JWT)
- `POST /api/facilities/register/` — creates Facility with status=pending, stores supabase_user_id from the verified token
- `POST /api/profiles/seed-bulk/` — stub bulk import, accepts a JSON array, creates Profile rows with onboarding_path=bulk_import, verification_state=pending
- `POST /api/profiles/self-register/` — creates Profile with verification_state=self_registered_unverified, supabase_user_id from verified token

Added in Phase 1A:
- `POST /api/facilities/bulk-import/` — CSV/Excel upload, queued to django-q2; returns 202 immediately
- `POST /api/facilities/invite-links/` — generates an InviteLink for the calling facility
- `POST /api/profiles/register-via-invite/<token>/` — public; the token is the authorisation
- `GET|POST /api/rota/shifts/` — list/create draft shifts
- `POST /api/rota/shifts/publish/` — publishes and queues a push per assigned professional
- `POST /api/devices/register/` — upserts a professional's FCM token

`core/authentication.py` is a DRF `BaseAuthentication` subclass reading `Authorization: Bearer <token>`. It verifies **asymmetrically (ES256) against the project's public JWKS endpoint via `PyJWKClient`** — there is no shared JWT secret, and accepted algorithms are pinned to reject `alg:none` and HS256 confusion attacks. Facility/professional endpoints then resolve the identity to a record via `core/permissions.py`, which attaches `request.facility` / `request.profile`. Never Django session auth.

## Definition of done — Phase 0 (all met)
1. Admin can log in to Django Admin.
2. A facility can register via the public API and appears in the Django Admin facility list with status=pending.
3. An admin can bulk-approve/reject a facility from Django Admin, and the change appears in that model's history (via `django-simple-history`).
4. A profile can be created via the seed-bulk or self-register endpoint.
5. An admin (or verification_officer) can verify/reject a profile from Django Admin, recorded in history.
6. Logging in as a `verification_officer` user shows no Facility section in the Admin nav at all; logging in as `admin` shows both.

## Current status (update this section as phases complete)
- **Phase 0:** Complete. Facility approval, licence verification, RBAC, audit trail — committed at `1c7fba7`.
- **Phase 1A backend (steps 1–5): complete.** Bulk import, invite-link onboarding, rota builder + publish, push notifications, shift reminders. 124 tests passing. Done-criteria 1–5 verified against live infrastructure, not just tests:
  1. CSV import returns 202 immediately; rows are created by a django-q2 worker.
  2. A real Supabase Auth account was provisioned and the invite email dispatched (`confirmation_sent_at` set) using the new-format `SUPABASE_SECRET_KEY`.
  3. Invite-link self-registration lands in `self_registered_unverified`, the existing Phase 0 queue.
  4. Publishing a shift delivered a real FCM push to a registered device (`sent: 1`) via `qcluster`.
  5. The reminder sweep fired exactly once across three consecutive scheduled runs.
- **Criterion 6: verified.** The minimal Next.js facility app (`web-app/`) drives the whole loop through its UI. Confirmed by running it, not by the build passing: a 3-row CSV imported through `/import` created 3 pending profiles, and 2 shifts created and published through `/rota` — one assigned, one deliberately unassigned and correctly reported as notifying nobody.
- **Phase 1A is complete.** All six done-criteria verified against live infrastructure. Next work is Phase 1B, and it is not started.
- **Running it:** the web server alone is not enough — `manage.py qcluster` must run for imports, pushes and reminders. Register the reminder schedule once with `manage.py setup_shift_reminders`.
- **After any system clock correction**, re-run `manage.py setup_shift_reminders`. `Schedule.next_run` is an absolute timestamp and does not self-heal: a clock that was running fast leaves the sweep stalled for the size of the correction, silently and with nothing logged.
- **Phase 1B: in progress.** Steps 1–2 of PSL_Phase1B_Spec.md Section 7 are built: the `ShiftSwapRequest` model, `LeaveApplication` and `ComplianceAlert` models, `Profile.license_expiry_date`, and the four swap endpoints with the atomic-accept concurrency test. Steps 3–7 (leave endpoints, compliance task, frontend, isolation tests, end-to-end run) follow.
- **Not yet built:** the rest of Phase 1B, Phase 2 (Jobs), Phase 3 (Academy), Phase 4 (Analytics).

Do not start work on a phase beyond what's marked "in progress" or listed as current focus in a prompt, even if it seems like a natural next step. Ask before expanding scope.

## Running the tests
`python manage.py test` runs on a local SQLite file (`test-db.sqlite3`, gitignored), not `:memory:` — Django's in-memory SQLite uses shared-cache mode, which raises `SQLITE_LOCKED` on contention, and the busy timeout does not retry that error. It made the swap concurrency test fail about one run in three.

SQLite still serialises writers, so it cannot demonstrate row-level concurrency at all. The genuine race is only proven on Postgres, via `PSL_TEST_ON_POSTGRES=1`. `rota/test_swaps.py::test_database_backend_under_test` records `connection.vendor` so a green SQLite run is never mistaken for the stronger guarantee.

**Point `PSL_TEST_ON_POSTGRES` at a local Postgres, not at Supabase.** Running it against `DATABASE_URL` means `CREATE DATABASE test_postgres` on the production project, and teardown then fails — Supavisor, the connection pooler, keeps an idle session attached to the test database, and `DROP DATABASE` refuses while any session exists. That has now stranded a `test_postgres` database twice, each needing a manual `pg_terminate_backend` + `DROP` in the SQL editor. A local instance has no pooler in front of it, drops cleanly, and is far faster:

```
set PSL_TEST_ON_POSTGRES=1
set DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
python manage.py test rota.test_swaps
```

## Resolved: legacy JWT secret rotation
Was deferred pending Supabase Auth wiring (see git history for the original note). Resolved: bulk-import account provisioning uses `SUPABASE_SECRET_KEY` (the new `sb_secret_...` format, sent on the `apikey` header) rather than the legacy `service_role`/JWT-based key. Nothing in the backend depends on the legacy JWT secret. It can be revoked in the Supabase dashboard (JWT Signing Keys tab) whenever convenient — no code impact.

## Known follow-up: production email volume
Bulk import currently sends invite emails through Supabase's default SMTP, which is rate-limited and not intended for production volume. **This is no longer theoretical:** a 3-row import provisioned 1 account and got HTTP 429 on the other 2, which the per-row report surfaced correctly (the profiles were still created, with `supabase_user_id` left null). Three rows was enough to hit the limit. Before any facility imports real staff, configure a dedicated SMTP provider (e.g. Resend, per the original tech stack doc) in Supabase's Auth settings. Worth adding a retry for 429 specifically, so throttled rows are re-attempted rather than needing a re-import.

## Known gap: push delivery needs a registered device
Publishing queues a notification per assigned shift; it does not mean one arrived. `send_push_notification` returns `{"reason": "no-devices"}` when the professional has no `PushDevice` row, and nothing surfaces that back to the facility. Imported staff have no device until they install a client and call `POST /api/devices/register/`, so in practice most publishes currently notify nobody. FCM delivery itself is verified working (Phase 1A criterion 4, with a real browser token). What is missing is a client for professionals to register from, and a way for the facility to see that a shift's assignee is unreachable.
