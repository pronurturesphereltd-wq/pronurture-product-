"""Set a new password on any PSL login, facility or professional.

Keyed on the **login** email — the address on the Supabase Auth account —
rather than on anything stored against the PSL record. Those are not the same
field and in practice diverge: the sample facility's `contact_email` is
sample.home@example.com while its owner signs in as a personal address. Looking
up by the stored email would simply fail to find it.

So this resolves in the reliable direction: email -> auth account -> whichever
record carries that `supabase_user_id`.

    manage.py reset_login_password someone@example.com

Compare `provision_professional_account`, which creates a professional's
account and is keyed on Profile.email. This one only resets, and covers both
kinds of account.

The password is shown once and stored nowhere.
"""

from django.core.management.base import BaseCommand, CommandError

from core.supabase_admin import (
    SupabaseAdminError,
    find_user_by_email,
    is_configured,
    set_user_password,
)
from facilities.models import Facility
from profiles.models import Profile
from profiles.management.commands.provision_professional_account import (
    generate_password,
)


def resolve_record(user_id):
    """Which PSL record does this auth account belong to?

    Returns (kind, record), or (None, None) when nothing claims it — a real
    state, not an error: someone can sign up through Supabase and never
    register a facility or profile.
    """
    facility = Facility.objects.filter(supabase_user_id=user_id).first()
    if facility is not None:
        return "facility", facility

    profile = Profile.objects.filter(supabase_user_id=user_id).first()
    if profile is not None:
        return "professional", profile

    return None, None


class Command(BaseCommand):
    help = "Set a new password on a PSL login, facility or professional."

    def add_arguments(self, parser):
        parser.add_argument("email", help="The address the account signs in with.")
        parser.add_argument(
            "--password", help="Use this password instead of a generated one."
        )

    def handle(self, *args, **options):
        if not is_configured():
            raise CommandError(
                "SUPABASE_SECRET_KEY is not set, so no password can be changed."
            )

        email = options["email"].strip()
        try:
            user_id = find_user_by_email(email)
        except SupabaseAdminError as exc:
            raise CommandError(f"Could not reach Supabase Auth: {exc}")

        if not user_id:
            raise CommandError(
                f"No Supabase Auth account signs in as {email}. Nothing was "
                "changed. To create an account for a professional, use "
                "provision_professional_account."
            )

        kind, record = resolve_record(user_id)

        password = options["password"] or generate_password()
        generated = not options["password"]

        try:
            set_user_password(user_id, password)
        except SupabaseAdminError as exc:
            raise CommandError(f"Could not set the password: {exc}")

        self.stdout.write(self.style.SUCCESS(f"Reset the password for {email}"))
        self.stdout.write(f"  supabase_user_id: {user_id}")
        if kind == "facility":
            self.stdout.write(f"  facility:         {record.name} ({record.status})")
            if record.status != Facility.Status.APPROVED:
                self.stdout.write(
                    self.style.WARNING(
                        "  This facility is not approved, so the app will "
                        "refuse it every endpoint until PSL approves it."
                    )
                )
        elif kind == "professional":
            self.stdout.write(
                f"  professional:     {record.full_name} "
                f"(role: {record.role or 'not set'})"
            )
        else:
            # Worth saying plainly: the password now works, but the account
            # will be refused everywhere until a record claims it.
            self.stdout.write(
                self.style.WARNING(
                    "  No facility or profile is linked to this account, so it "
                    "can sign in but every endpoint will refuse it."
                )
            )

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("  password: " + password))
        self.stdout.write(
            "  Shown once and stored nowhere. "
            + ("Generated — copy it now." if generated else "Yours — not saved here.")
        )
