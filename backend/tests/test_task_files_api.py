"""References, results and downloads over HTTP: 401, 403, 404 (IDOR), 409, 413 and the journal."""

from pathlib import Path
from unittest import mock
from urllib.parse import quote

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.tasks import services
from apps.tasks.models import TaskAttachment, TaskReference, TaskStatus
from apps.tasks.uploads import check_upload
from tests.factories import TaskFactory, UserFactory
from tests.sample_files import EXE, SAMPLES, upload

pytestmark = pytest.mark.django_db

# Literal paths: see the note in tests/test_auth.py.


def refs_url(task_id) -> str:
    return f"/api/v1/tasks/{task_id}/references/"


def atts_url(task_id) -> str:
    return f"/api/v1/tasks/{task_id}/attachments/"


def as_user(api_client, user):
    api_client.force_authenticate(user)
    return api_client


def working_task(status=TaskStatus.IN_PROGRESS, **fields):
    return TaskFactory(status=status, assignee=UserFactory(creative=True), **fields)


def superuser():
    return get_user_model().objects.create_superuser(username="owner", password="x" * 20)


def add_link(api_client, user, task_id, url="https://example.com/ad", **extra):
    as_user(api_client, user)
    return api_client.post(refs_url(task_id), {"kind": "link", "url": url, **extra}, format="json")


def add_file_reference(api_client, user, task_id, file=None, **extra):
    as_user(api_client, user)
    body = {"kind": "file", "file": file or upload("brief.pdf"), **extra}
    return api_client.post(refs_url(task_id), body, format="multipart")


def add_result(api_client, user, task_id, file=None, **extra):
    as_user(api_client, user)
    return api_client.post(
        atts_url(task_id), {"file": file or upload("result.png"), **extra}, format="multipart"
    )


def stored_attachment(task, name="result.png"):
    return services.add_attachment(
        task_id=task.pk, actor=task.assignee, upload=check_upload(upload(name))
    )


def stored_reference(task, name="brief.pdf"):
    return services.add_reference(
        task_id=task.pk, actor=task.buyer, upload=check_upload(upload(name))
    )


def read(response) -> bytes:
    """The body of a streamed file. Exhausting the stream closes the file; calling
    `response.close()` by hand would fire `request_finished` and close the test database
    connection."""
    return b"".join(response.streaming_content)


def event_actions(task) -> list[str]:
    return [e.action for e in task.events.all()]


# --- authentication and roles -------------------------------------------------------------


@pytest.mark.parametrize("make_url", [refs_url, atts_url])
@pytest.mark.parametrize("method", ["get", "post"])
def test_anonymous_gets_401(api_client, make_url, method):
    task = working_task()

    assert getattr(api_client, method)(make_url(task.pk)).status_code == 401


@pytest.mark.parametrize(
    "path", ["/api/v1/attachments/1/download/", "/api/v1/references/1/download/"]
)
def test_anonymous_cannot_download(api_client, path):
    assert api_client.get(path).status_code == 401


@pytest.mark.parametrize("make_url", [refs_url, atts_url])
@pytest.mark.parametrize("method", ["get", "post"])
def test_user_without_a_role_gets_403(api_client, make_url, method):
    task = working_task()
    as_user(api_client, superuser())

    assert getattr(api_client, method)(make_url(task.pk)).status_code == 403


def test_user_without_a_role_cannot_download(api_client):
    attachment = stored_attachment(working_task())
    as_user(api_client, superuser())

    assert api_client.get(f"/api/v1/attachments/{attachment.pk}/download/").status_code == 403


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
@pytest.mark.parametrize("make_url", [refs_url, atts_url])
def test_other_methods_are_not_allowed(api_client, make_url, method):
    task = working_task()
    as_user(api_client, task.buyer)

    assert getattr(api_client, method)(make_url(task.pk)).status_code == 405


# --- references: who may add --------------------------------------------------------------


def test_author_adds_a_link_reference(api_client):
    task = TaskFactory()

    response = add_link(api_client, task.buyer, task.pk, note="Mood")

    assert response.status_code == 201
    body = response.json()
    assert (body["kind"], body["url"], body["note"]) == ("link", "https://example.com/ad", "Mood")
    assert body["download_url"] is None
    assert "file" not in body
    assert TaskReference.objects.get().task == task
    assert event_actions(task) == ["reference_added"]


