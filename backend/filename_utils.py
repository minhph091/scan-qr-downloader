from __future__ import annotations

import mimetypes
import re
import unicodedata
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from backend.models import ConflictPolicy, NamingMode

# Windows reserved filenames and illegal characters (also strip tilde '~')
INVALID_WIN_CHARS_RE = re.compile(r'[<>:"/\\|?*~\x00-\x1f]')
NON_ASCII_CLEAN_RE = re.compile(r"[^\x20-\x7e]")
WHITESPACE_RE = re.compile(r"\s+")
NATURAL_SORT_RE = re.compile(r"(\d+)")
RESERVED_WIN_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Common MIME types for book companions (audio, pdf, docs, archives, video)
CUSTOM_MIME_EXTENSIONS: dict[str, str] = {
    "application/pdf": ".pdf",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/vnd.rar": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/epub+zip": ".epub",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "text/plain": ".txt",
}


def natural_sort_key(text: str) -> list[int | str]:
    """Sort strings naturally so 'page_2.jpg' comes before 'page_10.jpg'."""
    parts = NATURAL_SORT_RE.split(text.lower())
    return [int(p) if p.isdigit() else p for p in parts]


def to_ascii_safe(text: str) -> str:
    """Transliterate Vietnamese and other Unicode characters to clean ASCII."""
    replaced = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFKD", replaced)
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return NON_ASCII_CLEAN_RE.sub("_", without_marks)


def sanitize_filename(name: str, fallback: str = "file", max_length: int = 160) -> str:
    """Clean a filename so it is ASCII-safe and valid on Windows filesystems."""
    cleaned = unquote(name or "").strip()
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    cleaned = to_ascii_safe(cleaned)
    cleaned = INVALID_WIN_CHARS_RE.sub("_", cleaned)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip(" .")

    if not cleaned:
        cleaned = fallback

    stem = Path(cleaned).stem
    suffix = Path(cleaned).suffix

    if stem.upper() in RESERVED_WIN_NAMES:
        stem = f"_{stem}"

    if len(stem) + len(suffix) > max_length:
        allowed_stem_len = max(16, max_length - len(suffix))
        stem = stem[:allowed_stem_len].rstrip(" .")

    result = f"{stem}{suffix}".strip(" .")
    return result or fallback


def parse_content_disposition_filename(header_value: Optional[str]) -> Optional[str]:
    """Extract filename from Content-Disposition header (supports RFC 5987 UTF-8)."""
    if not header_value:
        return None

    # Prefer RFC 5987 filename*=UTF-8''...
    ext_match = re.search(
        r"filename\*\s*=\s*(?:UTF-8''|utf-8''|[^']*''?)([^;]+)",
        header_value,
        flags=re.IGNORECASE,
    )
    if ext_match:
        raw_name = ext_match.group(1).strip().strip('"').strip("'")
        decoded = unquote(raw_name)
        if decoded:
            return decoded

    # Standard filename="..." or filename=...
    std_match = re.search(
        r'filename\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^;]+))',
        header_value,
        flags=re.IGNORECASE,
    )
    if std_match:
        raw_name = next((g for g in std_match.groups() if g), "").strip()
        if raw_name:
            # Handle Latin-1 mojibake UTF-8 headers occasionally sent by servers
            try:
                raw_name = raw_name.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            return unquote(raw_name)

    return None


def guess_extension(content_type: Optional[str], url: str = "") -> str:
    """Guess a suitable file extension from Content-Type or URL path."""
    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime in CUSTOM_MIME_EXTENSIONS:
            return CUSTOM_MIME_EXTENSIONS[mime]
        guessed = mimetypes.guess_extension(mime)
        if guessed:
            if guessed == ".jpe":
                return ".jpg"
            return guessed

    if url:
        path = unquote(urlparse(url).path)
        suffix = Path(path).suffix.lower()
        if suffix and 1 < len(suffix) <= 8 and re.match(r"^\.[a-z0-9]+$", suffix):
            return suffix

    return ""


def determine_base_filename(
    *,
    custom_filename: Optional[str],
    content_disposition: Optional[str],
    resolved_url: str,
    original_url: str,
    content_type: Optional[str],
    source_image_name: str,
    order_index: int,
) -> str:
    """Choose the best base filename before applying naming mode prefixes."""
    candidate = None
    if custom_filename and custom_filename.strip():
        candidate = custom_filename.strip()

    if not candidate:
        candidate = parse_content_disposition_filename(content_disposition)

    if not candidate:
        for url in (resolved_url, original_url):
            path = unquote(urlparse(url).path)
            leaf = Path(path).name
            if leaf and leaf not in ("download", "uc", "view", "open", "file", "index.html"):
                if "." in leaf or len(leaf) >= 3:
                    candidate = leaf
                    break

    if not candidate:
        img_stem = Path(source_image_name).stem or f"item_{order_index:02d}"
        candidate = img_stem

    safe = sanitize_filename(candidate, fallback=f"file_{order_index:02d}")
    if not Path(safe).suffix:
        ext = guess_extension(content_type, resolved_url or original_url)
        if ext:
            safe = f"{safe}{ext}"
    return safe


def apply_naming_mode(
    base_filename: str,
    *,
    naming_mode: NamingMode,
    order_index: int,
    total_items: int,
    source_image_name: str,
    qr_index_in_image: int = 1,
) -> str:
    """Apply user-selected prefixing rule (original / index_prefix / image_prefix)."""
    pad_width = max(2, len(str(max(1, total_items))))
    safe_base = sanitize_filename(base_filename, fallback=f"file_{order_index:0{pad_width}d}")

    if naming_mode == NamingMode.ORIGINAL:
        return safe_base

    if naming_mode == NamingMode.INDEX_PREFIX:
        prefix = f"{order_index:0{pad_width}d}_"
        if safe_base.startswith(prefix):
            return safe_base
        return sanitize_filename(f"{prefix}{safe_base}")

    if naming_mode == NamingMode.IMAGE_PREFIX:
        img_stem = sanitize_filename(Path(source_image_name).stem, fallback=f"img_{order_index:0{pad_width}d}")
        if qr_index_in_image > 1:
            img_stem = f"{img_stem}_qr{qr_index_in_image}"
        if safe_base.lower().startswith(img_stem.lower()):
            return safe_base
        return sanitize_filename(f"{img_stem}_{safe_base}")

    return safe_base


def resolve_target_path(
    output_dir: Path,
    filename: str,
    conflict_policy: ConflictPolicy,
) -> tuple[Path, bool]:
    """
    Resolve final path in output_dir according to conflict_policy.
    Returns (target_path, should_skip).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / sanitize_filename(filename)

    if not target.exists():
        return target, False

    if conflict_policy == ConflictPolicy.OVERWRITE:
        return target, False

    if conflict_policy == ConflictPolicy.SKIP:
        if target.stat().st_size > 0:
            return target, True
        return target, False

    # ConflictPolicy.RENAME
    stem = target.stem
    suffix = target.suffix
    counter = 1
    while True:
        candidate = output_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate, False
        counter += 1
