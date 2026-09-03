"""How django-simple-history decides who made a change.

`history_user` is a foreign key to the Django User table, which only PSL staff
occupy. Public API callers authenticate with a Supabase JWT and are represented
by a `SupabaseUser` that has no such row, so simple_history's default of
handing over `request.user` raises ValueError on every API write.

Recording None for those changes is the honest answer: no staff member made
them. The acting Supabase identity is already persisted on the row itself as
`supabase_user_id`, so the audit trail loses nothing.
"""

from django.contrib.auth import get_user_model


def get_history_user(request, **kwargs):
    user = getattr(request, "user", None)
    if user is None:
        return None
    if isinstance(user, get_user_model()) and user.is_authenticated:
        return user
    return None
