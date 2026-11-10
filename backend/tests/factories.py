from datetime import timedelta

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone

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


class TaskFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "tasks.Task"

    buyer = factory.SubFactory(UserFactory)
    offer = factory.Sequence(lambda n: f"Offer {n}")
    geo = "DE"
    creative_type = "static"
    quantity = 3
    description = "Three banners for the spring campaign."
    priority = "normal"
    deadline = factory.LazyFunction(lambda: timezone.now() + timedelta(days=3))


class TaskEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "tasks.TaskEvent"

    task = factory.SubFactory(TaskFactory)
    actor = factory.LazyAttribute(lambda o: o.task.buyer)
    action = "created"
