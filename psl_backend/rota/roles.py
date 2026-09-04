"""The role-matching rule for shift swaps.

Its own module because it is a patient-safety rule, not view plumbing: a
general nurse must not end up covering a shift that needs an ENT Registrar.
Keeping it separate means it can be tested directly, without a request.

Deliberately narrow. There is no hierarchy and no partial matching — "any
Registrar covers any Registrar shift" is a much larger design question and is
not implied here. Two roles match only if they are the same role.
"""


def normalise(value):
    return " ".join((value or "").split()).casefold()


def role_matches(profile_role, shift_role):
    """True when a professional designated `profile_role` may cover a shift
    needing `shift_role`.

    Comparison ignores case and collapses surrounding and repeated whitespace.
    The spec says "exact match", and this is that: what it rules out is
    *semantic* looseness — seniority tiers, specialties, "close enough" —
    not the difference between "ENT Registrar" and "ENT  registrar", which
    are the same role typed by two different people. Blocking a legitimate
    swap over a stray capital buys no safety and would be read as a bug.

    A blank role on either side never matches. Nobody has a designated role
    yet — the field is new and there is no backfill — so a professional who
    has not been given one cannot accept any swap until a facility sets it in
    Django Admin. Failing closed on missing data is the right way round: the
    alternative silently lets every unset profile cover anything, and blank
    would match blank if the comparison were left to string equality alone.
    """
    designated = normalise(profile_role)
    required = normalise(shift_role)
    if not designated or not required:
        return False
    return designated == required
