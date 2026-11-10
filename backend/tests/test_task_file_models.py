import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.tasks.models import TaskReference
from tests.factories import TaskAttachmentFactory, TaskFactory, TaskReferenceFactory

pytestmark = pytest.mark.django_db


def test_link_reference_is_valid():
    reference = TaskReferenceFactory()

    assert reference.kind == "link"
    assert reference.file.name == ""


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "link", "url": ""},
        {"kind": "link", "url": "https://example.com/x", "file": "references/a.png"},
        {"kind": "file", "url": ""},
        {"kind": "file", "url": "https://example.com/x", "file": "references/a.png"},
    ],
)
def test_reference_must_be_exactly_a_link_or_a_file(fields):
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskReference.objects.create(task=TaskFactory(), **fields)


def test_file_reference_is_valid():
    reference = TaskReference.objects.create(
        task=TaskFactory(), kind="file", file="references/a.png", original_name="a.png", size=5
    )

    assert reference.url == ""


def test_attachment_round_follows_the_task_by_default_in_the_factory():
    attachment = TaskAttachmentFactory(
        task=TaskFactory(status="revision", revision_count=2, assignee=None),
        uploaded_by=TaskFactory().buyer,
    )

    assert attachment.round == 2


def test_task_with_files_cannot_be_deleted():
    reference = TaskReferenceFactory()
    attachment = TaskAttachmentFactory(task=TaskFactory(), uploaded_by=TaskFactory().buyer)

    for task in (reference.task, attachment.task):
        with pytest.raises(ProtectedError):
            task.delete()
