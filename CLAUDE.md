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

Added by the role-guardrail addendum: **Profile** gains `role` (operational designation, facility-controlled, gates swap acceptance) and **Shift** gains `ward` (informational only, gates nothing).

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
- **Step 6 (facility isolation): done, and it was not just a review pass.** All ten Phase 1B endpoints now have isolation tests against every hostile actor — foreign facility, foreign professional, wrong role, unapproved facility. The audit found a real information leak and a second, subtler instance of it; see below. 234 tests passing.
- **Role guardrail on swaps: built and tested** (PSL_Phase1B_Role_Guardrail_Addendum.md). See its own section below.
- **Step 7 remains:** the end-to-end run against live infrastructure.

## The web app serves two audiences, and knows which
`GET /api/me/` returns `kind: "facility" | "professional"` plus the matching record. Every page wraps itself in `RequireKind` (`app/guard.tsx`), which renders the page only for the audience it was built for and otherwise points the caller at their own home. `NavBar` shows no links until identity is known, because guessing is what caused the original bug.

**Why it exists:** a Supabase token proves identity, not role. Before this, `/rota` rendered facility controls to anyone holding a token, so a professional signing in got the shift-creation form, a page-level 403 from the two facility-only requests, and approve/decline buttons that would have 403'd too. Widening the swap and leave lists to serve both audiences is what turned a clean failure into a half-working page.

Pages: `/rota`, `/import`, `/compliance` are facility-only. `/me` is professional-only — assigned shifts, offer for swap, withdraw, accept a colleague's offer, apply for leave, leave status. `/` routes to the right home; `/login` sends everyone there rather than assuming `/rota`.

`GET /api/rota/shifts/` now serves both. A professional sees only shifts assigned to them **and only once published** — a draft is the facility thinking aloud, and showing it would make every unpublished edit look like a change to someone's week. `POST` stays facility-only and needs its own guard inside the view, since the permission class admits both; without it a professional reaching `request.facility` is a 500 where a 403 belongs.

Note this is a testing convenience as much as a product decision. The Phase 1B spec routes the professional experience to "the eventual mobile app" (§3), and `/me` is deliberately minimal — enough to drive the swap and leave loops through a UI rather than curl.

## A shift that has started cannot be swapped, at either end
Opening a swap has always refused a started shift. Two gaps around that, both found from one bug report:

1. `/me` rendered "Offer for swap" on **every** assigned shift, including ones already finished. The row looked actionable and the API refused it — the page contradicting itself. Upcoming and past shifts are now separate tables and only upcoming ones carry a button.
2. **Accepting** never checked `start_time` at all. An offer opened in good time sits pending indefinitely — nothing expires it — so a shift could be claimed hours after it began, reassigning it retroactively and taking the original assignee off a shift they may have worked. Acceptance now refuses with 400, and the UI hides stale offers.

The general rule: when the API refuses an action under some condition, the UI must not offer it under that condition. Both halves need the check — the UI so the control is absent, the API because the UI is not a security boundary.

Related React detail: **do not read the clock during render.** `Date.now()` in a component body is impure, eslint rejects it, and it drifts between renders for no reason the data reflects. `/me` captures "now" when data is fetched and compares against that.

## Swap offers are targeted only
Every offer names one colleague. There are no open "whoever is qualified can grab it" offers.

`target_professional` stays **nullable in the database** — settled offers predating the rule were created without one, and rewriting history to invent a target would be a lie about what happened. Required is enforced in the serializer instead. Those legacy rows are visible only to whoever made them and can never be accepted, which is the right end state.

`GET /api/rota/shifts/<id>/eligible-colleagues/` gives the frontend its list: same facility, role matching the shift, requester excluded. It is professional-safe, unlike `/api/facilities/staff/`, and only the shift's assignee may ask. Matching happens in Python via `role_matches`, not an `iexact` filter, so the list cannot disagree with what acceptance will allow.

**The empty case is a real state**, not an error: nobody else at the facility shares the role. The UI says so rather than showing a dropdown with nothing in it.

Creating an offer re-validates the target's facility, role and not-self **even though the picker only offers valid choices**. The API is the boundary; the UI is not.

Acceptance folds `target_professional` into the atomic claim itself rather than checking before it, so there is no window between checking and claiming. A colleague who was not named gets the same 404 as a request that does not exist — they learn nothing about whether it is there.

