import logging

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.users.models import Role
from tests.factories import DEFAULT_PASSWORD, UserFactory

pytestmark = pytest.mark.django_db

# Literal paths: calling reverse() at import time would load the URLconf during collection,
# before pytest-django switches DEBUG off, and the DEBUG-only schema routes would be added.
CSRF_URL = "/api/v1/auth/csrf/"
LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"
ME_URL = "/api/v1/auth/me/"
WRONG_CREDENTIALS = {"non_field_errors": ["Неверный логин или пароль."]}


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("auth-csrf", CSRF_URL),
        ("auth-login", LOGIN_URL),
        ("auth-logout", LOGOUT_URL),
        ("auth-me", ME_URL),
    ],
)
def test_url_names_resolve(name, path):
    assert reverse(name) == path


def fetch_csrf_token(client) -> str:
    response = client.get(CSRF_URL)
    assert response.status_code == 204
    return response.cookies["csrftoken"].value


def login(client, username, password=DEFAULT_PASSWORD, *, token=None, **extra):
    """POST /auth/login/ the way the SPA does: with the csrftoken cookie echoed in a header."""
    if token is None:
        token = fetch_csrf_token(client)
    return client.post(
        LOGIN_URL,
        {"username": username, "password": password},
        format="json",
        HTTP_X_CSRFTOKEN=token,
        **extra,
    )


def current_token(client) -> str:
    return client.cookies["csrftoken"].value


# ---------- csrf ----------


def test_csrf_endpoint_is_public_and_sets_a_readable_cookie(api_client):
    response = api_client.get(CSRF_URL)

    assert response.status_code == 204
    cookie = response.cookies["csrftoken"]
    assert cookie.value
    assert not cookie["httponly"]  # the SPA has to read it
    assert cookie["samesite"] == "Lax"


def test_csrf_endpoint_rejects_unsafe_methods(api_client):
    assert api_client.post(CSRF_URL).status_code == 405


# ---------- login ----------


@pytest.mark.parametrize("role", Role.values)
def test_login_succeeds_and_returns_the_user(api_client, role):
    user = UserFactory(role=role, first_name="Anna", last_name="Petrova")

    response = login(api_client, user.username)

    assert response.status_code == 200
    assert response.json() == {
        "id": user.pk,
        "username": user.username,
        "first_name": "Anna",
        "last_name": "Petrova",
        "role": role,
    }


def test_login_sets_an_httponly_session_cookie_and_rotates_csrf_token(api_client):
    user = UserFactory()
    token = fetch_csrf_token(api_client)

    response = login(api_client, user.username, token=token)

    session_cookie = response.cookies["sessionid"]
    assert session_cookie["httponly"]
    assert session_cookie["samesite"] == "Lax"
    assert response.cookies["csrftoken"].value != token


def test_login_does_not_keep_a_preexisting_session_key(api_client):
    """Session fixation: a key planted before login must not survive it."""
    user = UserFactory()
    session = api_client.session
    session["planted"] = "yes"
    session.save()
    planted_key = session.session_key

    login(api_client, user.username)

    assert api_client.session.session_key != planted_key


def test_login_does_not_return_the_password_or_hash(api_client):
    user = UserFactory()

    body = login(api_client, user.username).content.decode()

    assert DEFAULT_PASSWORD not in body
    assert user.password not in body
    assert "password" not in body


def test_login_updates_last_login(api_client):
    user = UserFactory()
    assert user.last_login is None

    login(api_client, user.username)

    user.refresh_from_db()
    assert user.last_login is not None


def test_user_without_role_can_log_in_but_has_empty_role(api_client):
    get_user_model().objects.create_superuser(username="owner", password=DEFAULT_PASSWORD)

    response = login(api_client, "owner")

    assert response.status_code == 200
    assert response.json()["role"] == ""


def test_wrong_password_is_rejected_without_a_session(api_client):
    user = UserFactory()

    response = login(api_client, user.username, "wrong-password-123")

    assert response.status_code == 400
    assert response.json() == WRONG_CREDENTIALS
    assert "sessionid" not in response.cookies
    assert api_client.get(ME_URL).status_code == 401


def test_unknown_inactive_and_wrong_password_look_the_same(api_client):
    active = UserFactory()
    inactive = UserFactory(is_active=False)

    wrong_password = login(api_client, active.username, "wrong-password-123")
    unknown_user = login(api_client, "nobody")
    inactive_user = login(api_client, inactive.username)  # correct password, but disabled

    for response in (wrong_password, unknown_user, inactive_user):
        assert response.status_code == 400
        assert response.json() == WRONG_CREDENTIALS
        assert "sessionid" not in response.cookies


