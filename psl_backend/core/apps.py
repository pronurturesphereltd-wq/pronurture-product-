from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Importing registers the OpenAPI security scheme for the Supabase
        # bearer authenticator. Registration happens on import, so this has to
        # be pulled in somewhere that always runs.
        from . import schema  # noqa: F401