**The concurrency proof changed shape.** Eight different people can no longer contend for one offer, so the race that matters is the target arriving twice at once — a double-tap, two devices, a retry. Bystanders are raced alongside them carrying the same target filter, and update zero rows, which is what proves the rule lives inside the claim.

## Role guardrail on shift swaps
A patient-safety rule, so it is enforced server-side: a professional cannot accept a swap for a role they are not designated for. `Profile.role` is the operational designation ("ENT Registrar", "A&E Nurse") — facility-controlled information, distinct from the licence fields PSL verifies, and set in Django Admin like `license_expiry_date`. `Shift.ward` exists now too and is **purely informational; it never gates anything**.

The check lives in `SwapRequestAcceptView` and runs **before** the atomic claim. That ordering is the requirement, not an implementation detail: the claim is a one-way door, so a mismatched attempt has to bounce while the request is still `pending` and still available to someone actually designated for the role. It answers **400** — a validation failure, deliberately not reusing the enumeration-safe 403/404 pattern, because the caller can already see the request and is entitled to know why they cannot take it.

**A blank role blocks every swap, on purpose.** The field is new and nothing backfills it. Failing closed on missing data beats a silent bypass — and note that blank would match blank if the comparison were left to plain string equality, which is exactly the bug this avoids.

Comparison ignores case and collapses whitespace (`rota/roles.py`). This is a deliberate reading of the spec's "exact match": what "exact" rules out is *semantic* looseness — no hierarchy, no specialty matching, no "any Registrar covers any Registrar" — not the difference between `ENT Registrar` and `ENT  registrar`, which are the same job typed twice. There are tests pinning both halves of that.

Scope limits, each with a test so they are not widened by accident: initial shift assignment is **not** gated (a facility can still roster anyone onto anything), cancelling is not gated, and ward is not gated.

**Set `Profile.role` before testing swaps.** A blank role refuses every acceptance — correctly, but it looks like a bug if you have not read this. Amaka and Bola are both set to `A&E Nurse` and can swap the live A&E Nurse shift between them; Chidi is still blank and has no Supabase account.

## Cross-tenant lookups must be scoped, not checked afterwards
Found during the step 6 audit, fixed across every Phase 1B endpoint. Two variants of the same leak:

1. `POST /api/rota/shifts/<id>/swap-request/` looked the shift up unscoped, then answered **403** "you are not assigned to that shift" for a real id and **404** for a missing one. Any Supabase account could walk shift ids and learn which existed across every facility on the platform.
2. The accept, cancel, approve, decline and resolve endpoints all answered 404 either way — but with a *different message*: a custom "No such swap request." versus Django's "No ShiftSwapRequest matches the given query." Same oracle, one layer down. This is the one that gets missed, because the status code looks right.

Both are fixed by putting the tenant filter inside `get_object_or_404` rather than in an `if` after it, so a foreign row and an absent row are indistinguishable by construction. `SwapRequestAcceptView` folds its targeted-request privacy rule into the same lookup with a `Q` object.

**The pattern to keep:** never `get_object_or_404(Model, pk=pk)` followed by a facility check. Pass the scope as a filter argument. Each endpoint has a test asserting the foreign-id and missing-id responses are byte-identical — status *and* body, because asserting only the status would have passed against the leaky version.

