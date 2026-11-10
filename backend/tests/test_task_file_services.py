"""References, results and the submit precondition in the service layer."""

from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied

from apps.tasks import services
from apps.tasks.models import (
    MAX_ATTACHMENTS_PER_ROUND,
    MAX_REFERENCES_PER_TASK,
    Task,
    TaskAttachment,
    TaskEvent,
    TaskReference,
    TaskStatus,
)
from apps.tasks.uploads import check_upload
from tests.factories import TaskAttachmentFactory, TaskFactory, TaskReferenceFactory, UserFactory
from tests.sample_files import upload

pytestmark = pytest.mark.django_db


def checked(name: str = "result.png"):
    return check_upload(upload(name))


def working_task(status=TaskStatus.IN_PROGRESS, **fields):
    return TaskFactory(status=status, assignee=UserFactory(creative=True), **fields)


def files_on_disk() -> list[str]:
    root = settings.MEDIA_ROOT
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# --- add_reference ------------------------------------------------------------------------


def test_buyer_adds_a_link_reference_and_the_event_is_written():
    task = TaskFactory()

    reference = services.add_reference(
        task_id=task.pk, actor=task.buyer, url="https://example.com/ad", note="  Style  "
    )

    assert reference.kind == "link"
    assert (reference.url, reference.note) == ("https://example.com/ad", "Style")
    assert not reference.file
    (event,) = task.events.all()
    assert (event.action, event.actor) == ("reference_added", task.buyer)
    assert (event.from_status, event.to_status) == ("new", "new")
    assert event.payload == {
        "reference_id": reference.pk,
        "kind": "link",
        "url": "https://example.com/ad",
    }
    assert Task.objects.get(pk=task.pk).status == TaskStatus.NEW


def test_buyer_adds_a_file_reference_stored_under_a_random_name():
    task = TaskFactory()

    reference = services.add_reference(
        task_id=task.pk, actor=task.buyer, upload=checked("brief.pdf")
    )

    assert (reference.kind, reference.url) == ("file", "")
    assert (reference.original_name, reference.content_type) == ("brief.pdf", "application/pdf")
    assert reference.size > 0
    (on_disk,) = files_on_disk()
    assert on_disk.replace("\\", "/") == reference.file.name
    assert reference.file.name.startswith("references/")
    assert "brief" not in reference.file.name
    (event,) = task.events.all()
    assert event.payload == {
        "reference_id": reference.pk,
        "kind": "file",
        "original_name": "brief.pdf",
    }


@pytest.mark.parametrize(
    "status", [TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.REVISION]
)
def test_references_can_be_added_until_the_task_is_done(status):
    task = working_task(status) if status != TaskStatus.NEW else TaskFactory()

    services.add_reference(task_id=task.pk, actor=task.buyer, url="https://example.com")

    assert task.references.count() == 1


def test_no_references_after_done():
    task = working_task(TaskStatus.DONE)

    with pytest.raises(services.TaskConflictError):
        services.add_reference(task_id=task.pk, actor=task.buyer, url="https://example.com")

    assert not TaskReference.objects.exists()
    assert not task.events.exists()


@pytest.mark.parametrize("who", ["other_buyer", "creative", "assignee", "lead", "anonymous"])
def test_only_the_author_buyer_adds_references(who):
    task = working_task()
    actor = {
        "other_buyer": UserFactory(),
        "creative": UserFactory(creative=True),
        "assignee": task.assignee,
        "lead": UserFactory(lead=True),
        "anonymous": AnonymousUser(),
    }[who]

    with pytest.raises(PermissionDenied):
        services.add_reference(task_id=task.pk, actor=actor, url="https://example.com")

    assert not TaskReference.objects.exists()


def test_role_is_checked_before_status():
    task = working_task(TaskStatus.DONE)

    with pytest.raises(PermissionDenied):
        services.add_reference(task_id=task.pk, actor=UserFactory(), url="https://example.com")


def test_exactly_one_of_link_and_file_is_required():
    task = TaskFactory()

    with pytest.raises(ValueError, match="Exactly one"):
        services.add_reference(task_id=task.pk, actor=task.buyer)
    with pytest.raises(ValueError, match="Exactly one"):
        services.add_reference(
            task_id=task.pk, actor=task.buyer, url="https://example.com", upload=checked()
        )


def test_references_per_task_are_limited():
    task = TaskFactory()
    TaskReferenceFactory.create_batch(MAX_REFERENCES_PER_TASK, task=task)

    with pytest.raises(services.TaskConflictError):
        services.add_reference(task_id=task.pk, actor=task.buyer, url="https://example.com/more")

    assert task.references.count() == MAX_REFERENCES_PER_TASK


