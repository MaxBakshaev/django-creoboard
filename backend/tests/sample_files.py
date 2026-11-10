"""Tiny files with real signatures, for upload tests."""

from django.core.files.uploadedfile import SimpleUploadedFile

SAMPLES: dict[str, bytes] = {
    "jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"0" * 30,
    "jpeg": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"0" * 30,
    "png": b"\x89PNG\r\n\x1a\n" + b"0" * 30,
    "gif": b"GIF89a" + b"0" * 30,
    "webp": b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"0" * 30,
    "mp4": b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
    "mov": b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ",
    "webm": (
        b"\x1a\x45\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm"
        + b"0" * 20
    ),
    "pdf": b"%PDF-1.4\n" + b"0" * 20,
    "zip": b"PK\x03\x04" + b"0" * 30,
    "psd": b"8BPS\x00\x01" + b"0" * 30,
}
EXE = b"MZ\x90\x00\x03" + b"0" * 30  # a Windows executable header


def upload(name: str = "result.png", content: bytes | None = None):
    """An uploaded file; by default a valid sample for the extension of `name`."""
    if content is None:
        content = SAMPLES[name.rsplit(".", 1)[-1].lower()]
    return SimpleUploadedFile(name, content)
