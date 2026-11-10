"""Base settings shared by all environments. Secrets come from the environment only."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
REPO_DIR = BASE_DIR.parent

env = environ.Env()
# Real environment variables take precedence over the values from .env.
environ.Env.read_env(REPO_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "django_filters",
    "apps.users",
    "apps.tasks",
    "apps.ai_usage",
    "apps.analytics",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
# Fail fast instead of hanging: on Windows "localhost" may resolve to an unreachable ::1 first,
# and libpq then falls back to 127.0.0.1 only after this timeout.
DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = 5
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "users.User"
# Argon2 is the only hasher for new passwords; PBKDF2 stays second so that hashes
# created elsewhere (for example by a data import) can still be verified and upgraded.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# The SPA reads the csrftoken cookie and sends it back in X-CSRFToken, so it must stay readable.
CSRF_COOKIE_HTTPONLY = False

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Uploaded files. There is deliberately no URL route for MEDIA_URL: files are served only by
# the authorized download endpoints, and nginx must not serve this directory either.
MEDIA_URL = "media/"
MEDIA_ROOT = env.path("DJANGO_MEDIA_ROOT", default=str(BASE_DIR / "media"))
FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750

# Upload limits. There are three layers: nginx `client_max_body_size` (must be at least
# MAX_UPLOAD_MB + UPLOAD_OVERHEAD_BYTES), the Content-Length check before the body is parsed
# and the size check after parsing (see apps/tasks/uploads.py).
# Both Django settings below do NOT limit the size of a file: DATA_UPLOAD_MAX_MEMORY_SIZE
# counts only the non-file part of a request (JSON, form fields); FILE_UPLOAD_MAX_MEMORY_SIZE
# is the threshold above which an uploaded file goes to a temporary file instead of memory.
# The values are read at request time, so a test may lower the limit.
MAX_UPLOAD_MB = env.int("MAX_UPLOAD_MB", default=100)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
UPLOAD_OVERHEAD_BYTES = 1024 * 1024  # multipart boundaries and the other form fields
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
DATA_UPLOAD_MAX_MEMORY_SIZE = 1_048_576

# Login throttling. Counters are kept in the per-process cache, so with several gunicorn
# workers the real limit is up to (workers x nominal); the strict limit is nginx limit_req.
LOGIN_THROTTLE_IP = env("LOGIN_THROTTLE_IP", default="5/min")
LOGIN_THROTTLE_USER = env("LOGIN_THROTTLE_USER", default="5/min")
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.users.authentication.SessionAuthentication401"],
    # Number of reverse proxies in front of the app: DRF takes the client IP for throttling
    # from X-Forwarded-For only when this is set (otherwise a client could spoof the header).
    "NUM_PROXIES": env.int("DJANGO_NUM_PROXIES", default=0),
    # Secure by default: every endpoint requires login unless it opts out explicitly.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Lists are paginated: ?page=N&page_size=M (up to 100).
    "DEFAULT_PAGINATION_CLASS": "apps.tasks.pagination.TaskPagination",
    "PAGE_SIZE": 20,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "CreoBoard API",
    "DESCRIPTION": "API of the creative department task board.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Task.status, TaskEvent.from_status and to_status share one set of choices.
    "ENUM_NAME_OVERRIDES": {"TaskStatusEnum": "apps.tasks.models.TaskStatus"},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
