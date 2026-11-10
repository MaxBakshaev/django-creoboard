import factory
from django.contrib.auth import get_user_model

from apps.users.models import Role

DEFAULT_PASSWORD = "correct-horse-battery-staple"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    first_name = "Test"
    last_name = "User"
    role = Role.BUYER
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        self.set_password(extracted or DEFAULT_PASSWORD)
        if create:
            self.save(update_fields=["password"])

    class Params:
        creative = factory.Trait(role=Role.CREATIVE)
        lead = factory.Trait(role=Role.LEAD)
