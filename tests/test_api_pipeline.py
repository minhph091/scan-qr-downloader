from __future__ import annotations

import asyncio
import io
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
import qrcode

from backend.main import app, job_manager


def _qr_png_bytes(url: str) -> bytes:
    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mock_transport_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == "https://books.test/files/ch1.pdf":
        body = b"%PDF-1.4 mock chapter 1 content" * 50
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(len(body)),
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote('Chương 1.pdf')}",
            },
            content=body,
        )

    if url == "https://books.test/landing/ch2":
        html = b'<html><body><a href="/files/audio2.mp3">Download MP3</a></body></html>'
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Length": str(len(html)),
            },
            content=html,
        )

    if url == "https://books.test/files/audio2.mp3":
        body = b"ID3-mock-mp3-data" * 100
        return httpx.Response(
            200,
            headers={
                "Content-Type": "audio/mpeg",
                "Content-Length": str(len(body)),
            },
            content=body,
        )

    return httpx.Response(404, content=b"Not Found")


@pytest.mark.asyncio
async def test_end_to_end_upload_scan_and_download(tmp_path: Path):
    output_dir = tmp_path / "book_downloads"
    prev_transport = job_manager.http_transport
    prev_settings = job_manager.get_settings().model_copy(deep=True)
    job_manager.http_transport = httpx.MockTransport(_mock_transport_handler)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 0. Verify frontend & health endpoint
            health_res = await client.get("/api/health")
            assert health_res.json() == {"status": "ok"}
            index_res = await client.get("/")
            assert index_res.status_code == 200
            assert "ScanQRDownloader" in index_res.text

            # 1. Create batch
            res = await client.post("/api/batches")
            assert res.status_code == 200
            batch_id = res.json()["batch_id"]

            # 2. Upload 2 QR images (1 direct PDF with UTF-8 header + 1 HTML landing page -> MP3)
            img1 = _qr_png_bytes("https://books.test/files/ch1.pdf")
            img2 = _qr_png_bytes("https://books.test/landing/ch2")
            up = await client.post(
                f"/api/batches/{batch_id}/upload",
                files=[
                    ("files", ("Chuong_1.png", img1, "image/png")),
                    ("files", ("Chuong_2.png", img2, "image/png")),
                ],
            )
            assert up.status_code == 200
            assert len(up.json()) == 2

            # 3. Wait for async scanning to complete
            batch_data = None
            for _ in range(50):
                batch_data = (await client.get(f"/api/batches/{batch_id}")).json()
                if not batch_data["is_scanning"] and batch_data["summary"]["ready"] == 2:
                    break
                await asyncio.sleep(0.05)

            assert batch_data is not None
            assert batch_data["summary"]["ready"] == 2, f"Unexpected scan state: {batch_data}"

            # 3b. Verify thumbnail endpoint works
            first_item_id = batch_data["items"][0]["id"]
            thumb_res = await client.get(f"/api/batches/{batch_id}/items/{first_item_id}/thumbnail")
            assert thumb_res.status_code == 200
            assert thumb_res.headers["content-type"] == "image/jpeg"

            # 4. Trigger batch download into output_dir
            dl = await client.post(
                f"/api/batches/{batch_id}/download",
                json={
                    "output_dir": str(output_dir),
                    "naming_mode": "index_prefix",
                    "conflict_policy": "rename",
                    "concurrency": 2,
                },
            )
            assert dl.status_code == 200

            # 5. Wait for downloads to finish
            for _ in range(60):
                batch_data = (await client.get(f"/api/batches/{batch_id}")).json()
                if not batch_data["is_downloading"] and batch_data["summary"]["completed"] == 2:
                    break
                await asyncio.sleep(0.05)

            assert batch_data["summary"]["completed"] == 2, f"Unexpected download state: {batch_data}"
            saved_files = sorted(p.name for p in output_dir.iterdir())
            assert saved_files == ["01_Chuong 1.pdf", "02_audio2.mp3"]
            assert (output_dir / "01_Chuong 1.pdf").read_bytes().startswith(b"%PDF-1.4")
            assert (output_dir / "02_audio2.mp3").read_bytes().startswith(b"ID3-mock-mp3-data")

            # 6. Export CSV report
            csv_res = await client.get(f"/api/batches/{batch_id}/report.csv")
            assert csv_res.status_code == 200
            assert "01_Chuong 1.pdf" in csv_res.text
            assert "02_audio2.mp3" in csv_res.text
    finally:
        job_manager.http_transport = prev_transport
        job_manager.update_settings(prev_settings)
