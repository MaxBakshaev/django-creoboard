"""Serializers of references and results. Only metadata is exposed: the path on disk never is."""

from typing import Any

from django.urls import reverse
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import APIException

from apps.users.serializers import UserBriefSerializer

from . import uploads
from .models import MAX_NOTE_LENGTH, ReferenceKind, TaskAttachment, TaskReference
from .uploads import CheckedUpload


class PayloadTooLarge(APIException):
    status_code = 413
    default_detail = "Файл слишком большой."
    default_code = "payload_too_large"


def check_file_field(file) -> CheckedUpload:
    """Runs the upload checks: 400 for a refused file, 413 for a too large one."""
    try:
        return uploads.check_upload(file)
    except uploads.UploadTooLarge as error:
        raise PayloadTooLarge(str(error)) from error
    except uploads.InvalidUpload as error:
        raise serializers.ValidationError({"file": str(error)}) from error


class ReferenceSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = TaskReference
        fields = [
            "id",
            "kind",
            "url",
            "original_name",
            "size",
            "content_type",
            "note",
            "download_url",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_download_url(self, reference: TaskReference) -> str | None:
        if reference.kind != ReferenceKind.FILE:
            return None
        return reverse("reference-download", kwargs={"pk": reference.pk})


class ReferenceCreateSerializer(serializers.Serializer):
    """A link (JSON) or a file (multipart); exactly the fields of the chosen kind."""

    kind = serializers.ChoiceField(choices=ReferenceKind.choices)
    url = serializers.CharField(required=False, allow_blank=True, max_length=uploads.MAX_URL_LENGTH)
    file = serializers.FileField(required=False, allow_empty_file=True, use_url=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=MAX_NOTE_LENGTH)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        kind = attrs["kind"]
        note = attrs.get("note", "")
        if kind == ReferenceKind.LINK:
            if attrs.get("file") is not None:
                raise serializers.ValidationError({"file": "Для ссылки файл не нужен."})
            try:
                url = uploads.check_url(attrs.get("url", ""))
            except uploads.InvalidUpload as error:
                raise serializers.ValidationError({"url": str(error)}) from error
            return {"url": url, "note": note}
        if attrs.get("url"):
            raise serializers.ValidationError({"url": "Для файла ссылка не нужна."})
        if attrs.get("file") is None:
            raise serializers.ValidationError({"file": "Приложите файл."})
        return {"upload": check_file_field(attrs["file"]), "note": note}


class AttachmentSerializer(serializers.ModelSerializer):
    uploaded_by = UserBriefSerializer(read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = TaskAttachment
        fields = [
            "id",
            "uploaded_by",
            "original_name",
            "size",
            "content_type",
            "round",
            "download_url",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_download_url(self, attachment: TaskAttachment) -> str:
        return reverse("attachment-download", kwargs={"pk": attachment.pk})


class AttachmentCreateSerializer(serializers.Serializer):
    """Only the file is read from the client; the round and the author are set by the server."""

    file = serializers.FileField(allow_empty_file=True, use_url=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {"upload": check_file_field(attrs["file"])}