def test_reference_is_rolled_back_with_its_file_when_the_event_fails():
    task = TaskFactory()

    with (
        mock.patch.object(TaskEvent.objects, "create", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        services.add_reference(task_id=task.pk, actor=task.buyer, upload=checked("brief.pdf"))

    assert not TaskReference.objects.exists()
    assert files_on_disk() == []


# --- add_attachment -----------------------------------------------------------------------


def test_assignee_uploads_a_result_and_the_event_is_written():
    task = working_task()

    attachment = services.add_attachment(
        task_id=task.pk, actor=task.assignee, upload=checked("final.mp4")
    )

    assert attachment.round == 0
    assert (attachment.uploaded_by, attachment.task) == (task.assignee, task)
    assert (attachment.original_name, attachment.content_type) == ("final.mp4", "video/mp4")
    assert attachment.file.name.startswith("attachments/")
    assert "final" not in attachment.file.name
    (event,) = task.events.all()
    assert (event.action, event.actor) == ("attachment_added", task.assignee)
    assert (event.from_status, event.to_status) == ("in_progress", "in_progress")
    assert event.payload == {
        "attachment_id": attachment.pk,
        "original_name": "final.mp4",
        "size": attachment.size,
        "round": 0,
    }
    assert Task.objects.get(pk=task.pk).status == TaskStatus.IN_PROGRESS


def test_round_comes_from_the_revision_count():
    task = working_task(TaskStatus.REVISION, revision_count=2)

    attachment = services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked())

    assert attachment.round == 2


def test_uploading_the_same_name_twice_keeps_both_files():
    task = working_task()

    first = services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked("a.png"))
    second = services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked("a.png"))

    assert first.file.name != second.file.name
    assert first.original_name == second.original_name == "a.png"
    assert len(files_on_disk()) == 2
    assert task.attachments.count() == 2


@pytest.mark.parametrize("status", [TaskStatus.REVIEW, TaskStatus.DONE])
def test_results_are_refused_in_other_statuses(status):
    task = working_task(status)

    with pytest.raises(services.TaskConflictError):
        services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked())

    assert not TaskAttachment.objects.exists()
    assert files_on_disk() == []


def test_results_are_refused_for_a_new_task_nobody_took():
    task = TaskFactory()

    with pytest.raises(PermissionDenied):
        services.add_attachment(task_id=task.pk, actor=UserFactory(creative=True), upload=checked())


@pytest.mark.parametrize("who", ["other_creative", "buyer", "author", "lead", "anonymous"])
def test_only_the_assignee_uploads_results(who):
    task = working_task()
    actor = {
        "other_creative": UserFactory(creative=True),
        "buyer": UserFactory(),
        "author": task.buyer,
        "lead": UserFactory(lead=True),
        "anonymous": AnonymousUser(),
    }[who]

    with pytest.raises(PermissionDenied):
        services.add_attachment(task_id=task.pk, actor=actor, upload=checked())

    assert not TaskAttachment.objects.exists()
    assert files_on_disk() == []


def test_results_per_round_are_limited_and_a_new_round_starts_empty():
    task = working_task()
    TaskAttachmentFactory.create_batch(
        MAX_ATTACHMENTS_PER_ROUND, task=task, uploaded_by=task.assignee, round=0
    )

    with pytest.raises(services.TaskConflictError):
        services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked())

    Task.objects.filter(pk=task.pk).update(status=TaskStatus.REVISION, revision_count=1)
    second_round = services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked())
    assert second_round.round == 1


def test_result_is_rolled_back_with_its_file_when_the_event_fails():
    task = working_task()

    with (
        mock.patch.object(TaskEvent.objects, "create", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked())

    assert not TaskAttachment.objects.exists()
    assert files_on_disk() == []
    assert not task.events.exists()


# --- submit needs a result of the current round -------------------------------------------


def test_submit_without_a_result_is_a_conflict_and_changes_nothing():
    task = working_task()

    with pytest.raises(services.TaskConflictError, match="результат"):
        services.submit(task_id=task.pk, actor=task.assignee)

    task.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.submitted_at is None
    assert not task.events.exists()


def test_submit_with_a_result_works():
    task = working_task()
    services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked())

    services.submit(task_id=task.pk, actor=task.assignee)

    task.refresh_from_db()
    assert task.status == TaskStatus.REVIEW
    assert [e.action for e in task.events.all()] == ["attachment_added", "submitted"]


def test_after_a_revision_a_result_of_the_new_round_is_required():
    task = working_task()
    services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked())
    services.submit(task_id=task.pk, actor=task.assignee)
    services.request_revision(task_id=task.pk, actor=task.buyer, comment="Again.")

    with pytest.raises(services.TaskConflictError):  # the round-0 file does not count
        services.submit(task_id=task.pk, actor=task.assignee)
    assert Task.objects.get(pk=task.pk).status == TaskStatus.REVISION

    new = services.add_attachment(task_id=task.pk, actor=task.assignee, upload=checked("v2.png"))
    assert new.round == 1
    services.submit(task_id=task.pk, actor=task.assignee)

    assert Task.objects.get(pk=task.pk).status == TaskStatus.REVIEW


def test_a_result_of_another_task_does_not_count():
    task = working_task()
    other = working_task()
    TaskAttachmentFactory(task=other, uploaded_by=other.assignee, round=0)

    with pytest.raises(services.TaskConflictError):
        services.submit(task_id=task.pk, actor=task.assignee)


def test_submit_checks_the_role_and_the_status_before_the_result():
    task = working_task()  # no result yet

    with pytest.raises(PermissionDenied):  # a foreign creative is refused, not told about results
        services.submit(task_id=task.pk, actor=UserFactory(creative=True))

    review = working_task(TaskStatus.REVIEW)
    with pytest.raises(services.TaskConflictError, match="статусе"):
        services.submit(task_id=review.pk, actor=review.assignee)
