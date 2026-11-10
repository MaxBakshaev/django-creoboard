from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model, created up front so AUTH_USER_MODEL never has to change.

    The `role` field is added in step 3.
    """
