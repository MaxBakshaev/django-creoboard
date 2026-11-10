import django_filters
from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import Task, TaskStatus


class TaskFilter(django_filters.FilterSet):
    """List filters. They narrow the queryset that is already limited by the user's role."""

    # By id, without offering a list of users to choose from.
    assignee = django_filters.NumberFilter(field_name="assignee_id")
    geo = django_filters.CharFilter(lookup_expr="icontains")
    overdue = django_filters.BooleanFilter(method="filter_overdue")
    search = django_filters.CharFilter(method="filter_search")
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
