from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

import httpx

from backend.filename_utils import natural_sort_key, sanitize_filename
from backend.models import MediaFilter

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".wma", ".opus"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".epub",
    ".txt",
    ".zip",
    ".rar",
    ".7z",
}
ALL_DOWNLOADABLE_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | DOCUMENT_EXTENSIONS

EXT_PATTERN = "|".join(ext.lstrip(".") for ext in sorted(ALL_DOWNLOADABLE_EXTENSIONS))

DOWNLOADABLE_URL_RE = re.compile(
    rf"(?P<url>(?:https?:)?//[^\s\"'<>\\]+?\.(?:{EXT_PATTERN})(?:\?[^\s\"'<>\\]*)?)",
    flags=re.IGNORECASE,
)

RELATIVE_MEDIA_QUOTED_RE = re.compile(
    rf'["\'](?P<path>(?:/|\./|\.\./|[a-zA-Z0-9_-]+/)[^"\'<>\s\\]+?\.(?:{EXT_PATTERN})(?:\?[^"\'<>\s\\]*)?)["\']',
    flags=re.IGNORECASE,
)

META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\';>\s]+)',
    flags=re.IGNORECASE,
)

GDRIVE_FOLDER_RE = re.compile(
    r"drive\.google\.com/(?:drive/(?:u/\d+/)?folders/|folderview\?.*?[?&]?id=)([a-zA-Z0-9_-]{10,})",
    flags=re.IGNORECASE,
)

CLOUD_FILE_URL_RE = re.compile(
    r"https?://(?:drive\.google\.com/(?:file/d/[a-zA-Z0-9_-]{10,}[^\s\"'<>]*|open\?[^\s\"'<>]*id=[a-zA-Z0-9_-]{10,}[^\s\"'<>]*|uc\?[^\s\"'<>]*id=[a-zA-Z0-9_-]{10,}[^\s\"'<>]*)|"
    r"docs\.google\.com/(?:document|spreadsheets|presentation)/d/[a-zA-Z0-9_-]{10,}[^\s\"'<>]*|"
    r"(?:www\.)?dropbox\.com/(?:s|scl/fi)/[^\s\"'<>]+|"
    r"(?:1drv\.ms|onedrive\.live\.com)/[^\s\"'<>]+)",
    flags=re.IGNORECASE,
)

# HTML tags that commonly embed downloadable links or audio players
ANCHOR_RE = re.compile(
    r'<a\b(?P<attrs>[^>]*)>(?P<inner>.*?)</a>',
    flags=re.IGNORECASE | re.DOTALL,
)
MEDIA_TAG_RE = re.compile(
    r'<(?:audio|source|video|iframe|embed|div|li|button|span)\b(?P<attrs>[^>]*)>',
    flags=re.IGNORECASE,
)
ATTR_PAIR_RE = re.compile(
    r'([a-zA-Z0-9_:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
    flags=re.IGNORECASE,
)
HTML_TAG_STRIP_RE = re.compile(r"<[^>]+>")

# JSON/JS object snippets pairing a title/name with a media URL
JSON_TRACK_OBJ_RE = re.compile(
    r'\{[^{}]{0,600}?"(?:title|name|label|caption|track)"\s*:\s*"(?P<title>[^"]{1,140})"[^{}]{0,600}?"(?:src|url|file|mp3|audio|link|href|path)"\s*:\s*"(?P<url>[^"]{4,500})"[^{}]{0,200}?\}',
    flags=re.IGNORECASE | re.DOTALL,
)
JSON_TRACK_OBJ_REV_RE = re.compile(
    r'\{[^{}]{0,600}?"(?:src|url|file|mp3|audio|link|href|path)"\s*:\s*"(?P<url>[^"]{4,500})"[^{}]{0,600}?"(?:title|name|label|caption|track)"\s*:\s*"(?P<title>[^"]{1,140})"[^{}]{0,200}?\}',
    flags=re.IGNORECASE | re.DOTALL,
)

