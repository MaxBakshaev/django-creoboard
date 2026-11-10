import pytest
from django.conf import settings
from django.core.management import call_command
from rest_framework.test import APIClient


def test_time_settings_are_utc_and_aware():
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "UTC"


def test_drf_requires_authentication_by_default():
    permissions = settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]

    assert permissions == ["rest_framework.permissions.IsAuthenticated"]


def test_custom_user_model_is_configured():
    assert settings.AUTH_USER_MODEL == "users.User"


@pytest.mark.parametrize("path", ["/api/v1/schema/", "/api/v1/docs/"])
def test_schema_and_docs_are_not_exposed_without_debug(path):
    assert settings.DEBUG is False

    assert APIClient().get(path).status_code == 404


def test_openapi_schema_builds_without_warnings(tmp_path):
    call_command(
        "spectacular", "--validate", "--fail-on-warn", "--file", str(tmp_path / "schema.yml")
    )

    assert (tmp_path / "schema.yml").read_text(encoding="utf-8").startswith("openapi:")


def test_upload_limits_are_consistent():
    assert settings.MAX_UPLOAD_BYTES == settings.MAX_UPLOAD_MB * 1024 * 1024
    assert settings.MAX_UPLOAD_MB > 0
    # Large files must go to a temporary file on disk, never be held in memory whole.
    assert settings.FILE_UPLOAD_MAX_MEMORY_SIZE < settings.MAX_UPLOAD_BYTES
    # Non-file request bodies (JSON, form fields) stay small.
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE <= 1024 * 1024


def test_uploaded_files_are_stored_outside_static_and_group_readable_only():
    media = str(settings.MEDIA_ROOT)

    assert media != str(settings.STATIC_ROOT)
    assert not media.startswith(str(settings.STATIC_ROOT))
    assert settings.FILE_UPLOAD_PERMISSIONS == 0o640


@pytest.mark.parametrize(
    "path", ["/media/attachments/x.png", "/media/", "/static/attachments/x.png"]
)
def test_media_is_not_served_directly(path):
    assert APIClient().get(path).status_code == 404
