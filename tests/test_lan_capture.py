from __future__ import annotations

import asyncio
import io
from pathlib import Path

import httpx
import pytest
import qrcode

from backend.main import app, job_manager
from backend.scanner import scan_qr_codes


def _make_qr_png(text: str) -> bytes:
    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_lan_capture_page_qr_and_upload_flow(tmp_path: Path):
    capture_dir = tmp_path / "lan_photos"
    prev_settings = job_manager.get_settings().model_copy(deep=True)

    try:
        updated = prev_settings.model_copy(
            update={
                "capture_dir": str(capture_dir),
                "auto_import_lan_captures": True,
            }
        )
        job_manager.update_settings(updated)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://192.168.1.50:8765") as client:
            # 1. Mobile capture page is served cleanly
            page_res = await client.get("/capture")
            assert page_res.status_code == 200
            assert "Chụp ảnh gửi máy tính" in page_res.text

            # 2. LAN info endpoint returns valid URL and configured capture_dir
            info_res = await client.get("/api/lan/info")
            assert info_res.status_code == 200
            info = info_res.json()
            assert info["capture_dir"] == str(capture_dir)
            assert info["capture_url"].endswith("/capture")

            # 3. LAN QR endpoint returns a valid PNG that decodes to the capture URL
            qr_res = await client.get(f"/api/lan/qr?url={info['capture_url']}")
            assert qr_res.status_code == 200
            assert qr_res.headers["content-type"] == "image/png"
            qr_file = tmp_path / "lan_qr.png"
            qr_file.write_bytes(qr_res.content)
            decoded = scan_qr_codes(qr_file)
            assert len(decoded) == 1
            assert decoded[0].url == info["capture_url"]

            # 4. Create active PC batch so auto-import can push into it
            batch_res = await client.post("/api/batches")
            batch_id = batch_res.json()["batch_id"]

            # 5. Phone captures & uploads a book page image containing a QR URL
            book_url = "https://publisher.example.com/books/ch05.mp3"
            photo_bytes = _make_qr_png(book_url)
            up_res = await client.post(
                "/api/lan/capture",
                data={"prefix": "Chuong_05"},
                files=[("files", ("IMG_001.png", photo_bytes, "image/png"))],
            )
            assert up_res.status_code == 200
            up_data = up_res.json()
            assert len(up_data["saved"]) == 1
            saved = up_data["saved"][0]
            assert saved["filename"].startswith("Chuong_05_")
            assert saved["detected_urls"] == [book_url]
            assert saved["imported_to_batch_id"] == batch_id
            assert (capture_dir / saved["filename"]).is_file()

            # 6. Wait briefly and confirm the PC batch automatically received & scanned the photo
            batch_data = None
            for _ in range(40):
                batch_data = (await client.get(f"/api/batches/{batch_id}")).json()
                if not batch_data["is_scanning"] and batch_data["summary"]["ready"] == 1:
                    break
                await asyncio.sleep(0.05)

            assert batch_data is not None
            assert batch_data["summary"]["ready"] == 1
            assert batch_data["items"][0]["url"] == book_url

            # 7. List captures, fetch thumbnail, block path traversal, and delete
            list_res = await client.get("/api/lan/captures")
            assert list_res.status_code == 200
            assert len(list_res.json()) == 1

            thumb_res = await client.get(f"/api/lan/captures/{saved['filename']}/thumbnail")
            assert thumb_res.status_code == 200
            assert thumb_res.headers["content-type"] == "image/jpeg"

            traversal_res = await client.delete("/api/lan/captures/..%5C..%5Crun.py")
            assert traversal_res.status_code == 404

            del_res = await client.delete(f"/api/lan/captures/{saved['filename']}")
            assert del_res.status_code == 200
            assert not (capture_dir / saved["filename"]).exists()
    finally:
        job_manager.update_settings(prev_settings)


@pytest.mark.asyncio
async def test_import_existing_captures_to_batch(tmp_path: Path):
    capture_dir = tmp_path / "existing_captures"
    capture_dir.mkdir(parents=True, exist_ok=True)
    prev_settings = job_manager.get_settings().model_copy(deep=True)

    try:
        job_manager.update_settings(
            prev_settings.model_copy(
                update={
                    "capture_dir": str(capture_dir),
                    "auto_import_lan_captures": False,
                }
            )
        )
        # Pre-populate capture folder with 2 images
        (capture_dir / "page_1.png").write_bytes(_make_qr_png("https://example.com/1.pdf"))
        (capture_dir / "page_2.png").write_bytes(_make_qr_png("https://example.com/2.pdf"))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            batch_id = (await client.post("/api/batches")).json()["batch_id"]
            imp = await client.post(f"/api/batches/{batch_id}/import-captures")
            assert imp.status_code == 200
            assert len(imp.json()) == 2

            batch_data = None
            for _ in range(40):
                batch_data = (await client.get(f"/api/batches/{batch_id}")).json()
                if not batch_data["is_scanning"] and batch_data["summary"]["ready"] == 2:
                    break
                await asyncio.sleep(0.05)

            assert batch_data is not None
            assert batch_data["summary"]["ready"] == 2
    finally:
        job_manager.update_settings(prev_settings)
