from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

import aiofiles
import httpx

from backend.filename_utils import (
    apply_naming_mode,
    determine_base_filename,
    resolve_target_path,
    sanitize_filename,
)
from backend.link_extractor import (
    ExtractedLink,
    build_track_custom_filename,
    expand_url_async,
    extract_links_from_html,
    extract_links_from_m3u,
    extract_page_title,
    is_gdrive_folder_url,
)
from backend.models import ConflictPolicy, MediaFilter, NamingMode
from backend.url_resolver import (
    extract_gdrive_confirm_url,
    extract_google_drive_id,
    resolve_url_strategy,
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

ProgressCallback = Callable[[int, Optional[int], float], Awaitable[None]]


class DownloadError(Exception):
    pass


class DownloadCancelled(Exception):
    pass


class MultiLinkDiscovered(Exception):
    """Raised when a single URL resolves to multiple downloadable files (playlist / webpage / Drive folder)."""

    def __init__(
        self,
        links: list[ExtractedLink],
        resolved_url: str,
        container_title: Optional[str] = None,
    ) -> None:
        super().__init__(f"Discovered {len(links)} downloadable links")
        self.links = links
        self.resolved_url = resolved_url
        self.container_title = container_title


@dataclass(slots=True)
class DownloadResult:
    resolved_url: str
    adapter_name: str
    saved_filename: str
    saved_path: str
    content_type: Optional[str]
    total_bytes: int
    skipped: bool = False


async def download_file_async(
    client: httpx.AsyncClient,
    *,
    url: str,
    output_dir: Path,
    naming_mode: NamingMode,
    conflict_policy: ConflictPolicy,
    order_index: int,
    total_items: int,
    source_image_name: str,
    qr_index_in_image: int = 1,
    custom_filename: Optional[str] = None,
    subfolder_name: Optional[str] = None,
    media_filter: MediaFilter = MediaFilter.ALL,
    cancel_event: Optional[asyncio.Event] = None,
    on_progress: Optional[ProgressCallback] = None,
    max_retries: int = 3,
) -> DownloadResult:
    """
    Resolve URL, handle cloud/landing-page redirects, detect multi-link containers,
    stream to atomic .part file, and rename upon completion.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        if cancel_event and cancel_event.is_set():
            raise DownloadCancelled("Đã hủy tải xuống")

        try:
            return await _download_once(
                client,
                url=url,
                output_dir=output_dir,
                naming_mode=naming_mode,
                conflict_policy=conflict_policy,
                order_index=order_index,
                total_items=total_items,
                source_image_name=source_image_name,
                qr_index_in_image=qr_index_in_image,
                custom_filename=custom_filename,
                subfolder_name=subfolder_name,
                media_filter=media_filter,
                cancel_event=cancel_event,
                on_progress=on_progress,
            )
        except (DownloadCancelled, MultiLinkDiscovered, DownloadError):
            raise
        except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
            last_error = exc
            if attempt < max_retries:
                await asyncio.sleep(min(1.5 * attempt, 5.0))
            else:
                raise DownloadError(f"Lỗi kết nối sau {max_retries} lần thử: {exc}") from exc

    raise DownloadError(f"Tải thất bại: {last_error}")


async def _download_once(
    client: httpx.AsyncClient,
    *,
    url: str,
    output_dir: Path,
    naming_mode: NamingMode,
    conflict_policy: ConflictPolicy,
    order_index: int,
    total_items: int,
    source_image_name: str,
    qr_index_in_image: int,
    custom_filename: Optional[str],
    subfolder_name: Optional[str],
    media_filter: MediaFilter,
    cancel_event: Optional[asyncio.Event],
    on_progress: Optional[ProgressCallback],
) -> DownloadResult:
    # Handle Google Drive folder URLs upfront
    if is_gdrive_folder_url(url):
        expanded = await expand_url_async(
            client,
            url,
            media_filter=media_filter,
            headers=BROWSER_HEADERS,
        )
        if not expanded.links:
            raise DownloadError("Thư mục Google Drive trống hoặc chưa bật quyền chia sẻ công khai.")
        if len(expanded.links) > 1:
            raise MultiLinkDiscovered(
                links=expanded.links,
                resolved_url=expanded.resolved_url,
                container_title=expanded.container_title,
            )
        url = expanded.links[0].url
        custom_filename = custom_filename or build_track_custom_filename(expanded.links[0])

    resolved = resolve_url_strategy(url)
    current_url = resolved.download_url
    adapter_name = resolved.adapter_name
    suggested_filename = custom_filename or resolved.suggested_filename

    effective_output_dir = (
        output_dir / sanitize_filename(subfolder_name, fallback="chapter")
        if subfolder_name and subfolder_name.strip()
        else output_dir
    )

    # Allow up to 3 hops when resolving intermediate HTML pages (GDrive warning / publisher page)
    for hop in range(3):
        if cancel_event and cancel_event.is_set():
            raise DownloadCancelled("Đã hủy tải xuống")

        async with client.stream("GET", current_url, headers=BROWSER_HEADERS) as response:
            if response.status_code >= 400:
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.RemoteProtocolError(f"HTTP {response.status_code}")
                raise DownloadError(f"Server trả về lỗi HTTP {response.status_code}")

            final_url = str(response.url)
            content_type = response.headers.get("content-type", "")
            content_disposition = response.headers.get("content-disposition")

            # Shortener redirected to a Google Drive folder
            if is_gdrive_folder_url(final_url):
                expanded = await expand_url_async(
                    client,
                    final_url,
                    media_filter=media_filter,
                    headers=BROWSER_HEADERS,
                )
                if not expanded.links:
                    raise DownloadError("Thư mục Google Drive trống hoặc chưa chia sẻ công khai.")
                if len(expanded.links) > 1:
                    raise MultiLinkDiscovered(
                        links=expanded.links,
                        resolved_url=expanded.resolved_url,
                        container_title=expanded.container_title,
                    )
                remapped = resolve_url_strategy(expanded.links[0].url)
                current_url = remapped.download_url
                adapter_name = f"gdrive_folder->{remapped.adapter_name}"
                suggested_filename = suggested_filename or build_track_custom_filename(expanded.links[0])
                continue

            # If the URL redirected to a known cloud file host (e.g., bit.ly -> drive.google.com/file/d/...)
            if hop == 0 and final_url != current_url:
                remapped = resolve_url_strategy(final_url)
                if remapped.adapter_name != "direct_http" and remapped.download_url != final_url:
                    current_url = remapped.download_url
                    adapter_name = remapped.adapter_name
                    suggested_filename = suggested_filename or remapped.suggested_filename
                    continue

            ct_lower = content_type.lower()
            is_html = "text/html" in ct_lower and not content_disposition
            is_m3u = (
                "mpegurl" in ct_lower
                or urlparse(final_url).path.lower().endswith((".m3u", ".m3u8"))
            )

            if is_html or is_m3u:
                chunks: list[bytes] = []
                read_len = 0
                async for chunk in response.aiter_bytes(chunk_size=16384):
                    chunks.append(chunk)
                    read_len += len(chunk)
                    if read_len >= 1024 * 1024:
                        break
                body_text = b"".join(chunks).decode("utf-8", errors="ignore")

                if is_m3u:
                    m3u_links = extract_links_from_m3u(body_text, final_url, media_filter=media_filter)
                    if len(m3u_links) > 1:
                        raise MultiLinkDiscovered(links=m3u_links, resolved_url=final_url)
                    if len(m3u_links) == 1:
                        remapped = resolve_url_strategy(m3u_links[0].url)
                        current_url = remapped.download_url
                        adapter_name = f"m3u->{remapped.adapter_name}"
                        suggested_filename = suggested_filename or build_track_custom_filename(m3u_links[0])
                        continue
                    raise DownloadError("Playlist M3U không chứa link tải hợp lệ.")

                # Google Drive virus-scan warning page
                if (
                    adapter_name == "google_drive"
                    or "drive.google.com" in final_url
                    or "googleusercontent.com" in final_url
                ):
                    gdrive_id = extract_google_drive_id(final_url) or extract_google_drive_id(url)
                    confirm_url = extract_gdrive_confirm_url(body_text, fallback_file_id=gdrive_id)
                    if confirm_url and confirm_url != current_url:
                        current_url = confirm_url
                        adapter_name = "google_drive"
                        continue

                if hop < 2:
                    extracted = extract_links_from_html(body_text, final_url, media_filter=media_filter)
                    if len(extracted) > 1:
                        raise MultiLinkDiscovered(
                            links=extracted,
                            resolved_url=final_url,
                            container_title=extract_page_title(body_text),
                        )
                    if len(extracted) == 1 and extracted[0].url != current_url:
                        remapped = resolve_url_strategy(extracted[0].url)
                        current_url = remapped.download_url
                        adapter_name = f"webpage->{remapped.adapter_name}"
                        suggested_filename = suggested_filename or build_track_custom_filename(extracted[0])
                        continue

                raise DownloadError(
                    "Link trỏ tới trang web (HTML) và không tìm thấy file tải trực tiếp. "
                    "Hãy mở link kiểm tra hoặc chỉnh sửa URL."
                )

            # Determine final target path
            base_name = determine_base_filename(
                custom_filename=suggested_filename,
                content_disposition=content_disposition,
                resolved_url=final_url,
                original_url=url,
                content_type=content_type,
                source_image_name=source_image_name,
                order_index=order_index,
            )
            formatted_name = apply_naming_mode(
                base_name,
                naming_mode=naming_mode,
                order_index=order_index,
                total_items=total_items,
                source_image_name=source_image_name,
                qr_index_in_image=qr_index_in_image,
            )
            target_path, should_skip = resolve_target_path(
                output_dir=effective_output_dir,
                filename=formatted_name,
                conflict_policy=conflict_policy,
            )

            if should_skip:
                existing_size = target_path.stat().st_size if target_path.exists() else 0
                if on_progress:
                    await on_progress(existing_size, existing_size, 0.0)
                return DownloadResult(
                    resolved_url=final_url,
                    adapter_name=adapter_name,
                    saved_filename=target_path.name,
                    saved_path=str(target_path),
                    content_type=content_type or None,
                    total_bytes=existing_size,
                    skipped=True,
                )

            total_bytes: Optional[int] = None
            cl_header = response.headers.get("content-length")
            if cl_header and cl_header.isdigit():
                total_bytes = int(cl_header)

            part_path = target_path.with_name(f"{target_path.name}.part")
            downloaded = 0
            start_time = time.monotonic()
            last_report = start_time

            try:
                async with aiofiles.open(part_path, "wb") as fp:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if cancel_event and cancel_event.is_set():
                            raise DownloadCancelled("Đã hủy tải xuống")
                        if not chunk:
                            continue
                        await fp.write(chunk)
                        downloaded += len(chunk)

                        now = time.monotonic()
                        if on_progress and (now - last_report >= 0.12):
                            elapsed = max(now - start_time, 0.001)
                            speed = downloaded / elapsed
                            await on_progress(downloaded, total_bytes, speed)
                            last_report = now

                if downloaded == 0:
                    raise DownloadError("File tải về bị rỗng (0 bytes)")

                # Atomic rename .part -> final filename
                os.replace(part_path, target_path)
            except Exception:
                try:
                    if part_path.exists():
                        part_path.unlink()
                except OSError:
                    pass
                raise

            elapsed = max(time.monotonic() - start_time, 0.001)
            if on_progress:
                await on_progress(downloaded, total_bytes or downloaded, downloaded / elapsed)

            return DownloadResult(
                resolved_url=final_url,
                adapter_name=adapter_name,
                saved_filename=target_path.name,
                saved_path=str(target_path),
                content_type=content_type or None,
                total_bytes=downloaded,
                skipped=False,
            )

    raise DownloadError("Quá nhiều lượt chuyển hướng trang HTML mà không tới được file tải.")
