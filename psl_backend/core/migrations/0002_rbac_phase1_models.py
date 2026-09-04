"""Extend the `admin` group to the models added in Phase 1A and 1B.

0001 granted permissions on Facility and Profile, the only two models that
existed. Everything added since — invite links, push devices, shifts, swap
requests, leave applications, compliance alerts — was registered in Django
Admin but reachable by superusers only, because no group had permissions on it.
That is a gap, not a design: the `admin` group is documented as PSL's
full-access staff group.

`verification_officer` is deliberately left untouched. Its whole point is that
granting nothing outside Profile is what hides the other sections from the
admin nav, and that is still the intended boundary.

Uses `.add()` rather than `.set()` so it extends 0001's grant instead of
replacing it.
"""

from functools import reduce
from operator import or_

from django.apps import apps as global_apps
from django.db import migrations, models

ADMIN_GROUP = "admin"

# Historical models included: an audit trail nobody with admin rights can read
# is not much of an audit trail.
NEW_ADMIN_MODELS = [
    ("facilities", "invitelink"),
    ("facilities", "historicalinvitelink"),
    ("profiles", "pushdevice"),
    ("rota", "shift"),
    ("rota", "historicalshift"),
    ("rota", "shiftswaprequest"),
    ("rota", "historicalshiftswaprequest"),
    ("leave", "leaveapplication"),
    ("leave", "historicalleaveapplication"),
    ("compliance", "compliancealert"),
]

APP_LABELS = ("facilities", "profiles", "rota", "leave", "compliance")


def ensure_permissions_exist(apps, using):
    """Same reason as 0001: the post_migrate signal that normally creates
    permissions has not fired yet, so on a fresh database there would be
    nothing here to grant."""
    from django.contrib.auth.management import create_permissions

    for app_label in APP_LABELS:
        create_permissions(
            global_apps.get_app_config(app_label),
            apps=apps,
            using=using,
            verbosity=0,
            interactive=False,
        )


def grant(apps, schema_editor):
    using = schema_editor.connection.alias
    ensure_permissions_exist(apps, using)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group, _ = Group.objects.using(using).get_or_create(name=ADMIN_GROUP)
    pair_filter = reduce(
        or_,
        (
            models.Q(content_type__app_label=app_label, content_type__model=model)
            for app_label, model in NEW_ADMIN_MODELS
        ),
    )
    group.permissions.add(*Permission.objects.using(using).filter(pair_filter))


def revoke(apps, schema_editor):
    using = schema_editor.connection.alias
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group = Group.objects.using(using).filter(name=ADMIN_GROUP).first()
    if group is None:
        return
    pair_filter = reduce(
        or_,
        (
            models.Q(content_type__app_label=app_label, content_type__model=model)
            for app_label, model in NEW_ADMIN_MODELS
        ),
    )
    group.permissions.remove(*Permission.objects.using(using).filter(pair_filter))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_rbac_groups"),
        ("facilities", "0002_historicalinvitelink_invitelink"),
        ("profiles", "0003_historicalprofile_license_expiry_date_and_more"),
        ("rota", "0002_historicalshiftswaprequest_shiftswaprequest"),
        ("leave", "0001_initial"),
        ("compliance", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
