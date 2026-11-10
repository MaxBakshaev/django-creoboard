import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


def test_user_model_is_the_project_user():
    assert get_user_model()._meta.label == "users.User"


def test_create_user_hashes_password_and_sets_aware_date_joined():
    user = get_user_model().objects.create_user(username="buyer1", password="s3cret-pass-123")

    assert user.check_password("s3cret-pass-123")
    assert user.password != "s3cret-pass-123"
    assert user.date_joined.tzinfo is not None
