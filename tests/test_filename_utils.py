from pathlib import Path

from backend.filename_utils import (
    apply_naming_mode,
    determine_base_filename,
    natural_sort_key,
    parse_content_disposition_filename,
    resolve_target_path,
    sanitize_filename,
)
from backend.models import ConflictPolicy, NamingMode


def test_natural_sort_order():
    names = ["Chuong 10.jpg", "Chuong 2.jpg", "Chuong 1.jpg", "Chuong 11.jpg"]
    assert sorted(names, key=natural_sort_key) == [
        "Chuong 1.jpg",
        "Chuong 2.jpg",
        "Chuong 10.jpg",
        "Chuong 11.jpg",
    ]


def test_sanitize_filename_windows_safe():
    assert sanitize_filename('Bai: 01/02?*"<>|.mp3') == "Bai_ 01_02______.mp3"
    assert sanitize_filename("ô 2~3.mp3") == "o 2_3.mp3"
    assert sanitize_filename("Chương 1 - Đọc hiểu.pdf") == "Chuong 1 - Doc hieu.pdf"
    assert sanitize_filename("CON.txt") == "_CON.txt"
    assert sanitize_filename("   ...  ", fallback="chapter") == "chapter"


def test_parse_content_disposition_utf8_and_standard():
    rfc5987 = "attachment; filename*=UTF-8''Ch%C6%B0%C6%A1ng%2001%20-%20Audio.mp3"
    assert parse_content_disposition_filename(rfc5987) == "Chương 01 - Audio.mp3"

    standard = 'attachment; filename="Lesson_02.pdf"'
    assert parse_content_disposition_filename(standard) == "Lesson_02.pdf"


def test_determine_and_apply_naming_modes():
    base = determine_base_filename(
        custom_filename=None,
        content_disposition='attachment; filename="Track 1.mp3"',
        resolved_url="https://example.com/download",
        original_url="https://example.com/short",
        content_type="audio/mpeg",
        source_image_name="Trang_03.jpg",
        order_index=3,
    )
    assert base == "Track 1.mp3"

    assert (
        apply_naming_mode(
            base,
            naming_mode=NamingMode.INDEX_PREFIX,
            order_index=3,
            total_items=25,
            source_image_name="Trang_03.jpg",
        )
        == "03_Track 1.mp3"
    )
    assert (
        apply_naming_mode(
            base,
            naming_mode=NamingMode.IMAGE_PREFIX,
            order_index=3,
            total_items=25,
            source_image_name="Trang_03.jpg",
        )
        == "Trang_03_Track 1.mp3"
    )
    assert (
        apply_naming_mode(
            base,
            naming_mode=NamingMode.ORIGINAL,
            order_index=3,
            total_items=25,
            source_image_name="Trang_03.jpg",
        )
        == "Track 1.mp3"
    )


def test_resolve_target_path_policies(tmp_path: Path):
    existing = tmp_path / "01_audio.mp3"
    existing.write_bytes(b"abc123")

    renamed, skip_r = resolve_target_path(tmp_path, "01_audio.mp3", ConflictPolicy.RENAME)
    assert not skip_r
    assert renamed.name == "01_audio (1).mp3"

    skipped, skip_s = resolve_target_path(tmp_path, "01_audio.mp3", ConflictPolicy.SKIP)
    assert skip_s
    assert skipped == existing

    overwritten, skip_o = resolve_target_path(tmp_path, "01_audio.mp3", ConflictPolicy.OVERWRITE)
    assert not skip_o
    assert overwritten == existing
