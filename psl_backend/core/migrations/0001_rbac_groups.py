"""Create the two PSL staff groups and their permissions.

Done as a data migration rather than by hand in the admin UI so the RBAC setup
is reproducible on every environment.

- `admin`                — full permissions on Facility and Profile.
- `verification_officer` — permissions on Profile only. Granting nothing on
  Facility is what hides the Facilities section from the admin nav entirely.
"""

from functools import reduce
from operator import or_

from django.apps import apps as global_apps
from django.db import migrations, models

ADMIN_GROUP = "admin"
VERIFICATION_OFFICER_GROUP = "verification_officer"

# Each group gets add/change/delete/view on these (app_label, model_name) pairs.
GROUP_MODELS = {
    ADMIN_GROUP: [("facilities", "facility"), ("profiles", "profile")],
    VERIFICATION_OFFICER_GROUP: [("profiles", "profile")],
}


def ensure_permissions_exist(apps, using):
    """Permissions are normally created by a post_migrate signal, which does not
    fire until the whole migrate run finishes. On a fresh database this
    migration would otherwise find nothing to assign, so create them now.

    `create_permissions` re-resolves the app config against the registry it is
    handed, so the real config satisfies its `models_module` guard while the
    historical registry supplies the models as they exist at this point.
    """
    from django.contrib.auth.management import create_permissions

    for app_label in ("facilities", "profiles"):
        create_permissions(
            global_apps.get_app_config(app_label),
            apps=apps,
            using=using,
            verbosity=0,
            interactive=False,
        )


def create_groups(apps, schema_editor):
    using = schema_editor.connection.alias
    ensure_permissions_exist(apps, using)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for group_name, model_pairs in GROUP_MODELS.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        pair_filter = reduce(
            or_,
            (
                models.Q(content_type__app_label=app_label, content_type__model=model)
                for app_label, model in model_pairs
            ),
        )
        group.permissions.set(Permission.objects.using(using).filter(pair_filter))


def delete_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.using(schema_editor.connection.alias).filter(
        name__in=list(GROUP_MODELS)
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("facilities", "0001_initial"),
        ("profiles", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_groups, delete_groups),
    ]