A 403 is still correct for intra-facility rules (cancelling someone else's request, a colleague deciding leave) — the caller can already see those rows, so there is nothing to hide.
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

## Provisioning an account without an email
`manage.py provision_professional_account <email> --role "A&E Nurse"` creates a Supabase Auth account for a Profile that already exists and links it, sending no email. Use it for seeding test professionals rather than the invite link, which goes through the rate-limited default SMTP.

It calls `POST /auth/v1/admin/users` with `email_confirm: true` — without that flag the account exists but is stuck behind a confirmation link that was never sent, so nobody can sign in. The password is generated, printed once, and stored nowhere; there is no way to read it back, only to reset it in the Supabase dashboard. If the auth account already exists (an earlier attempt half-succeeded) the command adopts it and says so rather than demanding manual cleanup — and does not claim to have set a password it did not set.

It refuses to invent a Profile: an orphan auth account with nothing pointing at it is the worse outcome.

`--reset-password` handles a different case: the profile is already linked, but the account cannot sign in. An invited professional who never opened the email has **no password and an unconfirmed address** — the invite creates the user, and the emailed link is where both would have been settled. Both failures report as `invalid_credentials`, so the account looks healthy from the outside and the profile looks correctly linked. The flag sets a password *and* confirms the email; setting only the password leaves sign-in failing for the other reason, which reads as a wrong password.

## The pooler ceiling is 15 sessions — keep CONN_MAX_AGE at 0
`DATABASE_URL` points at Supabase's session-mode pooler, capped at `pool_size: 15`. Exceeding it gives `OperationalError: FATAL: (EMAXCONNSESSION) max clients reached in session mode` — on ordinary requests, with nothing else wrong with them.

**`CONN_MAX_AGE` is 0 by default and should stay there while the session pooler is in front of the database.** It used to be 600, which is the right instinct for a normal Postgres and wrong here: `runserver` handles each request on its own thread, so every concurrent request takes a connection and then holds it for ten minutes. A few page loads — the app fires three to five requests each — exhausted the pool and the API began failing intermittently, which reads as flakiness rather than as a resource limit. Override with `DJANGO_CONN_MAX_AGE` only behind a deployment that has its own pool and does not go through the session pooler.

Stale processes are the other half. The qcluster's ORM broker polls constantly so its connections never idle out, and a forgotten `runserver` holds its own. `Get-CimInstance Win32_Process -Filter "Name like '%python%'"` shows start times; a `runserver` from yesterday is the usual culprit. Killing one frees its connections, but Supavisor takes a moment to reap closed sessions, so an immediate retry can still fail.

Killing a `runserver`'s **parent** orphans the child, which then cannot auto-reload and will serve stale code until it dies. Restart it properly rather than killing half the pair.

This is the same pooler that strands `test_postgres` after a Postgres test run, and another reason to point `PSL_TEST_ON_POSTGRES` at a local instance.

## Resetting a login password
`manage.py reset_login_password <email>` sets a new password on any PSL login, facility or professional, and reports which record it belongs to.

It is keyed on the **login** email — the address on the Supabase Auth account — and resolves email → auth account → whichever record carries that `supabase_user_id`. That indirection is not decoration: the sample facility's `contact_email` is `sample.home@example.com` while its owner signs in with a personal address, so any lookup keyed on the PSL record's own email finds nothing.

`provision_professional_account --reset-password` still exists and is keyed on `Profile.email`; use it for professionals, and this one when the record is a facility or the two addresses differ.

## Caught IntegrityError needs its own savepoint
Third occurrence in this project, so it is written down. `except IntegrityError` around a `save()` or `create()` **must** wrap it in a nested `transaction.atomic()`. Without the savepoint the failed statement poisons the surrounding transaction and every later query raises `TransactionManagementError` instead of the error you meant to return — the handler runs, but the recovery path inside it cannot touch the database. Bit `ShiftSwapRequestCreateView` (a 409 that never rendered), the compliance sweep (a collision that would have killed the run), and `provision_professional_account`.

## Known follow-up: production email volume
Bulk import currently sends invite emails through Supabase's default SMTP, which is rate-limited and not intended for production volume. **This is no longer theoretical:** a 3-row import provisioned 1 account and got HTTP 429 on the other 2, which the per-row report surfaced correctly (the profiles were still created, with `supabase_user_id` left null). Three rows was enough to hit the limit. Before any facility imports real staff, configure a dedicated SMTP provider (e.g. Resend, per the original tech stack doc) in Supabase's Auth settings. Worth adding a retry for 429 specifically, so throttled rows are re-attempted rather than needing a re-import.

## Known gap: push delivery needs a registered device
Publishing queues a notification per assigned shift; it does not mean one arrived. `send_push_notification` returns `{"reason": "no-devices"}` when the professional has no `PushDevice` row, and nothing surfaces that back to the facility. Imported staff have no device until they install a client and call `POST /api/devices/register/`, so in practice most publishes currently notify nobody. FCM delivery itself is verified working (Phase 1A criterion 4, with a real browser token). What is missing is a client for professionals to register from, and a way for the facility to see that a shift's assignee is unreachable.