def test_author_adds_a_file_reference(api_client):
    task = TaskFactory()

    response = add_file_reference(api_client, task.buyer, task.pk, note="Brief")

    assert response.status_code == 201
    body = response.json()
    assert (body["kind"], body["original_name"], body["content_type"]) == (
        "file",
        "brief.pdf",
        "application/pdf",
    )
    assert body["download_url"] == f"/api/v1/references/{body['id']}/download/"
    assert body["size"] == len(SAMPLES["pdf"])
    # Neither the path on disk nor the stored name is exposed.
    assert "file" not in body
    assert TaskReference.objects.get().file.name not in str(body)
    assert event_actions(task) == ["reference_added"]


@pytest.mark.parametrize(
    "status", [TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.REVISION]
)
def test_author_adds_references_until_the_task_is_done(api_client, status):
    task = working_task(status)

    assert add_link(api_client, task.buyer, task.pk).status_code == 201


def test_no_reference_for_a_done_task_is_409(api_client):
    task = working_task(TaskStatus.DONE)

    assert add_link(api_client, task.buyer, task.pk).status_code == 409
    assert not TaskReference.objects.exists()


@pytest.mark.parametrize("who", ["creative", "assignee", "lead"])
def test_creative_and_lead_cannot_add_references(api_client, who):
    task = working_task()
    user = {
        "creative": UserFactory(creative=True),
        "assignee": task.assignee,
        "lead": UserFactory(lead=True),
    }[who]

    assert add_link(api_client, user, task.pk).status_code == 403
    assert add_file_reference(api_client, user, task.pk).status_code == 403
    assert not TaskReference.objects.exists()


def test_another_buyer_gets_404_for_a_foreign_task(api_client):
    task = TaskFactory()

    assert add_link(api_client, UserFactory(), task.pk).status_code == 404
    assert as_user(api_client, UserFactory()).get(refs_url(task.pk)).status_code == 404
    assert not TaskReference.objects.exists()


def test_a_missing_task_is_404(api_client):
    assert add_link(api_client, UserFactory(), 999_999).status_code == 404
    assert as_user(api_client, UserFactory()).get(refs_url(999_999)).status_code == 404


# --- references: the data -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(document.cookie)",
        "JAVASCRIPT:alert(1)",
        "file:///etc/passwd",
        "data:text/html;base64,PHNjcmlwdD4=",
        "ftp://example.com/x",
        "//example.com/x",
        "example.com/x",
        "https://user:pass@example.com/x",
        "",
    ],
)
def test_links_other_than_http_and_https_are_refused(api_client, url):
    task = TaskFactory()

    response = add_link(api_client, task.buyer, task.pk, url=url)

    assert response.status_code == 400
    assert "url" in response.json()
    assert not TaskReference.objects.exists()
    assert not task.events.exists()


@pytest.mark.parametrize("url", ["http://example.com", "https://example.com/a?b=1#c"])
def test_http_and_https_links_are_accepted(api_client, url):
    task = TaskFactory()

    assert add_link(api_client, task.buyer, task.pk, url=url).status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"kind": "nope", "url": "https://example.com"},
        {"kind": "link"},
        {"kind": "file"},
        {"kind": "file", "url": "https://example.com"},
        {"url": "https://example.com"},
    ],
)
def test_inconsistent_reference_bodies_are_400(api_client, body):
    task = TaskFactory()
    as_user(api_client, task.buyer)

    assert api_client.post(refs_url(task.pk), body, format="json").status_code == 400
    assert not TaskReference.objects.exists()


def test_a_link_reference_with_a_file_is_400(api_client):
    task = TaskFactory()
    as_user(api_client, task.buyer)
    body = {"kind": "link", "url": "https://example.com", "file": upload("brief.pdf")}

    assert api_client.post(refs_url(task.pk), body, format="multipart").status_code == 400
    assert not TaskReference.objects.exists()


def test_note_is_limited(api_client):
    task = TaskFactory()

    assert add_link(api_client, task.buyer, task.pk, note="x" * 501).status_code == 400


def test_forged_reference_fields_are_ignored(api_client):
    task = TaskFactory()
    other = TaskFactory()

    response = add_link(
        api_client,
        task.buyer,
        task.pk,
        id=777,
        task=other.pk,
        size=1,
        content_type="text/html",
        original_name="evil.html",
        created_at="2000-01-01T00:00:00Z",
    )

    assert response.status_code == 201
    reference = TaskReference.objects.get()
    assert (reference.task, reference.size, reference.content_type) == (task, 0, "")
    assert reference.pk != 777
    assert reference.original_name == ""
    assert reference.created_at.year != 2000


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("virus.png", EXE),
        ("virus.pdf", EXE),
        ("tool.exe", EXE),
        ("page.html", b"<script>alert(1)</script>"),
        ("note.png", b"plain text"),
        ("empty.png", b""),
    ],
)
def test_bad_reference_files_are_400(api_client, name, content):
    task = TaskFactory()

    response = add_file_reference(
        api_client, task.buyer, task.pk, SimpleUploadedFile(name, content)
    )

    assert response.status_code == 400
    assert "file" in response.json()
    assert not TaskReference.objects.exists()