# Embedded Google Drive folder view entries
GDRIVE_EMBED_ENTRY_RE = re.compile(
    r'<a[^>]+href=["\'](https://drive\.google\.com/file/d/([a-zA-Z0-9_-]{10,})[^"\']*)["\'][^>]*>.*?'
    r'<div[^>]+class=["\'][^"\']*flip-entry-title[^"\']*["\'][^>]*>(.*?)</div>',
    flags=re.IGNORECASE | re.DOTALL,
)

URL_ATTR_NAMES = (
    "href",
    "src",
    "data-src",
    "data-audio",
    "data-mp3",
    "data-url",
    "data-file",
    "data-track",
    "data-sound",
    "data-download",
    "data-href",
)
TITLE_ATTR_NAMES = ("download", "data-title", "data-name", "title", "aria-label")


@dataclass(slots=True)
class ExtractedLink:
    url: str
    title: Optional[str] = None
    category: str = "other"  # "audio", "video", "document", "cloud", "other"
    source_hint: str = "html"


@dataclass(slots=True)
class ExpansionResult:
    original_url: str
    resolved_url: str
    is_direct_file: bool
    links: list[ExtractedLink]
    container_title: Optional[str] = None


def is_gdrive_folder_url(url: str) -> Optional[str]:
    match = GDRIVE_FOLDER_RE.search(url or "")
    return match.group(1) if match else None


