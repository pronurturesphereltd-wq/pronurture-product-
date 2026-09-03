# PSL Backend — Project Context for Claude Code

## What this is
Phase 0 of PronurtureSphere Ltd (PSL): a Django backend proving the platform's core loop — facility approval and professional licence verification — with a full audit trail. Nothing beyond that scope belongs in this phase (no rota, no jobs, no courses, no payments).

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
├── facilities/            # models.py, admin.py, serializers.py, views.py
├── profiles/               # models.py, admin.py, serializers.py, views.py
└── core/                    # Supabase JWT authentication class, shared helpers
```

## Data model
**Facility:** name, registration_number, contact_email (unique), supabase_user_id (UUID, unique, nullable), status (pending/approved/rejected/suspended), approved_at, approved_by (FK to Django User), created_at. Include `history = HistoricalRecords()`.

**Profile:** full_name, email (unique), phone, license_number, license_body, supabase_user_id (UUID, unique, nullable), facility (FK, nullable), verification_state (pending/self_registered_unverified/verified/rejected), onboarding_path (bulk_import/invite_link), verified_at, verified_by (FK to Django User), created_at, updated_at. Include `history = HistoricalRecords()`.

## Admin console (this IS the internal UI — no separate frontend)
- `FacilityAdmin`: list_display on name/contact_email/status/created_at, list_filter on status, bulk actions `approve_facilities` / `reject_facilities` that set status + approved_at + approved_by (from `request.user`).
- `ProfileAdmin`: list_display on full_name/email/verification_state/onboarding_path/created_at, list_filter on verification_state and onboarding_path, bulk actions `verify_profiles` / `reject_profiles` that set verification_state + verified_at + verified_by.

## RBAC
Two Django Groups: `admin` (full permissions on both Facility and Profile) and `verification_officer` (permissions on Profile only — no Facility access at all). Set this up as a data migration, not a manual admin-UI step, so it's reproducible.

## Public API (facility/professional-facing, authenticated via Supabase JWT)
- `POST /api/facilities/register/` — creates Facility with status=pending, stores supabase_user_id from the verified token
- `POST /api/profiles/seed-bulk/` — stub bulk import, accepts a JSON array, creates Profile rows with onboarding_path=bulk_import, verification_state=pending
- `POST /api/profiles/self-register/` — creates Profile with verification_state=self_registered_unverified, supabase_user_id from verified token

Build a custom DRF `authentication.BaseAuthentication` subclass in `core/` that reads the `Authorization: Bearer <token>` header, verifies it against Supabase's JWT secret via `pyjwt`, and attaches the decoded `supabase_user_id` to the request. Every endpoint above uses this, not Django session auth.

## Definition of done
1. Admin can log in to Django Admin.
2. A facility can register via the public API and appears in the Django Admin facility list with status=pending.
3. An admin can bulk-approve/reject a facility from Django Admin, and the change appears in that model's history (via `django-simple-history`).
4. A profile can be created via the seed-bulk or self-register endpoint.
5. An admin (or verification_officer) can verify/reject a profile from Django Admin, recorded in history.
6. Logging in as a `verification_officer` user shows no Facility section in the Admin nav at all; logging in as `admin` shows both.

Stop here. Do not build rota, jobs, courses, payments, or anything past this list, even if it seems like a natural next step.