def test_a_too_large_reference_file_is_413(api_client, settings):
    settings.MAX_UPLOAD_BYTES = 10
    task = TaskFactory()

    assert add_file_reference(api_client, task.buyer, task.pk).status_code == 413
    assert not TaskReference.objects.exists()


def test_too_many_references_is_409(api_client):
    task = TaskFactory()
    for number in range(20):
        add_link(api_client, task.buyer, task.pk, url=f"https://example.com/{number}")

    assert add_link(api_client, task.buyer, task.pk).status_code == 409
    assert task.references.count() == 20


# --- references: the list -----------------------------------------------------------------


def test_references_are_listed_for_whoever_sees_the_task(api_client):
    task = TaskFactory()
    add_link(api_client, task.buyer, task.pk, url="https://example.com/1")
    add_file_reference(api_client, task.buyer, task.pk)

    for viewer in (task.buyer, UserFactory(creative=True), UserFactory(lead=True)):
        response = as_user(api_client, viewer).get(refs_url(task.pk))
        assert response.status_code == 200
        assert [item["kind"] for item in response.json()] == ["link", "file"]


def test_a_creative_does_not_see_references_of_a_task_taken_by_someone_else(api_client):
    task = working_task()
    stored_reference(task)

    stranger = as_user(api_client, UserFactory(creative=True))
    assert stranger.get(refs_url(task.pk)).status_code == 404
    assert as_user(api_client, task.assignee).get(refs_url(task.pk)).status_code == 200


# --- results: who may upload --------------------------------------------------------------


def test_assignee_uploads_a_result(api_client):
    task = working_task()

    response = add_result(api_client, task.assignee, task.pk)

    assert response.status_code == 201
    body = response.json()
    assert (body["original_name"], body["content_type"], body["round"]) == (
        "result.png",
        "image/png",
        0,
    )
    assert body["uploaded_by"]["id"] == task.assignee.pk
    assert body["download_url"] == f"/api/v1/attachments/{body['id']}/download/"
    assert "file" not in body
    assert event_actions(task) == ["attachment_added"]


def test_round_is_set_by_the_server_after_a_revision(api_client):
    task = working_task(TaskStatus.REVISION, revision_count=2)

    response = add_result(api_client, task.assignee, task.pk, round=0)

    assert response.status_code == 201
    assert response.json()["round"] == 2
    assert TaskAttachment.objects.get().round == 2


def test_forged_result_fields_are_ignored(api_client):
    task = working_task()
    stranger = UserFactory(creative=True)

    response = add_result(
        api_client,
        task.assignee,
        task.pk,
        round=9,
        uploaded_by=stranger.pk,
        task=999,
        size=1,
        content_type="text/html",
        original_name="evil.html",
    )

    assert response.status_code == 201
    attachment = TaskAttachment.objects.get()
    assert (attachment.round, attachment.uploaded_by, attachment.task) == (0, task.assignee, task)
    assert (attachment.original_name, attachment.content_type) == ("result.png", "image/png")
    assert attachment.size == len(SAMPLES["png"])


@pytest.mark.parametrize("who", ["author", "lead", "other_buyer"])
def test_buyers_and_lead_cannot_upload_results(api_client, who):
    task = working_task()
    user = {
        "author": task.buyer,
        "lead": UserFactory(lead=True),
        "other_buyer": UserFactory(),
    }[who]

    assert add_result(api_client, user, task.pk).status_code == 403
    assert not TaskAttachment.objects.exists()


def test_a_creative_who_does_not_see_the_task_gets_404(api_client):
    task = working_task()

    assert add_result(api_client, UserFactory(creative=True), task.pk).status_code == 404
    assert not TaskAttachment.objects.exists()


def test_a_creative_cannot_upload_to_a_new_task_that_is_visible_but_not_theirs(api_client):
    task = TaskFactory()  # "new": every creative sees it, nobody works on it

    assert add_result(api_client, UserFactory(creative=True), task.pk).status_code == 403
    assert not TaskAttachment.objects.exists()


