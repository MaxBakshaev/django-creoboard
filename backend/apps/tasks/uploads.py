"""Checks of everything a client uploads or links: file name, extension, content and size.

The rules (docs/ARCHITECTURE.md, section 11):

- the extension must be in a whitelist and the content must really be of that type: the first
  bytes are matched with the `filetype` library, so an `.exe` renamed to `.png` is refused;
- the size is limited by `settings.MAX_UPLOAD_BYTES` (read at call time, so tests can lower it);
- the name on disk is random (UUID) and its extension is chosen here, never taken from the
  client; the client's name is sanitized and kept only as metadata for display;
- archives are never unpacked or inspected beyond the signature;
- links must be plain http or https.
"""

import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import filetype
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import URLValidator

# Extension -> content types `filetype` may report for it. SVG and HTML are left out on purpose:
# they can carry scripts. Everything is served as an attachment with `nosniff` anyway.
ALLOWED_TYPES: dict[str, frozenset[str]] = {
    "jpg": frozenset({"image/jpeg"}),
    "jpeg": frozenset({"image/jpeg"}),
    "png": frozenset({"image/png"}),
    "gif": frozenset({"image/gif"}),
    "webp": frozenset({"image/webp"}),
    "mp4": frozenset({"video/mp4", "video/x-m4v"}),
    "mov": frozenset({"video/quicktime"}),
    "webm": frozenset({"video/webm"}),
    "pdf": frozenset({"application/pdf"}),
    "zip": frozenset({"application/zip"}),
    "psd": frozenset({"image/vnd.adobe.photoshop"}),
}
ALLOWED_EXTENSIONS_TEXT = ", ".join(sorted(ALLOWED_TYPES))

MAX_NAME_LENGTH = 150
MAX_URL_LENGTH = 2000
SIGNATURE_BYTES = 8192
# Characters that never belong in a displayed file name: control (including CR and LF), format
# (bidi overrides such as U+202E), surrogate, private use and unassigned.
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

_url_validator = URLValidator(schemes=["http", "https"])


class InvalidUpload(ValueError):
    """The file or link is refused (the API answers 400)."""


class UploadTooLarge(Exception):
    """The file is bigger than `MAX_UPLOAD_BYTES` (the API answers 413)."""


@dataclass(frozen=True)
class CheckedUpload:
    """A file that passed every check; `file` is rewound to the start."""

    file: UploadedFile
    stored_name: str  # random name for the disk, with a server-chosen extension
    original_name: str  # sanitized, for display and the download header only
    size: int
    content_type: str  # detected from the content, not sent by the client


def max_request_bytes() -> int:
    """The biggest request body worth parsing: the file limit plus multipart overhead."""
    return settings.MAX_UPLOAD_BYTES + settings.UPLOAD_OVERHEAD_BYTES


def check_declared_size(content_length: str | None) -> None:
    """Raises `UploadTooLarge` when the declared request size is over the limit.

    A missing or malformed header passes: the size of the parsed file is checked later.
    """
    value = (content_length or "").strip()
    if not (value.isascii() and value.isdecimal()) or len(value) > 18:
        return
    if int(value) > max_request_bytes():
        raise UploadTooLarge(f"Запрос больше {settings.MAX_UPLOAD_MB} МБ.")


def sanitize_filename(name: str) -> str:
    """Returns a safe display name (no path, control or bidi characters, limited length).

    Raises `InvalidUpload` when nothing usable is left.
    """
    text = unicodedata.normalize("NFC", name or "")
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    # Tabs and line breaks become spaces (collapsed below); other control characters vanish.
    text = "".join(
        " " if ch.isspace() else ch
        for ch in text
        if ch.isspace() or unicodedata.category(ch) not in _DROPPED_CATEGORIES
    )
    text = " ".join(text.split()).strip(" .")
    if not text:
        raise InvalidUpload("Недопустимое имя файла.")
    if len(text) > MAX_NAME_LENGTH:
        extension = PurePosixPath(text).suffix
        keep = MAX_NAME_LENGTH - len(extension)
        text = text[: max(keep, 1)].rstrip(" .") + extension
    return text


def check_upload(file: UploadedFile) -> CheckedUpload:
    """Validates an uploaded file or raises `InvalidUpload` / `UploadTooLarge`."""
    size = file.size or 0
    if size > settings.MAX_UPLOAD_BYTES:
        raise UploadTooLarge(f"Файл больше {settings.MAX_UPLOAD_MB} МБ.")
    if size == 0:
        raise InvalidUpload("Файл пустой.")

    original_name = sanitize_filename(file.name or "")
    extension = PurePosixPath(original_name).suffix.lower().lstrip(".")
    if extension not in ALLOWED_TYPES:
        raise InvalidUpload(f"Недопустимый тип файла. Разрешено: {ALLOWED_EXTENSIONS_TEXT}.")

    file.seek(0)
    head = file.read(SIGNATURE_BYTES)
    file.seek(0)
    detected = filetype.guess(head)
    if detected is None or detected.mime not in ALLOWED_TYPES[extension]:
        raise InvalidUpload("Содержимое файла не соответствует его расширению.")

    return CheckedUpload(
        file=file,
        stored_name=f"{uuid.uuid4().hex}.{extension}",
        original_name=original_name,
        size=size,
        content_type=detected.mime,
    )


def check_url(value: str) -> str:
    """Returns the link if it is a plain http(s) URL without credentials, else raises."""
    url = (value or "").strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise InvalidUpload(f"Ссылка должна быть длиной от 1 до {MAX_URL_LENGTH} символов.")
    try:
        _url_validator(url)
        parts = urlsplit(url)
    except (DjangoValidationError, ValueError) as error:
        raise InvalidUpload("Допускаются только ссылки http и https.") from error
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise InvalidUpload("Допускаются только ссылки http и https.")
    if parts.username is not None or parts.password is not None:
        raise InvalidUpload("Ссылка не должна содержать логин и пароль.")
    return url
