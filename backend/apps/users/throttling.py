import hashlib

from django.conf import settings
from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView


class LoginIPThrottle(SimpleRateThrottle):
    """Limits login attempts per client IP.

    The rate is read from Django settings on every request (not at import time).
    Counters live in the per-process cache, so with N gunicorn workers the effective
    limit is up to N times the nominal one; nginx `limit_req` gives the strict limit.
    """

    scope = "login_ip"

    def get_rate(self) -> str:
        return settings.LOGIN_THROTTLE_IP

    def get_cache_key(self, request: Request, view: APIView) -> str:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class LoginUsernameThrottle(SimpleRateThrottle):
    """Limits login attempts per submitted username, to slow down distributed guessing.

    The key is a hash of the normalised username, so arbitrary user input never ends up
    in a cache key. A side effect: an attacker can briefly lock out a known username.
    """

    scope = "login_user"

    def get_rate(self) -> str:
        return settings.LOGIN_THROTTLE_USER

    def get_cache_key(self, request: Request, view: APIView) -> str | None:
        username = request.data.get("username") if hasattr(request.data, "get") else None
        if not isinstance(username, str) or not username.strip():
            return None  # invalid payload: the serializer answers 400, nothing to count
        digest = hashlib.sha256(username.strip().lower().encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}
