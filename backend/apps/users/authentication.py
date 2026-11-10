from rest_framework.authentication import SessionAuthentication
from rest_framework.request import Request


class SessionAuthentication401(SessionAuthentication):
    """Session auth that answers 401 (not 403) to anonymous requests.

    DRF turns NotAuthenticated into 403 when the first authenticator has no
    `authenticate_header`. The API contract (docs/ARCHITECTURE.md, section 9) needs 401.
    """

    def authenticate_header(self, request: Request) -> str:
        return "Session"


class CsrfProtectedAnonymousAuthentication(SessionAuthentication401):
    """For the login endpoint: no user is authenticated, but CSRF is still enforced.

    DRF checks CSRF only for already authenticated users, so without this the login form
    would be open to login CSRF. It runs before permissions and throttles, hence requests
    without a valid token are rejected with 403 before any credential is looked at.
    """

    def authenticate(self, request: Request) -> None:
        self.enforce_csrf(request)
        return None