@pytest.mark.parametrize("status", [TaskStatus.REVIEW, TaskStatus.DONE])
def test_results_in_other_statuses_are_409(api_client, status):
    task = working_task(status)

    assert add_result(api_client, task.assignee, task.pk).status_code == 409
    assert not TaskAttachment.objects.exists()


def test_a_missing_task_for_results_is_404(api_client):
    assert add_result(api_client, UserFactory(creative=True), 999_999).status_code == 404


# --- results: the data --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("virus.png", EXE),
        ("tool.exe", EXE),
        ("a.exe.png", EXE),
        ("a.png.exe", SAMPLES["png"]),
        ("page.html", b"<h1>x</h1>"),
        ("image.svg", b"<svg onload=alert(1)></svg>"),
        ("archive.zip", SAMPLES["pdf"]),
        ("empty.png", b""),
        ("noextension", SAMPLES["png"]),
    ],
)
def test_bad_result_files_are_400(api_client, name, content):
    task = working_task()

    response = add_result(api_client, task.assignee, task.pk, SimpleUploadedFile(name, content))

    assert response.status_code == 400
    assert "file" in response.json()
    assert not TaskAttachment.objects.exists()
    assert not task.events.exists()
    assert not any(Path(settings.MEDIA_ROOT).rglob("*.*"))


def test_a_request_without_a_file_is_400(api_client):
    task = working_task()
    as_user(api_client, task.assignee)

    assert api_client.post(atts_url(task.pk), {}, format="multipart").status_code == 400
    text_field = api_client.post(atts_url(task.pk), {"file": "text"}, format="multipart")
    assert text_field.status_code == 400
    assert api_client.post(atts_url(task.pk), {"file": "x"}, format="json").status_code == 400


def test_a_too_large_result_is_413(api_client, settings):
    settings.MAX_UPLOAD_BYTES = 10
    task = working_task()

    response = add_result(api_client, task.assignee, task.pk)

    assert response.status_code == 413
    assert not TaskAttachment.objects.exists()
    assert not any(Path(settings.MEDIA_ROOT).rglob("*.*"))


def test_a_declared_size_over_the_limit_is_refused_before_the_body_is_parsed(api_client, settings):
    settings.MAX_UPLOAD_BYTES = 1000
    settings.UPLOAD_OVERHEAD_BYTES = 1000
    task = working_task()
    api_client.force_login(task.assignee)  # a real session: force_authenticate skips the check

    with mock.patch("apps.tasks.file_serializers.check_file_field") as check:
        response = api_client.post(
            atts_url(task.pk), {"file": upload()}, format="multipart", CONTENT_LENGTH="2001"
        )

    assert response.status_code == 413
    check.assert_not_called()
    assert not TaskAttachment.objects.exists()


def test_the_declared_size_limit_applies_to_references_too(api_client, settings):
    settings.MAX_UPLOAD_BYTES = 1000
    settings.UPLOAD_OVERHEAD_BYTES = 0
    task = TaskFactory()
    api_client.force_login(task.buyer)

    response = api_client.post(
        refs_url(task.pk),
        {"kind": "link", "url": "https://example.com"},
        format="json",
        CONTENT_LENGTH="1001",
    )

    assert response.status_code == 413


def test_the_declared_size_is_checked_before_csrf_reads_the_body(csrf_client, settings):
    """Django's CSRF check parses a POST body; an oversized one must be refused first."""
    settings.MAX_UPLOAD_BYTES = 1000
    settings.UPLOAD_OVERHEAD_BYTES = 0
    task = working_task()
    csrf_client.force_login(task.assignee)  # no CSRF token is sent: 403 if the body were read

    with mock.patch("apps.tasks.file_serializers.check_file_field") as check:
        response = csrf_client.post(
            atts_url(task.pk), {"file": upload()}, format="multipart", CONTENT_LENGTH="1001"
        )

    assert response.status_code == 413
    check.assert_not_called()


@pytest.mark.parametrize("declared", ["abc", "-5", "", "9" * 5000, "1e9"])
def test_a_malformed_content_length_does_not_crash(api_client, declared):
    task = working_task()
    api_client.force_login(task.assignee)

    response = api_client.post(
        atts_url(task.pk), {"file": upload()}, format="multipart", CONTENT_LENGTH=declared
    )

    assert response.status_code in {201, 400, 413}


