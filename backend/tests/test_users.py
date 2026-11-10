import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.users.models import Role
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_model_is_the_project_user():
    assert get_user_model()._meta.label == "users.User"


def test_create_user_hashes_password_and_sets_aware_date_joined():
    user = get_user_model().objects.create_user(
        username="buyer1", password="s3cret-pass-123", role=Role.BUYER
    )

    assert user.check_password("s3cret-pass-123")
    assert user.password != "s3cret-pass-123"
    assert user.date_joined.tzinfo is not None


def test_roles_match_the_architecture():
    assert set(Role.values) == {"buyer", "creative", "lead"}


def test_role_is_optional_for_service_accounts():
    superuser = get_user_model().objects.create_superuser(username="owner", password="x-Pass-1234")

    assert superuser.role == ""
    superuser.full_clean()


def test_unknown_role_is_rejected_by_validation():
    user = UserFactory.build(role="admin")

    with pytest.raises(ValidationError) as error:
        user.full_clean()

    assert "role" in error.value.message_dict


def test_non_superuser_without_role_is_rejected_by_validation():
    user = UserFactory.build(role="")

    with pytest.raises(ValidationError):
        user.full_clean()


def test_non_superuser_without_role_is_rejected_by_the_database():
    with pytest.raises(IntegrityError), transaction.atomic():
        get_user_model().objects.create_user(username="norole", password="x-Pass-1234", role="")
