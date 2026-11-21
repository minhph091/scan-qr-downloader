from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

GDRIVE_FILE_ID_PATTERNS = [
    re.compile(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]{10,})"),
    re.compile(r"drive\.google\.com/open\?.*?[?&]?id=([a-zA-Z0-9_-]{10,})"),
    re.compile(r"drive\.google\.com/uc\?.*?[?&]?id=([a-zA-Z0-9_-]{10,})"),
    re.compile(r"drive\.usercontent\.google\.com/download\?.*?[?&]?id=([a-zA-Z0-9_-]{10,})"),
    re.compile(r"docs\.google\.com/(?:document|spreadsheets|presentation)/d/([a-zA-Z0-9_-]{10,})"),
]

DOWNLOADABLE_EXT_RE = re.compile(
    r"\.(?:pdf|mp3|m4a|wav|ogg|flac|mp4|webm|zip|rar|7z|epub|docx?|xlsx?|pptx?)(?:\?|$)",
    flags=re.IGNORECASE,
)

META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\';>\s]+)',
    flags=re.IGNORECASE,
)

ASSET_LINK_PATTERNS = [
    re.compile(r'<a[^>]+href=["\']([^"\']+)["\']', flags=re.IGNORECASE),
    re.compile(r'<source[^>]+src=["\']([^"\']+)["\']', flags=re.IGNORECASE),
    re.compile(r'<audio[^>]+src=["\']([^"\']+)["\']', flags=re.IGNORECASE),
    re.compile(r'<video[^>]+src=["\']([^"\']+)["\']', flags=re.IGNORECASE),
    re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', flags=re.IGNORECASE),
    re.compile(r'<embed[^>]+src=["\']([^"\']+)["\']', flags=re.IGNORECASE),
]


@dataclass(slots=True)
class ResolvedTarget:
    download_url: str
    adapter_name: str
    suggested_filename: Optional[str] = None


def extract_google_drive_id(url: str) -> Optional[str]:
    for pattern in GDRIVE_FILE_ID_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def resolve_url_strategy(url: str) -> ResolvedTarget:
    """
    Transform known cloud-storage sharing URLs into direct download URLs.
    """
    clean_url = url.strip()
    parsed = urlparse(clean_url)
    host = parsed.netloc.lower()

    # 1. Google Docs / Sheets / Slides export
    if "docs.google.com" in host:
        doc_match = re.search(
            r"docs\.google\.com/(document|spreadsheets|presentation)/d/([a-zA-Z0-9_-]{10,})",
            clean_url,
        )
        if doc_match:
            doc_type, file_id = doc_match.group(1), doc_match.group(2)
            export_map = {
                "document": ("pdf", f"https://docs.google.com/document/d/{file_id}/export?format=pdf"),
                "spreadsheets": ("xlsx", f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"),
                "presentation": ("pdf", f"https://docs.google.com/presentation/d/{file_id}/export/pdf"),
            }
            ext, export_url = export_map[doc_type]
            return ResolvedTarget(
                download_url=export_url,
                adapter_name="google_docs",
                suggested_filename=f"{file_id}.{ext}",
            )

    # 2. Google Drive direct download
    if "drive.google.com" in host or "drive.usercontent.google.com" in host:
        file_id = extract_google_drive_id(clean_url)
        if file_id:
            return ResolvedTarget(
                download_url=f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t",
                adapter_name="google_drive",
            )

    # 3. Dropbox sharing link -> dl=1
    if "dropbox.com" in host and "dl.dropboxusercontent.com" not in host:
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["dl"] = ["1"]
        new_query = urlencode(query, doseq=True)
        direct_url = urlunparse(parsed._replace(query=new_query))
        return ResolvedTarget(download_url=direct_url, adapter_name="dropbox")

    # 4. OneDrive / SharePoint -> download=1
    if any(domain in host for domain in ("1drv.ms", "onedrive.live.com", "sharepoint.com")):
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["download"] = ["1"]
        new_query = urlencode(query, doseq=True)
        direct_url = urlunparse(parsed._replace(query=new_query))
        return ResolvedTarget(download_url=direct_url, adapter_name="onedrive")

    # 5. Default direct HTTP(S)
    return ResolvedTarget(download_url=clean_url, adapter_name="direct_http")


def extract_gdrive_confirm_url(html_text: str, fallback_file_id: Optional[str] = None) -> Optional[str]:
    """Parse Google Drive's virus-scan warning HTML form to obtain the confirmed download URL."""
    form_match = re.search(
        r'<form[^>]+id=["\']download-form["\'][^>]+action=["\']([^"\']+)["\'][^>]*>(.*?)</form>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if form_match:
        action = html.unescape(form_match.group(1))
        body = form_match.group(2)
        inputs = dict(
            re.findall(
                r'<input[^>]+name=["\']([^"\']+)["\'][^>]+value=["\']([^"\']*)["\']',
                body,
                flags=re.IGNORECASE,
            )
        )
        if inputs:
            sep = "&" if "?" in action else "?"
            return f"{action}{sep}{urlencode(inputs)}"

    confirm_match = re.search(r"confirm=([0-9A-Za-z_]+)", html_text)
    if confirm_match and fallback_file_id:
        token = confirm_match.group(1)
        return f"https://drive.usercontent.google.com/download?id={fallback_file_id}&export=download&confirm={token}"

    return None


from backend.link_extractor import extract_links_from_html
from backend.models import MediaFilter


def extract_download_link_from_html(html_text: str, page_url: str) -> Optional[str]:
    """
    When a QR code opens an intermediate publisher landing page (HTML),
    extract the first direct file link, meta-refresh redirect, or embedded cloud link.
    """
    links = extract_links_from_html(html_text, page_url, media_filter=MediaFilter.ALL)
    return links[0].url if links else None