def test_user_deactivated_after_login_loses_access(api_client):
    user = UserFactory()
    login(api_client, user.username)
    assert api_client.get(ME_URL).status_code == 200

    user.is_active = False
    user.save(update_fields=["is_active"])

    assert api_client.get(ME_URL).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [{}, {"username": "x"}, {"password": "x"}, {"username": "", "password": ""}],
    ids=["empty", "no-password", "no-username", "blank"],
)
def test_login_requires_username_and_password(api_client, payload):
    token = fetch_csrf_token(api_client)

    response = api_client.post(LOGIN_URL, payload, format="json", HTTP_X_CSRFTOKEN=token)

    assert response.status_code == 400


@pytest.mark.parametrize("payload", [["a", "b"], "text", 42, None], ids=repr)
def test_login_with_a_malformed_body_is_a_400_not_a_500(api_client, payload):
    token = fetch_csrf_token(api_client)

    response = api_client.post(LOGIN_URL, payload, format="json", HTTP_X_CSRFTOKEN=token)

    assert response.status_code == 400


def test_login_rejects_an_oversized_password(api_client):
    user = UserFactory()

    response = login(api_client, user.username, "a" * 129)

    assert response.status_code == 400
    assert "password" in response.json()


def test_login_rejects_get(api_client):
    assert api_client.get(LOGIN_URL).status_code == 405


def test_login_logs_no_credentials(api_client, caplog):
    user = UserFactory(username="secret-login-name")
    caplog.set_level(logging.DEBUG)

    login(api_client, user.username, "wrong-password-123")
    login(api_client, user.username)

    assert "wrong-password-123" not in caplog.text
    assert DEFAULT_PASSWORD not in caplog.text
    assert "secret-login-name" not in caplog.text


# ---------- csrf protection ----------


def test_login_without_csrf_token_is_forbidden(csrf_client):
    user = UserFactory()

    response = csrf_client.post(
        LOGIN_URL, {"username": user.username, "password": DEFAULT_PASSWORD}, format="json"
    )

    assert response.status_code == 403
    assert "sessionid" not in response.cookies


def test_login_with_a_wrong_csrf_token_is_forbidden(csrf_client):
    user = UserFactory()
    fetch_csrf_token(csrf_client)

    response = login(csrf_client, user.username, token="not-the-token")

    assert response.status_code == 403
    assert csrf_client.get(ME_URL).status_code == 401


def test_login_with_a_valid_csrf_token_works_for_a_strict_client(csrf_client):
    user = UserFactory()

    assert login(csrf_client, user.username).status_code == 200
    assert csrf_client.get(ME_URL).status_code == 200


def test_csrf_failure_is_not_counted_by_the_throttle(csrf_client, settings):
    settings.LOGIN_THROTTLE_IP = "2/min"
    user = UserFactory()

    for _ in range(5):
        assert csrf_client.post(LOGIN_URL, {}, format="json").status_code == 403

    assert login(csrf_client, user.username).status_code == 200


# ---------- me ----------


def test_me_requires_authentication_and_answers_401(api_client):
    response = api_client.get(ME_URL)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Session"


def test_me_returns_the_current_user(api_client):
    user = UserFactory(creative=True)
    login(api_client, user.username)

    response = api_client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()["username"] == user.username
    assert response.json()["role"] == Role.CREATIVE


def test_me_exposes_no_sensitive_fields(api_client):
    user = UserFactory(lead=True)
    login(api_client, user.username)

    assert set(api_client.get(ME_URL).json()) == {
        "id",
        "username",
        "first_name",
        "last_name",
        "role",
    }


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_me_is_read_only(api_client, method):
    user = UserFactory()
    login(api_client, user.username)

    assert getattr(api_client, method)(ME_URL).status_code == 405


# ---------- logout ----------


def test_logout_without_session_answers_401(api_client):
    assert api_client.post(LOGOUT_URL).status_code == 401


def test_logout_ends_the_session(api_client):
    user = UserFactory()
    login(api_client, user.username)

    response = api_client.post(LOGOUT_URL)

    assert response.status_code == 204
    assert api_client.get(ME_URL).status_code == 401
    assert api_client.post(LOGOUT_URL).status_code == 401


def test_old_session_cookie_is_dead_after_logout(api_client):
    user = UserFactory()
    login(api_client, user.username)
    old_session_id = api_client.cookies["sessionid"].value

    api_client.post(LOGOUT_URL)
    api_client.cookies["sessionid"] = old_session_id  # replay the cookie

    assert api_client.get(ME_URL).status_code == 401


def test_logout_without_csrf_token_is_forbidden(csrf_client):
    user = UserFactory()
    login(csrf_client, user.username)

    response = csrf_client.post(LOGOUT_URL)

    assert response.status_code == 403
    assert csrf_client.get(ME_URL).status_code == 200  # still logged in


def test_logout_with_csrf_token_works_for_a_strict_client(csrf_client):
    user = UserFactory()
    login(csrf_client, user.username)

    response = csrf_client.post(LOGOUT_URL, HTTP_X_CSRFTOKEN=current_token(csrf_client))

    assert response.status_code == 204
    assert csrf_client.get(ME_URL).status_code == 401
