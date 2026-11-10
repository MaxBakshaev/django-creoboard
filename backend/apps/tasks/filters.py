import django_filters
from django import forms
from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import Task, TaskStatus

MAX_DB_INTEGER = 2**31 - 1


class IdFilter(django_filters.NumberFilter):
    """Integer id with a bound, so that huge or fractional numbers are rejected with 400."""

    field_class = forms.IntegerField

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("min_value", 1)
        kwargs.setdefault("max_value", MAX_DB_INTEGER)
        super().__init__(*args, **kwargs)


class TaskFilter(django_filters.FilterSet):
    """List filters. They narrow the queryset that is already limited by the user's role."""

    # By id, without offering a list of users to choose from.
    assignee = IdFilter(field_name="assignee_id")
    geo = django_filters.CharFilter(lookup_expr="icontains", max_length=64)
    overdue = django_filters.BooleanFilter(method="filter_overdue")
    search = django_filters.CharFilter(method="filter_search", max_length=200)
    ordering = django_filters.OrderingFilter(
        fields=[
            ("created_at", "created_at"),
            ("deadline", "deadline"),
            ("updated_at", "updated_at"),
            ("id", "id"),
        ]
    )

    class Meta:
        model = Task
        fields = ["status", "priority", "creative_type"]

    def filter_overdue(self, queryset: QuerySet[Task], name: str, value: bool) -> QuerySet[Task]:
        """An open task whose deadline has passed (docs/ARCHITECTURE.md, section 8)."""
        overdue = Q(deadline__lt=timezone.now()) & ~Q(status=TaskStatus.DONE)
        return queryset.filter(overdue) if value else queryset.exclude(overdue)

    def filter_search(self, queryset: QuerySet[Task], name: str, value: str) -> QuerySet[Task]:
        value = value.strip()
        if not value:
            return queryset
        return queryset.filter(Q(offer__icontains=value) | Q(description__icontains=value))
