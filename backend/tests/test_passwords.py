import pytest
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_new_passwords_are_hashed_with_argon2():
    user = UserFactory()

    assert user.password.startswith("argon2$")
    assert settings.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")


@pytest.mark.parametrize(
    "password",
    ["short1A", "12345678901234", "password", "qwertyuiop"],
    ids=["too-short", "numeric-only", "common", "common-2"],
)
def test_weak_passwords_are_rejected(password):
    with pytest.raises(ValidationError):
        validate_password(password)


def test_password_similar_to_username_is_rejected():
    user = UserFactory.build(username="creative-anna")

    with pytest.raises(ValidationError):
        validate_password("creative-anna", user=user)


def test_strong_password_is_accepted():
    validate_password("correct-horse-battery-staple")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("SESSION_COOKIE_HTTPONLY", True),
        ("SESSION_COOKIE_SAMESITE", "Lax"),
        ("CSRF_COOKIE_SAMESITE", "Lax"),
        # The SPA must read csrftoken to send it back as X-CSRFToken.
        ("CSRF_COOKIE_HTTPONLY", False),
    ],
)
def test_cookie_settings(name, expected):
    assert getattr(settings, name) == expected
