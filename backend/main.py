from __future__ import annotations

import asyncio
import io
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from backend.config import BASE_DIR
from backend.folder_dialog import open_folder_in_explorer, pick_folder_native
from backend.job_manager import JobManager
from backend.models import (
    AddManualUrlRequest,
    AppSettings,
    BatchItem,
    BatchState,
    ExpandLinksRequest,
    LanCaptureFile,
    LanCaptureUploadResponse,
    LanInfoResponse,
    StartDownloadRequest,
    UpdateItemRequest,
)
from backend.network_utils import generate_qr_png_bytes, get_lan_ipv4_addresses

FRONTEND_DIR = BASE_DIR / "frontend"

job_manager = JobManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    job_manager.shutdown()


app = FastAPI(
    title="ScanQRDownloader",
    version="1.1.0",
    lifespan=lifespan,
)


def _build_capture_urls(request: Request) -> tuple[list[str], str, int, str, list[str]]:
    port = request.url.port or 8765
    scheme = request.url.scheme or "http"
    lan_ips = get_lan_ipv4_addresses()
    primary_ip = lan_ips[0] if lan_ips else (request.url.hostname or "127.0.0.1")
    capture_urls = [f"{scheme}://{ip}:{port}/capture" for ip in (lan_ips or [primary_ip])]
    return lan_ips, primary_ip, port, capture_urls[0], capture_urls


def _render_thumbnail_bytes(img_path: Path, full: bool = False) -> bytes:
    with Image.open(img_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        max_dim = 1400 if full else 160
        img.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85 if full else 78)
        return buf.getvalue()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/settings", response_model=AppSettings)
async def get_settings() -> AppSettings:
    return job_manager.get_settings()


@app.put("/api/settings", response_model=AppSettings)
async def update_settings(settings: AppSettings) -> AppSettings:
    return job_manager.update_settings(settings)


@app.post("/api/system/pick-folder")
async def pick_folder(initial_dir: Optional[str] = Query(default=None)) -> dict[str, Optional[str]]:
    current = initial_dir or job_manager.get_settings().output_dir
    selected = await asyncio.to_thread(pick_folder_native, current)
    if selected:
        settings = job_manager.get_settings().model_copy(update={"output_dir": selected})
        job_manager.update_settings(settings)
    return {"path": selected}


@app.post("/api/system/pick-capture-folder")
async def pick_capture_folder(initial_dir: Optional[str] = Query(default=None)) -> dict[str, Optional[str]]:
    current = initial_dir or job_manager.get_settings().capture_dir
    selected = await asyncio.to_thread(pick_folder_native, current)
    if selected:
        settings = job_manager.get_settings().model_copy(update={"capture_dir": selected})
        job_manager.update_settings(settings)
    return {"path": selected}


@app.post("/api/system/open-folder")
async def open_folder(path: Optional[str] = Query(default=None)) -> dict[str, bool]:
    target = path or job_manager.get_settings().output_dir
    if not target:
        raise HTTPException(status_code=400, detail="Chưa chỉ định thư mục đích.")
    ok = await asyncio.to_thread(open_folder_in_explorer, target)
    return {"opened": ok}


# ---------------- LAN Mobile Photo Capture API ----------------


@app.get("/api/lan/info", response_model=LanInfoResponse)
async def get_lan_info(request: Request) -> LanInfoResponse:
    lan_ips, primary_ip, port, primary_url, all_urls = _build_capture_urls(request)
    settings = job_manager.get_settings()
    recent = job_manager.list_lan_captures(limit=12)
    return LanInfoResponse(
        lan_ips=lan_ips,
        primary_ip=primary_ip,
        port=port,
        capture_url=primary_url,
        capture_urls=all_urls,
        capture_dir=settings.capture_dir,
        auto_import_lan_captures=settings.auto_import_lan_captures,
        saved_count=job_manager.count_lan_captures(),
        recent_captures=recent,
    )


@app.get("/api/lan/qr")
async def get_lan_qr(
    request: Request,
    url: Optional[str] = Query(default=None),
) -> Response:
    _, _, _, default_url, _ = _build_capture_urls(request)
    target_url = url.strip() if url and url.strip() else default_url
    png_bytes = await asyncio.to_thread(generate_qr_png_bytes, target_url)
    return Response(content=png_bytes, media_type="image/png")


@app.post("/api/lan/capture", response_model=LanCaptureUploadResponse)
async def upload_lan_capture(
    files: list[UploadFile] = File(...),
    prefix: Optional[str] = Form(default=None),
    batch_id: Optional[str] = Form(default=None),
) -> LanCaptureUploadResponse:
    payload: list[tuple[str, bytes]] = []
    for upload in files:
        content = await upload.read()
        if content:
            payload.append((upload.filename or "photo.jpg", content))

    if not payload:
        raise HTTPException(status_code=400, detail="Không có ảnh hợp lệ nào được gửi lên.")

    saved = await job_manager.save_lan_captures(
        payload,
        prefix=prefix,
        target_batch_id=batch_id,
    )
    return LanCaptureUploadResponse(
        capture_dir=str(job_manager.get_capture_dir()),
        saved=saved,
    )


