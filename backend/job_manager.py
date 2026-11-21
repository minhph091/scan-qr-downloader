from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import httpx

from backend.config import SESSIONS_DIR, ensure_app_dirs, load_settings, save_settings
from backend.downloader import (
    BROWSER_HEADERS,
    DownloadCancelled,
    DownloadError,
    MultiLinkDiscovered,
    download_file_async,
)
from backend.filename_utils import natural_sort_key, sanitize_filename
from backend.link_extractor import (
    ExtractedLink,
    build_track_custom_filename,
    expand_url_async,
    url_looks_like_direct_binary,
)
from backend.models import (
    AddManualUrlRequest,
    AppSettings,
    BatchItem,
    BatchState,
    BatchSummary,
    ExpandLinksRequest,
    ItemStatus,
    LanCaptureFile,
    MediaFilter,
    StartDownloadRequest,
    UpdateItemRequest,
)
from backend.scanner import extract_url_from_qr_text, scan_qr_codes

ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".gif",
    ".heic",
    ".heif",
}


class JobManager:
    def __init__(self, http_transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        ensure_app_dirs()
        self.settings: AppSettings = load_settings()
        self.http_transport = http_transport
        self._batches: dict[str, BatchState] = {}
        self.latest_batch_id: Optional[str] = None
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._download_tasks: dict[str, asyncio.Task[Any]] = {}
        self._scan_tasks: dict[str, asyncio.Task[Any]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._scan_executor = ThreadPoolExecutor(
            max_workers=max(2, min(6, os.cpu_count() or 4)),
            thread_name_prefix="qr-scan",
        )

    def shutdown(self) -> None:
        self._scan_executor.shutdown(wait=False)

    def get_settings(self) -> AppSettings:
        return self.settings

    def update_settings(self, new_settings: AppSettings) -> AppSettings:
        self.settings = save_settings(new_settings)
        for batch_id, batch in self._batches.items():
            batch.settings = self.settings.model_copy(deep=True)
            self._mark_duplicates(batch)
            self._recompute_summary(batch)
            self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))
        return self.settings

    def create_batch(self) -> BatchState:
        batch_id = uuid.uuid4().hex[:12]
        session_dir = SESSIONS_DIR / batch_id
        session_dir.mkdir(parents=True, exist_ok=True)
        batch = BatchState(
            batch_id=batch_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            settings=self.settings.model_copy(deep=True),
        )
        self._batches[batch_id] = batch
        self._subscribers[batch_id] = []
        self.latest_batch_id = batch_id
        return batch

    def get_batch(self, batch_id: str) -> Optional[BatchState]:
        batch = self._batches.get(batch_id)
        if batch:
            self._recompute_summary(batch)
        return batch

    def list_batches(self) -> list[BatchState]:
        for b in self._batches.values():
            self._recompute_summary(b)
        return list(self._batches.values())

    def clear_batch(self, batch_id: str) -> bool:
        batch = self._batches.get(batch_id)
        if not batch:
            return False
        self.cancel_downloads(batch_id)
        batch.items.clear()
        batch.is_scanning = False
        batch.is_downloading = False
        self._recompute_summary(batch)

        session_dir = SESSIONS_DIR / batch_id
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
            session_dir.mkdir(parents=True, exist_ok=True)

        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))
        return True

    async def add_uploaded_images(
        self,
        batch_id: str,
        files_payload: list[tuple[str, bytes]],
    ) -> list[BatchItem]:
        batch = self._batches.get(batch_id)
        if not batch:
            raise KeyError(f"Batch not found: {batch_id}")

        session_dir = SESSIONS_DIR / batch_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Filter valid images and sort naturally (e.g., Chuong 2 before Chuong 10)
        valid_files = [
            (fname, content)
            for fname, content in files_payload
            if Path(fname).suffix.lower() in ALLOWED_IMAGE_EXTENSIONS and len(content) > 0
        ]
        valid_files.sort(key=lambda pair: natural_sort_key(pair[0]))

        new_items: list[BatchItem] = []
        next_order = (max((item.order_index for item in batch.items), default=0)) + 1

        for orig_name, content in valid_files:
            item_id = uuid.uuid4().hex[:10]
            clean_orig = Path(orig_name).name
            safe_disk_name = f"{item_id}_{sanitize_filename(clean_orig)}"
            disk_path = session_dir / safe_disk_name
            disk_path.write_bytes(content)

            item = BatchItem(
                id=item_id,
                order_index=next_order,
                source_filename=clean_orig,
                source_image_path=str(disk_path),
                status=ItemStatus.QUEUED_SCAN,
            )
            batch.items.append(item)
            new_items.append(item)
            next_order += 1

        self._recompute_summary(batch)
        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))

        if new_items:
            task = asyncio.create_task(self._scan_items_async(batch_id, [i.id for i in new_items]))
            self._scan_tasks[batch_id] = task

        return new_items

    def add_manual_url(self, batch_id: str, req: AddManualUrlRequest) -> BatchItem:
        batch = self._batches.get(batch_id)
        if not batch:
            raise KeyError(f"Batch not found: {batch_id}")

        url = extract_url_from_qr_text(req.url) or req.url.strip()
        next_order = (max((item.order_index for item in batch.items), default=0)) + 1
        item = BatchItem(
            id=uuid.uuid4().hex[:10],
            order_index=next_order,
            source_filename=req.label or f"Manual_Link_{next_order:02d}",
            source_image_path="",
            status=ItemStatus.READY,
            raw_qr_text=req.url.strip(),
            url=url,
            custom_filename=req.custom_filename,
            scanner_stage="manual",
            selected=True,
        )
        batch.items.append(item)
        self._mark_duplicates(batch)
        self._recompute_summary(batch)
        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))
        return item

    def update_item(self, batch_id: str, item_id: str, req: UpdateItemRequest) -> BatchItem:
        batch = self._batches.get(batch_id)
        if not batch:
            raise KeyError(f"Batch not found: {batch_id}")

        item = next((i for i in batch.items if i.id == item_id), None)
        if not item:
            raise KeyError(f"Item not found: {item_id}")

        if req.url is not None:
            cleaned = req.url.strip()
            normalized = extract_url_from_qr_text(cleaned)
            item.url = normalized or (cleaned if cleaned.startswith(("http://", "https://")) else None)
            item.raw_qr_text = cleaned or None
            item.error_message = None
            if item.url:
                item.status = ItemStatus.READY
                item.selected = True
            else:
                item.status = ItemStatus.NO_QR
                item.selected = False

        if req.custom_filename is not None:
            item.custom_filename = req.custom_filename.strip() or None

        if req.selected is not None:
            item.selected = req.selected

        item.touch()
        self._mark_duplicates(batch)
        self._recompute_summary(batch)
        self._broadcast(batch_id, "item_updated", {"item": item.model_dump(mode="json"), "summary": batch.summary.model_dump(mode="json")})
        return item

    def delete_item(self, batch_id: str, item_id: str) -> bool:
        batch = self._batches.get(batch_id)
        if not batch:
            return False
        before = len(batch.items)
        batch.items = [i for i in batch.items if i.id != item_id]
        if len(batch.items) == before:
            return False
        self._reindex_items(batch)
        self._mark_duplicates(batch)
        self._recompute_summary(batch)
        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))
        return True

    async def _scan_items_async(self, batch_id: str, item_ids: list[str]) -> None:
        batch = self._batches.get(batch_id)
        if not batch:
            return

        batch.is_scanning = True
        self._recompute_summary(batch)
        self._broadcast(batch_id, "scan_state", {"is_scanning": True, "summary": batch.summary.model_dump(mode="json")})

        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(max(2, min(6, os.cpu_count() or 4)))

        async def _scan_one(item_id: str) -> None:
            item = next((i for i in batch.items if i.id == item_id), None)
            if not item or not item.source_image_path:
                return

            async with sem:
                item.status = ItemStatus.SCANNING
                item.error_message = None
                item.touch()
                self._recompute_summary(batch)
                self._broadcast(
                    batch_id,
                    "item_updated",
                    {"item": item.model_dump(mode="json"), "summary": batch.summary.model_dump(mode="json")},
                )

                try:
                    detections = await loop.run_in_executor(
                        self._scan_executor, scan_qr_codes, item.source_image_path
                    )
                except Exception as exc:
                    item.status = ItemStatus.FAILED
                    item.selected = False
                    item.error_message = f"Không đọc được file ảnh: {exc}"
                    item.touch()
                    self._recompute_summary(batch)
                    self._broadcast(
                        batch_id,
                        "item_updated",
                        {"item": item.model_dump(mode="json"), "summary": batch.summary.model_dump(mode="json")},
                    )
                    return

                if not detections:
                    item.status = ItemStatus.NO_QR
                    item.selected = False
                    item.error_message = "Không tìm thấy mã QR trong ảnh. Bạn có thể tự nhập URL."
                    item.touch()
                else:
                    first = detections[0]
                    item.raw_qr_text = first.raw_text
                    item.url = first.url
                    item.scanner_stage = first.stage
                    if first.url:
                        item.status = ItemStatus.READY
                        item.selected = True
                    else:
                        item.status = ItemStatus.NO_QR
                        item.selected = False
                        item.error_message = f"QR không chứa URL hợp lệ: {first.raw_text[:80]}"
                    item.touch()

                    # If the photo contains multiple distinct QR codes, spawn sibling items right after it
                    if len(detections) > 1:
                        insert_pos = batch.items.index(item) + 1
                        for qr_idx, det in enumerate(detections[1:], start=2):
                            extra = BatchItem(
                                id=uuid.uuid4().hex[:10],
                                order_index=item.order_index,
                                source_filename=item.source_filename,
                                source_image_path=item.source_image_path,
                                qr_index_in_image=qr_idx,
                                raw_qr_text=det.raw_text,
                                url=det.url,
                                scanner_stage=det.stage,
                                status=ItemStatus.READY if det.url else ItemStatus.NO_QR,
                                selected=bool(det.url),
                                error_message=None if det.url else f"QR #{qr_idx} không phải URL",
                            )
                            batch.items.insert(insert_pos, extra)
                            insert_pos += 1
                        self._reindex_items(batch)

                self._mark_duplicates(batch)
                self._recompute_summary(batch)
                self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))

        await asyncio.gather(*(_scan_one(iid) for iid in item_ids))

        batch.is_scanning = False
        self._mark_duplicates(batch)
        self._recompute_summary(batch)
        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))

        # Optionally probe webpage / Drive-folder links right after scanning to reveal all MP3s
        if self.settings.auto_expand_links:
            candidate_ids = [
                i.id
                for i in batch.items
                if i.status == ItemStatus.READY
                and i.url
                and not i.parent_url
                and not url_looks_like_direct_binary(i.url)
            ]
            if candidate_ids:
                await self.expand_batch_links(
                    batch_id,
                    ExpandLinksRequest(item_ids=candidate_ids),
                    silent_errors=True,
                )

        if self.settings.auto_download_after_scan and not batch.is_downloading:
            has_ready = any(i.status == ItemStatus.READY and i.selected and i.url for i in batch.items)
            if has_ready:
                await self.start_downloads(batch_id, StartDownloadRequest())

    def _replace_item_with_extracted_links(
        self,
        batch: BatchState,
        parent_item: BatchItem,
        links: list[ExtractedLink],
        resolved_url: str,
        container_title: Optional[str] = None,
        *,
        initial_status: ItemStatus = ItemStatus.READY,
    ) -> list[BatchItem]:
        """
        Replace a single container item (webpage / playlist / Drive folder) in-place with
        N individual downloadable child items while preserving book page provenance.
        """
        if parent_item not in batch.items or not links:
            return []

        idx = batch.items.index(parent_item)
        total = len(links)

        subfolder: Optional[str] = None
        if self.settings.create_subfolder_for_multi_links and total > 1:
            raw_folder = (
                Path(parent_item.source_filename).stem
                if parent_item.source_filename
                else (container_title or f"QR_{parent_item.order_index:02d}")
            )
            if parent_item.qr_index_in_image > 1:
                raw_folder = f"{raw_folder}_qr{parent_item.qr_index_in_image}"
            subfolder = sanitize_filename(raw_folder, fallback=f"chapter_{parent_item.order_index:02d}")

        stage_base = parent_item.scanner_stage or "qr"
        spawned: list[BatchItem] = []
        for track_no, link in enumerate(links, start=1):
            child = BatchItem(
                id=uuid.uuid4().hex[:10],
                order_index=parent_item.order_index,
                source_filename=parent_item.source_filename,
                source_image_path=parent_item.source_image_path,
                qr_index_in_image=parent_item.qr_index_in_image,
                parent_url=resolved_url or parent_item.url,
                track_index=track_no if total > 1 else None,
                track_total=total if total > 1 else None,
                subfolder_name=subfolder,
                status=initial_status,
                raw_qr_text=parent_item.raw_qr_text,
                url=link.url,
                adapter_name=link.source_hint,
                scanner_stage=f"{stage_base}+{link.source_hint}",
                custom_filename=build_track_custom_filename(link),
                selected=True,
            )
            spawned.append(child)

        batch.items[idx : idx + 1] = spawned
        self._reindex_items(batch)
        self._mark_duplicates(batch)
        self._recompute_summary(batch)
        return spawned

    async def expand_batch_links(
        self,
        batch_id: str,
        req: ExpandLinksRequest,
        *,
        silent_errors: bool = False,
    ) -> BatchState:
        """Probe webpage / Drive-folder URLs in a batch to split multi-MP3 pages before download."""
        batch = self._batches.get(batch_id)
        if not batch:
            raise KeyError(f"Batch not found: {batch_id}")

        if req.media_filter is not None:
            self.settings.media_filter = req.media_filter
        if req.create_subfolder_for_multi_links is not None:
            self.settings.create_subfolder_for_multi_links = req.create_subfolder_for_multi_links
        save_settings(self.settings)
        batch.settings = self.settings.model_copy(deep=True)

        explicit_ids = set(req.item_ids) if req.item_ids else None
        targets: list[BatchItem] = []
        for item in list(batch.items):
            if not item.url:
                continue
            if explicit_ids is not None:
                if item.id in explicit_ids:
                    targets.append(item)
            else:
                if item.status in (ItemStatus.READY, ItemStatus.FAILED) and not url_looks_like_direct_binary(item.url):
                    targets.append(item)

        if not targets:
            return batch

        batch.is_scanning = True
        self._recompute_summary(batch)
        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))

        timeout = httpx.Timeout(connect=10.0, read=25.0, write=15.0, pool=10.0)
        client_kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "timeout": timeout,
            "limits": httpx.Limits(max_connections=8),
        }
        if self.http_transport is not None:
            client_kwargs["transport"] = self.http_transport

        sem = asyncio.Semaphore(4)
        async with httpx.AsyncClient(**client_kwargs) as client:

            async def _probe_one(item: BatchItem) -> None:
                if not item.url:
                    return
                async with sem:
                    try:
                        res = await expand_url_async(
                            client,
                            item.url,
                            media_filter=self.settings.media_filter,
                            headers=BROWSER_HEADERS,
                        )
                    except Exception as exc:
                        if not silent_errors:
                            item.error_message = f"Không phân tích được trang web: {exc}"
                            item.touch()
                        return

                    if res.is_direct_file or not res.links:
                        return

                    if len(res.links) > 1:
                        self._replace_item_with_extracted_links(
                            batch,
                            item,
                            res.links,
                            res.resolved_url,
                            res.container_title,
                            initial_status=ItemStatus.READY,
                        )
                    elif len(res.links) == 1:
                        single = res.links[0]
                        if single.url != item.url:
                            item.parent_url = res.resolved_url or item.url
                            item.url = single.url
                            item.adapter_name = single.source_hint
                            item.custom_filename = item.custom_filename or build_track_custom_filename(single)
                            item.error_message = None
                            item.status = ItemStatus.READY
                            item.touch()
                            self._mark_duplicates(batch)
                            self._recompute_summary(batch)

                    self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))

            await asyncio.gather(*(_probe_one(it) for it in targets))

        batch.is_scanning = False
        self._recompute_summary(batch)
        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))
        return batch

    async def start_downloads(self, batch_id: str, req: StartDownloadRequest) -> BatchState:
        batch = self._batches.get(batch_id)
        if not batch:
            raise KeyError(f"Batch not found: {batch_id}")

        if batch.is_downloading:
            return batch

        # Merge request overrides into active settings & persist
        if req.output_dir is not None and req.output_dir.strip():
            self.settings.output_dir = req.output_dir.strip()
        if req.naming_mode is not None:
            self.settings.naming_mode = req.naming_mode
        if req.conflict_policy is not None:
            self.settings.conflict_policy = req.conflict_policy
        if req.media_filter is not None:
            self.settings.media_filter = req.media_filter
        if req.create_subfolder_for_multi_links is not None:
            self.settings.create_subfolder_for_multi_links = req.create_subfolder_for_multi_links
        if req.concurrency is not None:
            self.settings.concurrency = req.concurrency
        save_settings(self.settings)
        batch.settings = self.settings.model_copy(deep=True)

        target_dir = Path(self.settings.output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Select eligible items
        eligible_statuses = {ItemStatus.FAILED, ItemStatus.CANCELLED} if req.retry_failed_only else {
            ItemStatus.READY,
            ItemStatus.FAILED,
            ItemStatus.CANCELLED,
        }
        selected_ids = set(req.item_ids) if req.item_ids else None

        targets: list[BatchItem] = []
        for item in batch.items:
            if selected_ids is not None:
                if item.id in selected_ids and item.url:
                    targets.append(item)
                continue
            if not item.url:
                continue
            if req.retry_failed_only:
                if item.status in eligible_statuses:
                    targets.append(item)
            else:
                if item.selected and item.status in eligible_statuses:
                    targets.append(item)

        if not targets:
            return batch

        cancel_event = asyncio.Event()
        self._cancel_events[batch_id] = cancel_event
        batch.is_downloading = True

        for item in targets:
            item.status = ItemStatus.QUEUED_DOWNLOAD
            item.error_message = None
            item.downloaded_bytes = 0
            item.total_bytes = None
            item.progress_percent = 0.0
            item.speed_bps = 0.0
            item.touch()

        self._recompute_summary(batch)
        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))

        task = asyncio.create_task(
            self._run_download_pool(batch_id, [t.id for t in targets], target_dir, cancel_event)
        )
        self._download_tasks[batch_id] = task
        return batch

    def cancel_downloads(self, batch_id: str) -> bool:
        ev = self._cancel_events.get(batch_id)
        if ev:
            ev.set()
            return True
        return False

    async def _run_download_pool(
        self,
        batch_id: str,
        item_ids: list[str],
        output_dir: Path,
        cancel_event: asyncio.Event,
    ) -> None:
        batch = self._batches.get(batch_id)
        if not batch:
            return

        sem = asyncio.Semaphore(self.settings.concurrency)
        total_items = len(batch.items)
        timeout = httpx.Timeout(
            connect=15.0,
            read=float(self.settings.timeout_seconds),
            write=30.0,
            pool=15.0,
        )

        client_kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "timeout": timeout,
            "limits": httpx.Limits(max_connections=self.settings.concurrency * 2),
        }
        if self.http_transport is not None:
            client_kwargs["transport"] = self.http_transport

        async with httpx.AsyncClient(**client_kwargs) as client:

            async def _download_one(item_id: str) -> None:
                item = next((i for i in batch.items if i.id == item_id), None)
                if not item or not item.url:
                    return

                async with sem:
                    if cancel_event.is_set():
                        item.status = ItemStatus.CANCELLED
                        item.touch()
                        self._recompute_summary(batch)
                        self._broadcast(
                            batch_id,
                            "item_updated",
                            {"item": item.model_dump(mode="json"), "summary": batch.summary.model_dump(mode="json")},
                        )
                        return

                    item.status = ItemStatus.DOWNLOADING
                    item.error_message = None
                    item.touch()
                    self._recompute_summary(batch)
                    self._broadcast(
                        batch_id,
                        "item_updated",
                        {"item": item.model_dump(mode="json"), "summary": batch.summary.model_dump(mode="json")},
                    )

                    async def _on_progress(downloaded: int, total: Optional[int], speed: float) -> None:
                        item.downloaded_bytes = downloaded
                        item.total_bytes = total
                        item.speed_bps = speed
                        item.progress_percent = (
                            round(min(100.0, (downloaded / total) * 100.0), 1)
                            if total and total > 0
                            else 0.0
                        )
                        item.touch()
                        self._broadcast(
                            batch_id,
                            "item_progress",
                            {
                                "id": item.id,
                                "downloaded_bytes": downloaded,
                                "total_bytes": total,
                                "speed_bps": speed,
                                "progress_percent": item.progress_percent,
                            },
                        )

                    try:
                        res = await download_file_async(
                            client,
                            url=item.url,
                            output_dir=output_dir,
                            naming_mode=self.settings.naming_mode,
                            conflict_policy=self.settings.conflict_policy,
                            order_index=item.track_index or item.order_index,
                            total_items=item.track_total or len(batch.items),
                            source_image_name=item.source_filename,
                            qr_index_in_image=item.qr_index_in_image,
                            custom_filename=item.custom_filename,
                            subfolder_name=item.subfolder_name,
                            media_filter=self.settings.media_filter,
                            cancel_event=cancel_event,
                            on_progress=_on_progress,
                        )
                        item.resolved_url = res.resolved_url
                        item.adapter_name = res.adapter_name
                        item.saved_filename = res.saved_filename
                        item.saved_path = res.saved_path
                        item.content_type = res.content_type
                        item.downloaded_bytes = res.total_bytes
                        item.total_bytes = res.total_bytes
                        item.progress_percent = 100.0
                        item.speed_bps = 0.0
                        item.status = ItemStatus.SKIPPED if res.skipped else ItemStatus.COMPLETED
                        item.touch()
                    except MultiLinkDiscovered as discovered:
                        # Automatically expand this multi-MP3 page/folder in-place and download all tracks!
                        spawned = self._replace_item_with_extracted_links(
                            batch,
                            item,
                            discovered.links,
                            discovered.resolved_url,
                            discovered.container_title,
                            initial_status=ItemStatus.QUEUED_DOWNLOAD,
                        )
                        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))
                        child_ids = [c.id for c in spawned if c.status != ItemStatus.DUPLICATE]
                        if child_ids:
                            # Release current semaphore slot before fanning out children
                            return child_ids
                        return None
                    except DownloadCancelled:
                        item.status = ItemStatus.CANCELLED
                        item.speed_bps = 0.0
                        item.error_message = "Đã hủy bởi người dùng"
                        item.touch()
                    except DownloadError as exc:
                        item.status = ItemStatus.FAILED
                        item.speed_bps = 0.0
                        item.error_message = str(exc)
                        item.touch()
                    except Exception as exc:
                        item.status = ItemStatus.FAILED
                        item.speed_bps = 0.0
                        item.error_message = f"Lỗi không xác định: {exc}"
                        item.touch()

                    self._recompute_summary(batch)
                    self._broadcast(
                        batch_id,
                        "item_updated",
                        {"item": item.model_dump(mode="json"), "summary": batch.summary.model_dump(mode="json")},
                    )
                    return None

            async def _run_with_expansion(target_ids: list[str]) -> None:
                results = await asyncio.gather(*(_download_one(iid) for iid in target_ids))
                next_ids: list[str] = []
                for extra in results:
                    if extra:
                        next_ids.extend(extra)
                if next_ids:
                    await _run_with_expansion(next_ids)

            await _run_with_expansion(item_ids)

        batch.is_downloading = False
        self._recompute_summary(batch)
        self._broadcast(batch_id, "batch_updated", batch.model_dump(mode="json"))

    def export_report_csv(self, batch_id: str) -> str:
        batch = self._batches.get(batch_id)
        if not batch:
            raise KeyError(f"Batch not found: {batch_id}")

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "STT",
                "Anh goc",
                "Trang thai",
                "URL QR",
                "URL dich",
                "Adapter",
                "Ten file da luu",
                "Duong dan",
                "Kich thuoc (bytes)",
                "Loi",
            ]
        )
        for item in batch.items:
            writer.writerow(
                [
                    item.order_index,
                    item.source_filename,
                    item.status.value,
                    item.url or "",
                    item.resolved_url or "",
                    item.adapter_name or "",
                    item.saved_filename or "",
                    item.saved_path or "",
                    item.downloaded_bytes,
                    item.error_message or "",
                ]
            )
        return buf.getvalue()

    def get_capture_dir(self) -> Path:
        capture_dir = Path(self.settings.capture_dir)
        capture_dir.mkdir(parents=True, exist_ok=True)
        return capture_dir

    def list_lan_captures(self, limit: int = 100) -> list[LanCaptureFile]:
        capture_dir = self.get_capture_dir()
        files: list[Path] = [
            p
            for p in capture_dir.iterdir()
            if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
        ]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        result: list[LanCaptureFile] = []
        for p in files[:limit]:
            st = p.stat()
            result.append(
                LanCaptureFile(
                    filename=p.name,
                    saved_path=str(p),
                    size_bytes=st.st_size,
                    created_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                )
            )
        return result

    def count_lan_captures(self) -> int:
        capture_dir = self.get_capture_dir()
        return sum(
            1
            for p in capture_dir.iterdir()
            if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
        )

    def resolve_capture_file(self, filename: str) -> Optional[Path]:
        """Resolve a filename safely inside capture_dir, blocking path traversal."""
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            return None
        capture_dir = self.get_capture_dir().resolve()
        candidate = (capture_dir / filename).resolve()
        if candidate.parent != capture_dir or not candidate.is_file():
            return None
        return candidate

    def delete_lan_capture(self, filename: str) -> bool:
        target = self.resolve_capture_file(filename)
        if not target:
            return False
        target.unlink()
        if self.latest_batch_id and self.latest_batch_id in self._batches:
            self._broadcast(
                self.latest_batch_id,
                "lan_captures_changed",
                {"saved_count": self.count_lan_captures()},
            )
        return True

    async def save_lan_captures(
        self,
        files_payload: list[tuple[str, bytes]],
        *,
        prefix: Optional[str] = None,
        target_batch_id: Optional[str] = None,
    ) -> list[LanCaptureFile]:
        capture_dir = self.get_capture_dir()
        clean_prefix = sanitize_filename(prefix, fallback="") if prefix and prefix.strip() else ""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        loop = asyncio.get_running_loop()

        saved_records: list[LanCaptureFile] = []
        to_import: list[tuple[str, bytes]] = []

        for idx, (orig_name, content) in enumerate(files_payload, start=1):
            if not content:
                continue
            ext = Path(orig_name or "").suffix.lower()
            if ext not in ALLOWED_IMAGE_EXTENSIONS:
                ext = ".jpg"

            base_stem = f"{clean_prefix}_{timestamp}_{idx:02d}" if clean_prefix else f"capture_{timestamp}_{idx:02d}"
            candidate = capture_dir / sanitize_filename(f"{base_stem}{ext}")
            counter = 1
            while candidate.exists():
                candidate = capture_dir / sanitize_filename(f"{base_stem}_{counter}{ext}")
                counter += 1

            part_path = candidate.with_name(f"{candidate.name}.part")
            part_path.write_bytes(content)
            os.replace(part_path, candidate)

            # Quick QR feedback for the mobile photographer
            detected_urls: list[str] = []
            try:
                detections = await loop.run_in_executor(
                    self._scan_executor, scan_qr_codes, candidate
                )
                detected_urls = [d.url for d in detections if d.url]
            except Exception:
                detected_urls = []

            st = candidate.stat()
            record = LanCaptureFile(
                filename=candidate.name,
                saved_path=str(candidate),
                size_bytes=st.st_size,
                created_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                detected_urls=detected_urls,
            )
            saved_records.append(record)
            to_import.append((candidate.name, content))

        active_batch_id = (
            target_batch_id
            if target_batch_id and target_batch_id in self._batches
            else self.latest_batch_id
        )

        if to_import and self.settings.auto_import_lan_captures and active_batch_id:
            await self.add_uploaded_images(active_batch_id, to_import)
            for rec in saved_records:
                rec.imported_to_batch_id = active_batch_id

        if active_batch_id and active_batch_id in self._batches:
            self._broadcast(
                active_batch_id,
                "lan_captures_changed",
                {
                    "saved_count": self.count_lan_captures(),
                    "latest": [r.model_dump(mode="json") for r in saved_records],
                },
            )

        return saved_records

    async def import_captures_to_batch(self, batch_id: str) -> list[BatchItem]:
        if batch_id not in self._batches:
            raise KeyError(f"Batch not found: {batch_id}")
        capture_dir = self.get_capture_dir()
        image_paths = [
            p
            for p in capture_dir.iterdir()
            if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
        ]
        image_paths.sort(key=lambda p: natural_sort_key(p.name))
        if not image_paths:
            return []
        payload = [(p.name, p.read_bytes()) for p in image_paths]
        return await self.add_uploaded_images(batch_id, payload)

    async def subscribe(self, batch_id: str) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(batch_id, []).append(queue)
        try:
            batch = self.get_batch(batch_id)
            if batch:
                yield self._format_sse("batch_updated", batch.model_dump(mode="json"))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield self._format_sse(event["event"], event["data"])
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subs = self._subscribers.get(batch_id, [])
            if queue in subs:
                subs.remove(queue)

    def _broadcast(self, batch_id: str, event_name: str, data: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(batch_id, [])):
            try:
                q.put_nowait({"event": event_name, "data": data})
            except asyncio.QueueFull:
                pass

    @staticmethod
    def _format_sse(event_name: str, data: dict[str, Any]) -> str:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event_name}\ndata: {payload}\n\n"

    @staticmethod
    def _reindex_items(batch: BatchState) -> None:
        for idx, item in enumerate(batch.items, start=1):
            item.order_index = idx

    def _mark_duplicates(self, batch: BatchState) -> None:
        if not self.settings.deduplicate_urls:
            for item in batch.items:
                if item.status == ItemStatus.DUPLICATE and item.url:
                    item.status = ItemStatus.READY
                    item.selected = True
            return

        seen_urls: set[str] = set()
        for item in batch.items:
            if not item.url:
                continue
            norm = item.url.strip()
            if item.status in (ItemStatus.READY, ItemStatus.DUPLICATE):
                if norm in seen_urls:
                    item.status = ItemStatus.DUPLICATE
                    item.selected = False
                else:
                    seen_urls.add(norm)
                    item.status = ItemStatus.READY
            else:
                seen_urls.add(norm)

    @staticmethod
    def _recompute_summary(batch: BatchState) -> None:
        summary = BatchSummary(total=len(batch.items))
        for item in batch.items:
            st = item.status
            if st in (ItemStatus.QUEUED_SCAN, ItemStatus.SCANNING):
                summary.scanning += 1
            elif st == ItemStatus.READY:
                summary.ready += 1
            elif st == ItemStatus.NO_QR:
                summary.no_qr += 1
            elif st == ItemStatus.DUPLICATE:
                summary.duplicate += 1
            elif st in (ItemStatus.QUEUED_DOWNLOAD, ItemStatus.DOWNLOADING):
                summary.downloading += 1
            elif st == ItemStatus.COMPLETED:
                summary.completed += 1
            elif st == ItemStatus.SKIPPED:
                summary.skipped += 1
            elif st == ItemStatus.FAILED:
                summary.failed += 1
            elif st == ItemStatus.CANCELLED:
                summary.cancelled += 1
        batch.summary = summary
