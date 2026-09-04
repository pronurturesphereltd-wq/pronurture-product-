"""Give an existing Profile a Supabase Auth account, without sending an email.

The normal onboarding path is the invite link, which emails the professional.
That path stays. This one exists because Supabase's default SMTP is rate
limited — a three-row import provisioned one account and got HTTP 429 on the
other two — and because seeding a test professional does not need an email
round trip at all.

The account is created already confirmed, so the person can sign in with the
printed password straight away.

    manage.py provision_professional_account nurse2@example.com --role "A&E Nurse"

The password is shown once and stored nowhere. Do not paste it into a file
that git can see; there is no way to read it back afterwards, only to reset it
in the Supabase dashboard.
"""

import secrets
import string

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from core.supabase_admin import (
    SupabaseAdminError,
    create_user,
    find_user_by_email,
    is_configured,
)
from profiles.models import Profile

# Ambiguous glyphs left out: a password that gets read off a screen and
# retyped should not turn on telling l from 1 or O from 0.
ALPHABET = (
    "".join(c for c in string.ascii_letters + string.digits if c not in "lI1O0")
    + "!@#%^*-_=+"
)


def generate_password(length=20):
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


class Command(BaseCommand):
    help = "Create a Supabase Auth account for an existing Profile, no email sent."

    def add_arguments(self, parser):
        parser.add_argument("email", help="The Profile's email address.")
        parser.add_argument(
            "--password",
            help="Use this password instead of a generated one.",
        )
        parser.add_argument(
            "--role",
            help=(
                "Also set Profile.role. Blank blocks every shift swap, so a "
                "professional who will accept one needs this."
            ),
        )

    def handle(self, *args, **options):
        if not is_configured():
            raise CommandError(
                "SUPABASE_SECRET_KEY is not set, so no account can be created."
            )

        email = options["email"].strip()
        try:
            profile = Profile.objects.get(email__iexact=email)
        except Profile.DoesNotExist:
            raise CommandError(
                f"No profile with email {email}. This command links an account "
                "to a profile that already exists; it does not create one."
            )

        if profile.supabase_user_id:
            raise CommandError(
                f"{profile.full_name} already has supabase_user_id "
                f"{profile.supabase_user_id}. Clear it first if you mean to "
                "re-link."
            )

        password = options["password"] or generate_password()
        generated = not options["password"]

        try:
            user_id = create_user(
                profile.email,
                password,
                user_metadata={"full_name": profile.full_name},
            )
        except SupabaseAdminError as exc:
            # An address already registered in Supabase but not yet linked here
            # is the ordinary case when a previous attempt half-succeeded.
            # Adopt the existing account rather than demanding manual cleanup.
            self.stdout.write(self.style.WARNING(f"Create failed: {exc}"))
            self.stdout.write("Looking for an existing account with that email…")
            user_id = find_user_by_email(profile.email)
            if not user_id:
                raise CommandError(
                    "Could not create the account, and no existing account has "
                    "that email. Nothing was changed."
                )
            self.stdout.write(
                self.style.WARNING(
                    f"Found existing account {user_id}. Linking it. The password "
                    "below was NOT applied — the existing one still stands."
                )
            )
            password = None

        profile.supabase_user_id = user_id
        if options["role"]:
            profile.role = options["role"]
        try:
            # Its own atomic block so a unique-constraint violation rolls back
            # a savepoint instead of poisoning the surrounding transaction —
            # without it every later query fails with TransactionManagementError
            # rather than reaching the CommandError below.
            with transaction.atomic():
                # save(), not update(): the link and the role change both
                # belong in the profile's history.
                profile.save()
        except IntegrityError:
            raise CommandError(
                f"Supabase user {user_id} is already linked to another profile. "
                "The auth account exists but this profile was not changed."
            )

        self.stdout.write(
            self.style.SUCCESS(f"Linked {profile.full_name} <{profile.email}>")
        )
        self.stdout.write(f"  supabase_user_id: {user_id}")
        self.stdout.write(f"  role:             {profile.role or '(blank)'}")
        if password:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("  password: " + password))
            self.stdout.write(
                "  Shown once and stored nowhere. "
                + (
                    "Generated — copy it now."
                    if generated
                    else "Yours — it was not saved here."
                )
            )
        if not profile.role:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "  No role set. This profile cannot accept any shift swap "
                    "until one is set, by design. Pass --role or set it in "
                    "Django Admin."
                )
            )