def url_looks_like_direct_binary(url: str) -> bool:
    """Quick heuristic to avoid probing URLs that already clearly point to a single file."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "drive.google.com" in host and "/file/d/" in parsed.path:
        return True
    if "drive.usercontent.google.com" in host:
        return True
    suffix = Path(unquote(parsed.path)).suffix.lower()
    return suffix in ALL_DOWNLOADABLE_EXTENSIONS


def classify_link(url: str, title: Optional[str] = None) -> str:
    """Classify a candidate URL by extension or cloud host."""
    for text in (unquote(urlparse(url).path), title or ""):
        suffix = Path(text).suffix.lower()
        if suffix in AUDIO_EXTENSIONS:
            return "audio"
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        if suffix in DOCUMENT_EXTENSIONS:
            return "document"

    lower_url = url.lower()
    if any(
        token in lower_url
        for token in (
            "drive.google.com/file/d/",
            "drive.google.com/open?",
            "drive.google.com/uc?",
            "docs.google.com/",
            "dropbox.com/s/",
            "dropbox.com/scl/",
            "1drv.ms/",
            "onedrive.live.com/",
        )
    ):
        return "cloud"

    return "other"


def _matches_media_filter(link: ExtractedLink, media_filter: MediaFilter) -> bool:
    if media_filter == MediaFilter.ALL:
        return link.category in ("audio", "video", "document", "cloud")
    if media_filter == MediaFilter.AUDIO_ONLY:
        if link.category == "audio":
            return True
        # Keep cloud links if filename/title hints audio or has no conflicting doc extension
        if link.category == "cloud":
            if link.title:
                ext = Path(link.title).suffix.lower()
                if ext and ext not in AUDIO_EXTENSIONS:
                    return False
            return True
        return False
    if media_filter == MediaFilter.DOCUMENTS_ONLY:
        if link.category == "document":
            return True
        if link.category == "cloud":
            if link.title:
                ext = Path(link.title).suffix.lower()
                if ext and ext not in DOCUMENT_EXTENSIONS:
                    return False
            return True
        return False
    return True


GENERIC_LINK_LABELS = {
    "download",
    "download now",
    "download file",
    "download mp3",
    "download audio",
    "download pdf",
    "tải về",
    "tải xuống",
    "tải file",
    "tải mp3",
    "tải audio",
    "tải pdf",
    "play",
    "nghe",
    "nghe ngay",
    "click here",
    "mp3",
    "audio",
    "pdf",
    "file",
}


def _clean_title(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    text = HTML_TAG_STRIP_RE.sub(" ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) < 2:
        return None
    if text.lower() in GENERIC_LINK_LABELS:
        return None
    return text[:120]


def _parse_attrs(attr_str: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in ATTR_PAIR_RE.finditer(attr_str):
        key = m.group(1).lower()
        val = m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))
        attrs[key] = html.unescape((val or "").strip())
    return attrs


def _normalize_candidate_url(raw_url: str, page_url: str) -> Optional[str]:
    u = html.unescape((raw_url or "").strip())
    if not u or u.startswith(("#", "javascript:", "mailto:", "tel:", "data:", "blob:")):
        return None
    # Unescape common JS/JSON escaped slashes
    u = u.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/").replace("\\u0026", "&")
    abs_url = urljoin(page_url, u)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return abs_url


def extract_page_title(html_text: str) -> Optional[str]:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    title = _clean_title(m.group(1))
    return sanitize_filename(title, fallback="") if title else None


def extract_links_from_html(
    html_text: str,
    page_url: str,
    media_filter: MediaFilter = MediaFilter.ALL,
) -> list[ExtractedLink]:
    """
    Extract ALL downloadable media/document/cloud links from a webpage HTML,
    preserving page order and deduplicating while recovering human-readable track titles.
    """
    if not html_text:
        return []

    found: dict[str, ExtractedLink] = {}

    def _add(raw_url: str, title: Optional[str], source_hint: str) -> None:
        abs_url = _normalize_candidate_url(raw_url, page_url)
        if not abs_url:
            return
        cleaned_title = _clean_title(title)
        cat = classify_link(abs_url, cleaned_title)
        if cat == "other":
            return
        link = ExtractedLink(url=abs_url, title=cleaned_title, category=cat, source_hint=source_hint)
        if not _matches_media_filter(link, media_filter):
            return
        existing = found.get(abs_url)
        if existing is None:
            found[abs_url] = link
        elif not existing.title and cleaned_title:
            existing.title = cleaned_title

    # 1. Structured JSON/JS playlist objects (captures nice track titles + URLs)
    unescaped_text = (
        html_text.replace("\\/", "/")
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\u0026", "&")
    )
    for pattern in (JSON_TRACK_OBJ_RE, JSON_TRACK_OBJ_REV_RE):
        for m in pattern.finditer(unescaped_text):
            _add(m.group("url"), m.group("title"), "js_playlist")

    # 2. Anchor tags (<a href="..." title="...">Track 01</a>)
    for m in ANCHOR_RE.finditer(html_text):
        attrs = _parse_attrs(m.group("attrs"))
        title = None
        for t_attr in TITLE_ATTR_NAMES:
            if attrs.get(t_attr):
                title = attrs[t_attr]
                break
        if not title:
            title = m.group("inner")
        for u_attr in URL_ATTR_NAMES:
            if attrs.get(u_attr):
                _add(attrs[u_attr], title, "anchor")

    # 3. Media & custom player tags (<audio>, <source>, <video>, <iframe>, <div data-mp3=...>)
    for m in MEDIA_TAG_RE.finditer(html_text):
        attrs = _parse_attrs(m.group("attrs"))
        title = next((attrs[k] for k in TITLE_ATTR_NAMES if attrs.get(k)), None)
        for u_attr in URL_ATTR_NAMES:
            if attrs.get(u_attr):
                _add(attrs[u_attr], title, "media_tag")

    # 4. Raw absolute media URLs anywhere in scripts / inline JS / JSON
    for m in DOWNLOADABLE_URL_RE.finditer(unescaped_text):
        _add(m.group("url"), None, "script_url")

    # 5. Relative quoted media URLs inside JS arrays/configs (e.g. "/audio/track01.mp3")
    for m in RELATIVE_MEDIA_QUOTED_RE.finditer(unescaped_text):
        _add(m.group("path"), None, "script_rel_url")

    # 6. Embedded cloud links (Google Drive / Dropbox / OneDrive) in scripts/iframes
    for m in CLOUD_FILE_URL_RE.finditer(unescaped_text):
        _add(m.group(0), None, "cloud_embed")

    # 7. Meta-refresh fallback if nothing else was found
    if not found:
        meta_match = META_REFRESH_RE.search(html_text)
        if meta_match:
            target = _normalize_candidate_url(meta_match.group(1), page_url)
            if target:
                found[target] = ExtractedLink(
                    url=target,
                    title=None,
                    category=classify_link(target),
                    source_hint="meta_refresh",
                )

    return list(found.values())


def extract_links_from_gdrive_embedded_html(
    html_text: str,
    media_filter: MediaFilter = MediaFilter.ALL,
) -> list[ExtractedLink]:
    """Parse Google Drive public `embeddedfolderview` HTML to list all files in a folder."""
    entries: list[ExtractedLink] = []
    seen: set[str] = set()

    for m in GDRIVE_EMBED_ENTRY_RE.finditer(html_text):
        file_url = html.unescape(m.group(1).strip())
        title = _clean_title(m.group(3))
        if file_url in seen:
            continue
        seen.add(file_url)
        cat = classify_link(file_url, title)
        if cat == "other":
            cat = "cloud"
        link = ExtractedLink(url=file_url, title=title, category=cat, source_hint="gdrive_folder")
        if _matches_media_filter(link, media_filter):
            entries.append(link)

    # Sort naturally by filename/title (Track 2 before Track 10)
    entries.sort(key=lambda item: natural_sort_key(item.title or item.url))
    return entries


def extract_links_from_m3u(
    text: str,
    page_url: str,
    media_filter: MediaFilter = MediaFilter.ALL,
) -> list[ExtractedLink]:
    """Parse an M3U audio playlist into downloadable track links."""
    links: list[ExtractedLink] = []
    seen: set[str] = set()
    pending_title: Optional[str] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            parts = line.split(",", 1)
            if len(parts) == 2:
                pending_title = _clean_title(parts[1])
            continue
        if line.startswith("#"):
            continue
        abs_url = _normalize_candidate_url(line, page_url)
        if abs_url and abs_url not in seen:
            seen.add(abs_url)
            cat = classify_link(abs_url, pending_title)
            if cat == "other":
                cat = "audio"
            link = ExtractedLink(url=abs_url, title=pending_title, category=cat, source_hint="m3u")
            if _matches_media_filter(link, media_filter):
                links.append(link)
        pending_title = None

    return links


def build_track_custom_filename(link: ExtractedLink) -> Optional[str]:
    """
    Prefer the true original filename from the URL / server Content-Disposition.
    Only fall back to link.title when the URL path itself has no real filename
    (e.g., Google Drive /file/d/<ID>/view where the folder entry title IS the filename).
    """
    url_path = unquote(urlparse(link.url).path)
    url_leaf = Path(url_path).name
    url_ext = Path(url_path).suffix.lower()
    if url_leaf and url_ext in ALL_DOWNLOADABLE_EXTENSIONS:
        # Keep original filename from server/URL; do not overwrite with webpage text like "ô 2~3"
        return None

    if not link.title:
        return None
    safe = sanitize_filename(link.title, fallback="")
    if not safe:
        return None
    if Path(safe).suffix:
        return safe
    if link.category == "audio":
        return f"{safe}.mp3"
    return safe


async def expand_url_async(
    client: httpx.AsyncClient,
    url: str,
    *,
    media_filter: MediaFilter = MediaFilter.ALL,
    headers: Optional[dict[str, str]] = None,
) -> ExpansionResult:
    """
    Inspect a URL to discover whether it is a direct file OR a container (webpage / playlist /
    Google Drive folder) holding multiple downloadable files.
    """
    clean_url = url.strip()

    # 1. Google Drive public folder
    folder_id = is_gdrive_folder_url(clean_url)
    if folder_id:
        embed_url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
        resp = await client.get(embed_url, headers=headers)
        if resp.status_code < 400:
            links = extract_links_from_gdrive_embedded_html(resp.text, media_filter=media_filter)
            title = extract_page_title(resp.text)
            return ExpansionResult(
                original_url=clean_url,
                resolved_url=str(resp.url),
                is_direct_file=False,
                links=links,
                container_title=title,
            )

    # 2. Stream headers first so we never download giant binaries during link probing
    async with client.stream("GET", clean_url, headers=headers) as resp:
        if resp.status_code >= 400:
            return ExpansionResult(
                original_url=clean_url,
                resolved_url=str(resp.url),
                is_direct_file=False,
                links=[],
            )

        final_url = str(resp.url)

        # Redirected to a Google Drive folder (e.g. bit.ly -> drive.google.com/drive/folders/...)
        redirected_folder_id = is_gdrive_folder_url(final_url)
        if redirected_folder_id and redirected_folder_id != folder_id:
            embed_url = f"https://drive.google.com/embeddedfolderview?id={redirected_folder_id}#list"
            f_resp = await client.get(embed_url, headers=headers)
            if f_resp.status_code < 400:
                links = extract_links_from_gdrive_embedded_html(f_resp.text, media_filter=media_filter)
                return ExpansionResult(
                    original_url=clean_url,
                    resolved_url=final_url,
                    is_direct_file=False,
                    links=links,
                    container_title=extract_page_title(f_resp.text),
                )

        content_type = (resp.headers.get("content-type") or "").lower()
        content_disposition = resp.headers.get("content-disposition")

        is_html = "text/html" in content_type and not content_disposition
        is_m3u = (
            "mpegurl" in content_type
            or urlparse(final_url).path.lower().endswith((".m3u", ".m3u8"))
        )
        is_json = "application/json" in content_type and not content_disposition

        if not (is_html or is_m3u or is_json):
            return ExpansionResult(
                original_url=clean_url,
                resolved_url=final_url,
                is_direct_file=True,
                links=[],
            )

        chunks: list[bytes] = []
        read_len = 0
        async for chunk in resp.aiter_bytes(chunk_size=16384):
            chunks.append(chunk)
            read_len += len(chunk)
            if read_len >= 1024 * 1024:  # Cap at 1 MB for safety
                break
        body_text = b"".join(chunks).decode("utf-8", errors="ignore")

    if is_m3u:
        links = extract_links_from_m3u(body_text, final_url, media_filter=media_filter)
        return ExpansionResult(
            original_url=clean_url,
            resolved_url=final_url,
            is_direct_file=False,
            links=links,
        )

    if is_json:
        links = _extract_links_from_json_text(body_text, final_url, media_filter=media_filter)
        return ExpansionResult(
            original_url=clean_url,
            resolved_url=final_url,
            is_direct_file=False,
            links=links,
        )

    # HTML page
    links = extract_links_from_html(body_text, final_url, media_filter=media_filter)
    return ExpansionResult(
        original_url=clean_url,
        resolved_url=final_url,
        is_direct_file=False,
        links=links,
        container_title=extract_page_title(body_text),
    )


def _extract_links_from_json_text(
    json_text: str,
    page_url: str,
    media_filter: MediaFilter = MediaFilter.ALL,
) -> list[ExtractedLink]:
    """Walk an arbitrary JSON playlist payload to extract media URLs and nearby titles."""
    try:
        data = json.loads(json_text)
    except Exception:
        return extract_links_from_html(json_text, page_url, media_filter=media_filter)

    found: dict[str, ExtractedLink] = {}

    def _walk(node: object) -> None:
        if isinstance(node, list):
            for elem in node:
                _walk(elem)
        elif isinstance(node, dict):
            title = None
            for k in ("title", "name", "label", "caption", "filename"):
                if isinstance(node.get(k), str) and node[k].strip():
                    title = node[k].strip()
                    break
            for k, v in node.items():
                if isinstance(v, str):
                    abs_url = _normalize_candidate_url(v, page_url)
                    if abs_url:
                        cat = classify_link(abs_url, title)
                        if cat != "other":
                            link = ExtractedLink(url=abs_url, title=_clean_title(title), category=cat, source_hint="json_api")
                            if _matches_media_filter(link, media_filter) and abs_url not in found:
                                found[abs_url] = link
                elif isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(node, str):
            abs_url = _normalize_candidate_url(node, page_url)
            if abs_url:
                cat = classify_link(abs_url)
                if cat != "other":
                    link = ExtractedLink(url=abs_url, title=None, category=cat, source_hint="json_api")
                    if _matches_media_filter(link, media_filter) and abs_url not in found:
                        found[abs_url] = link

    _walk(data)
    return list(found.values())
