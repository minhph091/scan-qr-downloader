from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ItemStatus(str, Enum):
    QUEUED_SCAN = "queued_scan"
    SCANNING = "scanning"
    READY = "ready"
    NO_QR = "no_qr"
    DUPLICATE = "duplicate"
    QUEUED_DOWNLOAD = "queued_download"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NamingMode(str, Enum):
    ORIGINAL = "original"
    INDEX_PREFIX = "index_prefix"
    IMAGE_PREFIX = "image_prefix"


class ConflictPolicy(str, Enum):
    RENAME = "rename"
    SKIP = "skip"
    OVERWRITE = "overwrite"


class MediaFilter(str, Enum):
    ALL = "all"
    AUDIO_ONLY = "audio_only"
    DOCUMENTS_ONLY = "documents_only"


class AppSettings(BaseModel):
    output_dir: str = Field(default="")
    capture_dir: str = Field(default="")
    auto_import_lan_captures: bool = Field(default=True)
    naming_mode: NamingMode = Field(default=NamingMode.ORIGINAL)
    conflict_policy: ConflictPolicy = Field(default=ConflictPolicy.RENAME)
    media_filter: MediaFilter = Field(default=MediaFilter.ALL)
    auto_expand_links: bool = Field(default=True)
    create_subfolder_for_multi_links: bool = Field(default=False)
    concurrency: int = Field(default=4, ge=1, le=12)
    deduplicate_urls: bool = Field(default=True)
    auto_download_after_scan: bool = Field(default=False)
    timeout_seconds: int = Field(default=60, ge=10, le=600)


class BatchItem(BaseModel):
    id: str
    order_index: int
    source_filename: str
    source_image_path: str
    qr_index_in_image: int = 1
    parent_url: Optional[str] = None
    track_index: Optional[int] = None
    track_total: Optional[int] = None
    subfolder_name: Optional[str] = None
    status: ItemStatus = ItemStatus.QUEUED_SCAN
    raw_qr_text: Optional[str] = None
    url: Optional[str] = None
    resolved_url: Optional[str] = None
    adapter_name: Optional[str] = None
    scanner_stage: Optional[str] = None
    custom_filename: Optional[str] = None
    saved_filename: Optional[str] = None
    saved_path: Optional[str] = None
    content_type: Optional[str] = None
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    speed_bps: float = 0.0
    progress_percent: float = 0.0
    selected: bool = True
    error_message: Optional[str] = None
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()


class BatchSummary(BaseModel):
    total: int = 0
    scanning: int = 0
    ready: int = 0
    no_qr: int = 0
    duplicate: int = 0
    downloading: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: int = 0


class BatchState(BaseModel):
    batch_id: str
    created_at: str
    is_scanning: bool = False
    is_downloading: bool = False
    settings: AppSettings
    summary: BatchSummary = Field(default_factory=BatchSummary)
    items: list[BatchItem] = Field(default_factory=list)


class StartDownloadRequest(BaseModel):
    output_dir: Optional[str] = None
    naming_mode: Optional[NamingMode] = None
    conflict_policy: Optional[ConflictPolicy] = None
    media_filter: Optional[MediaFilter] = None
    create_subfolder_for_multi_links: Optional[bool] = None
    concurrency: Optional[int] = Field(default=None, ge=1, le=12)
    item_ids: Optional[list[str]] = None
    retry_failed_only: bool = False


class ExpandLinksRequest(BaseModel):
    item_ids: Optional[list[str]] = None
    media_filter: Optional[MediaFilter] = None
    create_subfolder_for_multi_links: Optional[bool] = None


class UpdateItemRequest(BaseModel):
    url: Optional[str] = None
    custom_filename: Optional[str] = None
    selected: Optional[bool] = None


class AddManualUrlRequest(BaseModel):
    url: str
    label: Optional[str] = None
    custom_filename: Optional[str] = None


class LanCaptureFile(BaseModel):
    filename: str
    saved_path: str
    size_bytes: int
    created_at: str
    detected_urls: list[str] = Field(default_factory=list)
    imported_to_batch_id: Optional[str] = None


class LanCaptureUploadResponse(BaseModel):
    capture_dir: str
    saved: list[LanCaptureFile] = Field(default_factory=list)


class LanInfoResponse(BaseModel):
    lan_ips: list[str] = Field(default_factory=list)
    primary_ip: str
    port: int
    capture_url: str
    capture_urls: list[str] = Field(default_factory=list)
    capture_dir: str
    auto_import_lan_captures: bool
    saved_count: int = 0
    recent_captures: list[LanCaptureFile] = Field(default_factory=list)
