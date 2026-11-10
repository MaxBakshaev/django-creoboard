from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    BUYER = "buyer", "Байер"
    CREATIVE = "creative", "Креативщик"
    LEAD = "lead", "Руководитель"


class User(AbstractUser):
    """Custom user model; `role` drives access to the API (see docs/ARCHITECTURE.md, section 5).

    An empty role is allowed only for service accounts such as the superuser: permission
    checks compare the role exactly, so such a user gets no business access to the API.
    """

    role = models.CharField("роль", max_length=16, choices=Role.choices, blank=True)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(role="") | models.Q(is_superuser=True),
                name="user_role_required_unless_superuser",
                violation_error_message="Роль обязательна для всех, кроме суперпользователя.",
            ),
        ]