def test_the_same_name_twice_gives_two_results_with_different_files(api_client):
    task = working_task()

    first = add_result(api_client, task.assignee, task.pk).json()
    second = add_result(api_client, task.assignee, task.pk).json()

    assert first["id"] != second["id"]
    assert first["original_name"] == second["original_name"] == "result.png"
    names = list(TaskAttachment.objects.values_list("file", flat=True))
    assert len(set(names)) == 2
    assert all(name.startswith("attachments/") and "result" not in name for name in names)
    assert event_actions(task) == ["attachment_added", "attachment_added"]


def test_the_name_from_the_client_is_cleaned(api_client):
    task = working_task()

    dirty = SimpleUploadedFile("../../etc/evil‮gnp.png", SAMPLES["png"])

    response = add_result(api_client, task.assignee, task.pk, dirty)

    assert response.status_code == 201
    assert response.json()["original_name"] == "evilgnp.png"


def test_too_many_results_in_a_round_is_409(api_client):
    task = working_task()
    for _ in range(20):
        assert add_result(api_client, task.assignee, task.pk).status_code == 201

    assert add_result(api_client, task.assignee, task.pk).status_code == 409


# --- results: the list --------------------------------------------------------------------


def test_results_are_listed_for_whoever_sees_the_task(api_client):
    task = working_task()
    add_result(api_client, task.assignee, task.pk, upload("one.png"))
    add_result(api_client, task.assignee, task.pk, upload("two.mp4"))

    for viewer in (task.assignee, task.buyer, UserFactory(lead=True)):
        response = as_user(api_client, viewer).get(atts_url(task.pk))
        assert response.status_code == 200
        assert [item["original_name"] for item in response.json()] == ["one.png", "two.mp4"]


@pytest.mark.parametrize("who", ["other_buyer", "other_creative"])
def test_results_of_a_foreign_task_are_404(api_client, who):
    task = working_task()
    user = UserFactory() if who == "other_buyer" else UserFactory(creative=True)

    assert as_user(api_client, user).get(atts_url(task.pk)).status_code == 404


# --- the journal --------------------------------------------------------------------------


def test_file_events_show_up_in_the_history(api_client):
    task = working_task()
    add_link(api_client, task.buyer, task.pk)
    add_result(api_client, task.assignee, task.pk)

    events = as_user(api_client, UserFactory(lead=True)).get(f"/api/v1/tasks/{task.pk}/events/")

    assert [e["action"] for e in events.json()] == ["reference_added", "attachment_added"]
    assert events.json()[1]["payload"]["original_name"] == "result.png"


# --- submit over HTTP ---------------------------------------------------------------------


def test_submit_needs_a_result_of_the_current_round(api_client):
    task = working_task()
    submit = f"/api/v1/tasks/{task.pk}/submit/"

    assert as_user(api_client, task.assignee).post(submit).status_code == 409
    add_result(api_client, task.assignee, task.pk)
    assert as_user(api_client, task.assignee).post(submit).status_code == 200

    revision = as_user(api_client, task.buyer).post(
        f"/api/v1/tasks/{task.pk}/request-revision/", {"comment": "Again."}, format="json"
    )
    assert revision.status_code == 200
    assert as_user(api_client, task.assignee).post(submit).status_code == 409  # round 0 is old
    assert add_result(api_client, task.assignee, task.pk).json()["round"] == 1
    assert as_user(api_client, task.assignee).post(submit).status_code == 200


# --- download -----------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["assignee", "author", "lead"])
def test_viewers_download_a_result(api_client, who):
    task = working_task()
    attachment = stored_attachment(task, "final.png")
    user = {"assignee": task.assignee, "author": task.buyer, "lead": UserFactory(lead=True)}[who]

    response = as_user(api_client, user).get(f"/api/v1/attachments/{attachment.pk}/download/")

    assert response.status_code == 200
    assert read(response) == SAMPLES["png"]
    assert response["Content-Type"] == "image/png"
    assert response["Content-Disposition"].startswith("attachment;")
    assert 'filename="final.png"' in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]


def test_unicode_file_name_is_encoded_in_the_header(api_client):
    task = working_task()
    attachment = stored_attachment(task)
    TaskAttachment.objects.filter(pk=attachment.pk).update(original_name="Баннер весна.png")

    response = as_user(api_client, task.buyer).get(f"/api/v1/attachments/{attachment.pk}/download/")

    header = response["Content-Disposition"]
    assert header.startswith("attachment;")
    assert f"filename*=utf-8''{quote('Баннер весна.png')}" in header
    assert "Баннер" not in header  # not sent raw
    read(response)


