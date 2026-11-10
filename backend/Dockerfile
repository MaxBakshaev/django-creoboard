# syntax=docker/dockerfile:1
# Build context: backend/

FROM python:3.12.16-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt


FROM python:3.12.16-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . .

# collectstatic needs a settings module to import; these values exist only for the build
# and are not used at runtime.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret \
    DATABASE_URL=postgres://build:build@localhost:5432/build \
    python manage.py collectstatic --noinput \
    && mkdir -p /app/media \
    && chown -R app:app /app/staticfiles /app/media

USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--config", "gunicorn.conf.py"]
