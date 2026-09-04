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
├── rota/                    # Shift + ShiftSwapRequest, publish endpoint, django-q2 tasks
├── leave/                   # LeaveApplication, submission + facility approval queue
├── compliance/          # ComplianceAlert, the daily sweep, facility-facing endpoints
└── core/                     # Supabase JWT auth, permissions, push, history helpers
```

## Data model
**Facility:** name, registration_number, contact_email (unique), supabase_user_id (UUID, unique, nullable), status (pending/approved/rejected/suspended), approved_at, approved_by (FK to Django User), created_at. Include `history = HistoricalRecords()`.

**Profile:** full_name, email (unique), phone, license_number, license_body, supabase_user_id (UUID, unique, nullable), facility (FK, nullable), verification_state (pending/self_registered_unverified/verified/rejected), onboarding_path (bulk_import/invite_link), verified_at, verified_by (FK to Django User), created_at, updated_at. Include `history = HistoricalRecords()`.

Added in Phase 1A:

**InviteLink** (facilities): facility FK, unguessable UUID `token`, created_by, expires_at. **PushDevice** (profiles): profile FK, unique fcm_token, device_type. **Shift** (rota): facility FK, nullable professional FK, role, start/end_time, is_published, published_at, plus `reminder_sent`/`reminder_sent_at` as the idempotency guard for the reminder sweep.

Added in Phase 1B:

**ShiftSwapRequest** (rota): shift FK, requesting/target/accepted_by Profile FKs, status, decided_at — with a partial `UniqueConstraint` on `shift` where `status='pending'`, so one shift can carry only one open offer. **LeaveApplication** (leave): professional FK, start/end_date, reason, status, decided_at, and a `CheckConstraint` that end_date is not before start_date. **ComplianceAlert** (compliance): profile FK, alert_type, due_date, status, resolved_at, with a partial `UniqueConstraint` on (profile, alert_type) where `status='open'` — the enforced half of the sweep's idempotency guard. **Profile** gains `license_expiry_date`.

ComplianceAlert is the one new model with no `HistoricalRecords`: it is derived state the sweep regenerates from the profile's licence data, and the licence changes that drive it are already in the Profile history.

`HistoricalRecords` is passed `get_user=core.history.get_history_user`. Public API callers are Supabase identities with no Django User row, and simple_history's default raises ValueError on them.

## Admin console (this IS the internal UI — no separate frontend)
- `FacilityAdmin`: list_display on name/contact_email/status/created_at, list_filter on status, bulk actions `approve_facilities` / `reject_facilities` that set status + approved_at + approved_by (from `request.user`).
- `ProfileAdmin`: list_display on full_name/email/verification_state/onboarding_path/created_at, list_filter on verification_state and onboarding_path, bulk actions `verify_profiles` / `reject_profiles` that set verification_state + verified_at + verified_by.

## RBAC
Two Django Groups: `admin` (full permissions on every model, including the historical tables — an audit trail nobody can read is not much of one) and `verification_officer` (permissions on Profile only — no access to anything else at all). Set up as data migrations, not manual admin-UI steps, so it's reproducible: `core/0001_rbac_groups` creates the groups, `core/0002_rbac_phase1_models` extends `admin` to everything added after Phase 0. Note `0001` uses `.set()` and `0002` uses `.add()` — a future migration that widens a group must add, or it silently revokes what came before.

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

Added in Phase 1B:
- `POST /api/rota/shifts/<id>/swap-request/` — the assignee offers their own shift, optionally to one named colleague
- `GET /api/rota/swap-requests/` — read by either side. A professional sees open offers plus their own; a facility sees every swap on its own shifts
- `POST /api/rota/swap-requests/<id>/accept/` — the atomic claim; the loser of a race gets 409
- `POST /api/rota/swap-requests/<id>/cancel/` — the requester withdraws
- `GET|POST /api/leave/applications/` — one endpoint, two audiences: a facility reads its approval queue, a professional reads their own applications. `IsFacilityOrProfessional` attaches whichever record the caller owns and the view branches on which one arrived
- `POST /api/leave/applications/<id>/approve|decline/` — facility only, scoped to its own roster
- `GET /api/facilities/compliance-alerts/` — open alerts by default; `?status=all` for the history
- `POST /api/facilities/compliance-alerts/<id>/resolve/`

`core/authentication.py` is a DRF `BaseAuthentication` subclass reading `Authorization: Bearer <token>`. It verifies **asymmetrically (ES256) against the project's public JWKS endpoint via `PyJWKClient`** — there is no shared JWT secret, and accepted algorithms are pinned to reject `alg:none` and HS256 confusion attacks. Facility/professional endpoints then resolve the identity to a record via `core/permissions.py`, which attaches `request.facility` / `request.profile`. Never Django session auth.

**Bodyless POST endpoints need `@extend_schema(request=None, ...)`.** Without it drf-spectacular cannot guess a request body, and silently drops the endpoint from the schema — it disappears from `/api/docs/` while the route still works. `manage.py spectacular --validate` reports these as errors; it should stay at zero.

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
- **Running it:** the web server alone is not enough — `manage.py qcluster` must run for imports, pushes and reminders. Register the two schedules once: `manage.py setup_shift_reminders` and `manage.py setup_compliance_checks` (add `--run-now` to sweep immediately instead of waiting for the cluster).
- **After any system clock correction**, re-run both setup commands. `Schedule.next_run` is an absolute timestamp and does not self-heal: a clock that was running fast leaves the sweep stalled for the size of the correction, silently and with nothing logged. This applies to every schedule created from now on, not just the one it first bit.
- **Phase 1B: in progress.** Steps 1–5 of PSL_Phase1B_Spec.md Section 7 are built. 218 tests passing, `next build` and `eslint` clean. Swap requests with the atomic accept and its concurrency proof; leave applications with the facility approval queue; the daily compliance sweep; and the front end — a `/compliance` page plus swap and leave sections on `/rota`, keeping the app at four pages. **Nothing here is verified against live infrastructure yet** — that is step 7, and it has not run. A passing build is not a working page; Phase 1A's criterion 6 made that distinction the hard way.
- **Steps 6–7 remain:** facility-isolation tests across all three models (step 6 — written alongside each endpoint rather than deferred, so this is a review pass, not new work) and the end-to-end run (step 7).
- **`GET /api/rota/swap-requests/` was widened to serve facilities too** (step 5). It was professional-only, which made done-criterion 3 — a facility seeing accepted swaps in the rota view — impossible to satisfy. A facility sees every swap on its own shifts, including ones targeted at a named colleague; the peer-privacy filter narrows for professionals only, since the facility owns the shift and is not a peer. It still cannot accept or cancel: visibility, not a gate, and there are tests asserting both refusals.
- **Licence expiry is PSL's data, not the facility's.** `license_expiry_date` is set in Django Admin during licence verification, alongside `verification_state`. It is deliberately not a bulk-import column: a facility uploading its own staff's licence expiry dates would be self-certifying the thing PSL exists to verify. This does mean the compliance sweep finds nothing until PSL staff start recording expiry dates.
- **The `admin` group now spans every model** (core migration `0002_rbac_phase1_models`). It previously covered only Facility and Profile, so everything added in Phase 1A — shifts, invite links, push devices — was reachable by superusers alone. `verification_officer` is unchanged and still Profile-only; that narrowness is what hides the other sections from its admin nav.
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
