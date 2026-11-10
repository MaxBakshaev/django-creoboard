"""Gunicorn settings. Everything tunable comes from the environment."""

import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")  # noqa: S104 - inside the container
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
accesslog = "-"
errorlog = "-"
# Behind nginx every request arrives from the proxy, so trust its forwarded headers only.
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "*")
