from .base import *  # noqa: F403
from .base import env

DEBUG = False

# TLS is terminated by nginx (Elestio); the app sees plain HTTP plus X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
# Container healthchecks call the app over plain HTTP, so they must not be redirected.
SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/$"]

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 60 * 60 * 12

SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

DATABASES["default"]["CONN_MAX_AGE"] = 60  # noqa: F405

# Elestio nginx -> web nginx -> gunicorn: two proxies append to X-Forwarded-For.
# Confirm the real chain on the first deploy (step 2) and override via the environment.
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "NUM_PROXIES": env.int("DJANGO_NUM_PROXIES", default=2),
}

# HSTS preload is a public, hard-to-undo commitment and does not fit a shared
# service sub-domain, so this one deployment check is silenced on purpose.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
