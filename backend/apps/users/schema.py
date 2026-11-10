from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class SessionAuthentication401Scheme(OpenApiAuthenticationExtension):
    """Describes our session authentication subclasses in the OpenAPI schema."""

    target_class = "apps.users.authentication.SessionAuthentication401"
    match_subclasses = True
    name = "cookieAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "cookie", "name": settings.SESSION_COOKIE_NAME}