@app.get("/api/lan/captures", response_model=list[LanCaptureFile])
async def list_lan_captures(limit: int = Query(default=100, ge=1, le=500)) -> list[LanCaptureFile]:
    return job_manager.list_lan_captures(limit=limit)


@app.get("/api/lan/captures/{filename}/thumbnail")
async def get_lan_capture_thumbnail(filename: str, full: bool = False) -> Response:
    img_path = job_manager.resolve_capture_file(filename)
    if not img_path:
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh đã chụp.")
    data = await asyncio.to_thread(_render_thumbnail_bytes, img_path, full)
    return Response(content=data, media_type="image/jpeg")


@app.delete("/api/lan/captures/{filename}")
async def delete_lan_capture(filename: str) -> dict[str, bool]:
    ok = job_manager.delete_lan_capture(filename)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh cần xóa.")
    return {"deleted": True}


# ---------------- Batch QR Scanning & Downloading API ----------------


@app.post("/api/batches", response_model=BatchState)
async def create_batch() -> BatchState:
    return job_manager.create_batch()


@app.get("/api/batches/{batch_id}", response_model=BatchState)
async def get_batch(batch_id: str) -> BatchState:
    batch = job_manager.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")
    return batch


@app.delete("/api/batches/{batch_id}/items")
async def clear_batch(batch_id: str) -> dict[str, bool]:
    ok = job_manager.clear_batch(batch_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")
    return {"cleared": True}


@app.post("/api/batches/{batch_id}/upload", response_model=list[BatchItem])
async def upload_images(
    batch_id: str,
    files: list[UploadFile] = File(...),
) -> list[BatchItem]:
    if not job_manager.get_batch(batch_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")

    payload: list[tuple[str, bytes]] = []
    for upload in files:
        content = await upload.read()
        if upload.filename and content:
            payload.append((upload.filename, content))

    if not payload:
        raise HTTPException(status_code=400, detail="Không có file ảnh hợp lệ nào được tải lên.")

    return await job_manager.add_uploaded_images(batch_id, payload)


@app.post("/api/batches/{batch_id}/import-captures", response_model=list[BatchItem])
async def import_captures_to_batch(batch_id: str) -> list[BatchItem]:
    try:
        return await job_manager.import_captures_to_batch(batch_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")


@app.post("/api/batches/{batch_id}/manual-url", response_model=BatchItem)
async def add_manual_url(batch_id: str, req: AddManualUrlRequest) -> BatchItem:
    try:
        return job_manager.add_manual_url(batch_id, req)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")


@app.patch("/api/batches/{batch_id}/items/{item_id}", response_model=BatchItem)
async def update_item(batch_id: str, item_id: str, req: UpdateItemRequest) -> BatchItem:
    try:
        return job_manager.update_item(batch_id, item_id, req)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy mục cần cập nhật.")


@app.delete("/api/batches/{batch_id}/items/{item_id}")
async def delete_item(batch_id: str, item_id: str) -> dict[str, bool]:
    ok = job_manager.delete_item(batch_id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy mục cần xóa.")
    return {"deleted": True}


@app.get("/api/batches/{batch_id}/items/{item_id}/thumbnail")
async def get_item_thumbnail(batch_id: str, item_id: str, full: bool = False) -> Response:
    batch = job_manager.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    item = next((i for i in batch.items if i.id == item_id), None)
    if not item or not item.source_image_path:
        raise HTTPException(status_code=404, detail="Image not found")

    img_path = Path(item.source_image_path)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Image file missing")

    data = await asyncio.to_thread(_render_thumbnail_bytes, img_path, full)
    return Response(content=data, media_type="image/jpeg")


@app.post("/api/batches/{batch_id}/expand", response_model=BatchState)
async def expand_batch_links(batch_id: str, req: ExpandLinksRequest) -> BatchState:
    try:
        return await job_manager.expand_batch_links(batch_id, req)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")


@app.post("/api/batches/{batch_id}/download", response_model=BatchState)
async def start_downloads(batch_id: str, req: StartDownloadRequest) -> BatchState:
    try:
        return await job_manager.start_downloads(batch_id, req)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Không thể tạo thư mục đích: {exc}")


@app.post("/api/batches/{batch_id}/cancel")
async def cancel_downloads(batch_id: str) -> dict[str, bool]:
    cancelled = job_manager.cancel_downloads(batch_id)
    return {"cancelled": cancelled}


@app.get("/api/batches/{batch_id}/events")
async def batch_events(batch_id: str) -> StreamingResponse:
    if not job_manager.get_batch(batch_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")
    return StreamingResponse(
        job_manager.subscribe(batch_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/batches/{batch_id}/report.csv")
async def export_csv(batch_id: str) -> PlainTextResponse:
    try:
        csv_text = job_manager.export_report_csv(batch_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")
    return PlainTextResponse(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="qr_report_{batch_id}.csv"'},
    )


# Mount static frontend assets
if (FRONTEND_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
if (FRONTEND_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_file)


@app.get("/capture", include_in_schema=False)
async def serve_capture_page() -> FileResponse:
    capture_file = FRONTEND_DIR / "capture.html"
    if not capture_file.exists():
        raise HTTPException(status_code=404, detail="Capture page not found")
    return FileResponse(capture_file)