def test_quotes_and_line_breaks_cannot_break_the_header(api_client):
    task = working_task()
    attachment = stored_attachment(task)
    TaskAttachment.objects.filter(pk=attachment.pk).update(original_name='a".png\r\nX-Evil: 1')

    response = as_user(api_client, task.buyer).get(f"/api/v1/attachments/{attachment.pk}/download/")

    assert "X-Evil" not in response
    assert "\r" not in response["Content-Disposition"]
    assert "\n" not in response["Content-Disposition"]
    read(response)


def test_the_accept_header_of_the_client_does_not_matter(api_client):
    task = working_task()
    attachment = stored_attachment(task)
    as_user(api_client, task.buyer)

    response = api_client.get(
        f"/api/v1/attachments/{attachment.pk}/download/", HTTP_ACCEPT="image/png"
    )

    assert response.status_code == 200
    read(response)


@pytest.mark.parametrize("who", ["other_buyer", "other_creative"])
def test_download_of_a_foreign_result_is_404(api_client, who):
    attachment = stored_attachment(working_task())
    user = UserFactory() if who == "other_buyer" else UserFactory(creative=True)

    response = as_user(api_client, user).get(f"/api/v1/attachments/{attachment.pk}/download/")

    assert response.status_code == 404


def test_download_ids_that_do_not_exist_are_404(api_client):
    as_user(api_client, UserFactory(lead=True))

    assert api_client.get("/api/v1/attachments/999999/download/").status_code == 404
    assert api_client.get("/api/v1/references/999999/download/").status_code == 404


def test_download_with_a_malformed_id_is_404(api_client):
    as_user(api_client, UserFactory(lead=True))

    assert api_client.get("/api/v1/attachments/abc/download/").status_code == 404


def test_download_when_the_file_is_gone_from_the_disk_is_404(api_client):
    task = working_task()
    attachment = stored_attachment(task)
    Path(settings.MEDIA_ROOT, attachment.file.name).unlink()

    response = as_user(api_client, task.buyer).get(f"/api/v1/attachments/{attachment.pk}/download/")

    assert response.status_code == 404
    assert attachment.file.name not in response.content.decode()


def test_download_of_a_file_reference(api_client):
    task = TaskFactory()
    reference = stored_reference(task)

    # The author, a creative who sees the new task and the lead may download it.
    for viewer in (task.buyer, UserFactory(creative=True), UserFactory(lead=True)):
        response = as_user(api_client, viewer).get(f"/api/v1/references/{reference.pk}/download/")
        assert response.status_code == 200
        assert read(response) == SAMPLES["pdf"]
        assert response["Content-Type"] == "application/pdf"
        assert response["Content-Disposition"].startswith("attachment;")
        assert response["X-Content-Type-Options"] == "nosniff"


def test_download_of_a_foreign_file_reference_is_404(api_client):
    task = TaskFactory()
    reference = stored_reference(task)
    as_user(api_client, UserFactory())

    assert api_client.get(f"/api/v1/references/{reference.pk}/download/").status_code == 404


def test_a_link_reference_has_nothing_to_download(api_client):
    task = TaskFactory()
    add_link(api_client, task.buyer, task.pk)
    reference = TaskReference.objects.get()

    response = as_user(api_client, task.buyer).get(f"/api/v1/references/{reference.pk}/download/")

    assert response.status_code == 404


def test_a_result_id_does_not_open_a_reference_and_vice_versa(api_client):
    task = working_task()
    attachment = stored_attachment(task)
    reference = stored_reference(task)
    as_user(api_client, task.buyer)

    # Different tables: the ids may coincide, the endpoints must not mix them up.
    assert api_client.get(f"/api/v1/attachments/{attachment.pk}/download/").status_code == 200
    assert api_client.get(f"/api/v1/references/{reference.pk}/download/").status_code == 200
    assert read(api_client.get(f"/api/v1/attachments/{attachment.pk}/download/")) == SAMPLES["png"]
    assert read(api_client.get(f"/api/v1/references/{reference.pk}/download/")) == SAMPLES["pdf"]


def test_files_are_not_reachable_by_their_storage_path(api_client):
    task = working_task()
    attachment = stored_attachment(task)
    as_user(api_client, task.buyer)

    assert api_client.get(f"/media/{attachment.file.name}").status_code == 404
    assert api_client.get(f"/{attachment.file.name}").status_code == 404
