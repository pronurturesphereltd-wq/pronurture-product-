"""OpenAPI description of the Supabase bearer scheme.

Without this, drf-spectacular cannot introspect the custom authentication class
and silently omits the security scheme, leaving the published docs claiming the
endpoints are unauthenticated.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class SupabaseJWTScheme(OpenApiAuthenticationExtension):
    target_class = "core.authentication.SupabaseJWTAuthentication"
    name = "SupabaseJWT"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "Supabase-issued access token. Signed with the project's "
                "asymmetric signing key and verified against its public JWKS "
                "endpoint. Send as: Authorization: Bearer <access_token>"
            ),
        }
