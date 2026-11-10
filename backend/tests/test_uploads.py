import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.tasks import uploads
from apps.tasks.uploads import InvalidUpload, UploadTooLarge
from tests.sample_files import EXE, SAMPLES, upload

# --- extension and signature --------------------------------------------------------------


@pytest.mark.parametrize("extension", sorted(SAMPLES))
def test_every_whitelisted_type_passes_with_a_matching_signature(extension):
    checked = uploads.check_upload(upload(f"file.{extension}"))

    assert checked.stored_name.endswith(f".{extension}")
    assert checked.content_type in uploads.ALLOWED_TYPES[extension]
    assert checked.file.read() == SAMPLES[extension]  # rewound to the start


def test_whitelist_and_samples_cover_the_same_extensions():
    assert set(SAMPLES) == set(uploads.ALLOWED_TYPES)


@pytest.mark.parametrize("name", ["tool.exe", "page.html", "image.svg", "run.sh", "x.php", "a.js"])
def test_forbidden_extension_is_refused_even_with_a_valid_signature(name):
    with pytest.raises(InvalidUpload):
        uploads.check_upload(SimpleUploadedFile(name, SAMPLES["png"]))


@pytest.mark.parametrize("name", ["virus.png", "virus.jpg", "virus.pdf", "virus.zip", "v.mp4"])
def test_executable_renamed_to_an_allowed_extension_is_refused(name):
    with pytest.raises(InvalidUpload, match="не соответствует"):
        uploads.check_upload(SimpleUploadedFile(name, EXE))


@pytest.mark.parametrize("name", ["a.exe.png", "a.png.exe", "a.php.jpg", "a.html.png"])
def test_double_extensions_follow_the_last_one_and_the_signature(name):
    with pytest.raises(InvalidUpload):
        uploads.check_upload(SimpleUploadedFile(name, EXE))


def test_content_of_another_allowed_type_is_refused():
    with pytest.raises(InvalidUpload, match="не соответствует"):
        uploads.check_upload(SimpleUploadedFile("doc.zip", SAMPLES["pdf"]))
    with pytest.raises(InvalidUpload, match="не соответствует"):
        uploads.check_upload(SimpleUploadedFile("pic.png", SAMPLES["jpg"]))


def test_plain_text_with_an_image_extension_is_refused():
    with pytest.raises(InvalidUpload):
        uploads.check_upload(SimpleUploadedFile("note.png", b"just text, no signature"))


@pytest.mark.parametrize("name", ["noextension", ".png", "trailingdot.", "..."])
def test_a_name_without_a_real_extension_is_refused(name):
    with pytest.raises(InvalidUpload):
        uploads.check_upload(SimpleUploadedFile(name, SAMPLES["png"]))


def test_extension_is_case_insensitive():
    checked = uploads.check_upload(SimpleUploadedFile("PHOTO.PNG", SAMPLES["png"]))

    assert checked.stored_name.endswith(".png")
    assert checked.original_name == "PHOTO.PNG"


# --- size ---------------------------------------------------------------------------------


def test_empty_file_is_refused():
    with pytest.raises(InvalidUpload, match="пустой"):
        uploads.check_upload(SimpleUploadedFile("empty.png", b""))


def test_file_over_the_limit_is_too_large(settings):
    settings.MAX_UPLOAD_BYTES = 10

    with pytest.raises(UploadTooLarge):
        uploads.check_upload(upload("big.png"))


def test_file_exactly_at_the_limit_is_accepted(settings):
    content = SAMPLES["png"]
    settings.MAX_UPLOAD_BYTES = len(content)

    assert uploads.check_upload(SimpleUploadedFile("edge.png", content)).size == len(content)


def test_size_is_checked_before_the_content_is_read(settings):
    settings.MAX_UPLOAD_BYTES = 5

    with pytest.raises(UploadTooLarge):  # not InvalidUpload, although the name is bad too
        uploads.check_upload(SimpleUploadedFile("tool.exe", EXE))


def test_request_limit_is_the_file_limit_plus_overhead(settings):
    settings.MAX_UPLOAD_BYTES = 1000
    settings.UPLOAD_OVERHEAD_BYTES = 24

    assert uploads.max_request_bytes() == 1024


# --- names on disk and for display --------------------------------------------------------


def test_stored_name_is_random_and_ignores_the_client_name():
    first = uploads.check_upload(SimpleUploadedFile("same.png", SAMPLES["png"]))
    second = uploads.check_upload(SimpleUploadedFile("same.png", SAMPLES["png"]))

    assert first.stored_name != second.stored_name
    assert "same" not in first.stored_name
    stem = first.stored_name.removesuffix(".png")
    assert len(stem) == 32
    assert int(stem, 16) >= 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.png", "passwd.png"),
        ("..\\..\\windows\\evil.png", "evil.png"),
        ("/abs/path/photo.png", "photo.png"),
        ("C:\\Users\\x\\photo.png", "photo.png"),
        ("line\r\nbreak.png", "line break.png"),
        ("tab\tand   spaces.png", "tab and spaces.png"),
        ("null\x00byte.png", "nullbyte.png"),
        ("rlo\u202egnp.exe", "rlognp.exe"),  # the bidi override cannot hide the extension
        ("zero\u200bwidth.png", "zerowidth.png"),
        ("  .hidden.png  ", "hidden.png"),
        ('quote".png', 'quote".png'),
        ("Баннер_Весна.png", "Баннер_Весна.png"),
        ("e\u0301.png", "\u00e9.png"),  # NFC
    ],
)
def test_sanitize_filename(raw, expected):
    assert uploads.sanitize_filename(raw) == expected


def test_long_name_is_truncated_but_keeps_the_extension():
    name = uploads.sanitize_filename("a" * 500 + ".png")

    assert len(name) == uploads.MAX_NAME_LENGTH
    assert name.endswith(".png")


@pytest.mark.parametrize("raw", ["", "   ", "...", "/", "\u202e\u200b", "../"])
def test_sanitize_filename_refuses_a_name_with_nothing_left(raw):
    with pytest.raises(InvalidUpload):
        uploads.sanitize_filename(raw)


def test_original_name_is_sanitized_in_the_result():
    checked = uploads.check_upload(SimpleUploadedFile("../x/evil\r\n.png", SAMPLES["png"]))

    assert checked.original_name == "evil .png"


# --- links --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://example.com", "https://example.com/a?b=1#c", "https://sub.example.com:8443/x"]
)
def test_http_and_https_links_pass(url):
    assert uploads.check_url(url) == url


def test_link_is_stripped():
    assert uploads.check_url("  https://example.com/x  ") == "https://example.com/x"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "file:///etc/passwd",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/file",
        "vbscript:msgbox(1)",
        "//example.com/x",
        "example.com",
        "http://",
        "https:///path",
        "https://user:secret@example.com/x",
        "https://user@example.com/x",
        "https://exa mple.com",
        "https://example.com/\nSet-Cookie: a=b",
        "",
        "   ",
        "https://example.com/" + "a" * 2000,
    ],
)
def test_other_links_are_refused(url):
    with pytest.raises(InvalidUpload):
        uploads.check_url(url)
