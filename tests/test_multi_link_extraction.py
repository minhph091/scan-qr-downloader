from __future__ import annotations

import asyncio
import io
from pathlib import Path

import httpx
import pytest
import qrcode

from backend.link_extractor import (
    extract_links_from_gdrive_embedded_html,
    extract_links_from_html,
    extract_links_from_m3u,
)
from backend.main import app, job_manager
from backend.models import AppSettings, MediaFilter
from backend.scanner import scan_qr_codes


def _make_qr_png(text: str) -> bytes:
    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_extract_multiple_mp3s_from_html_dom_and_js_playlist():
    html_page = """
    <html>
      <head><title>Unit 1 - Listening Tracks</title></head>
      <body>
        <audio controls src="/audio/track01.mp3" data-title="Track 01 - Dialogue"></audio>
        <div class="player" data-mp3="https://cdn.books.test/u1/track02.mp3?token=abc" data-name="Track 02 - Vocabulary"></div>
        <a href="/docs/workbook.pdf">Workbook PDF</a>
        <script>
          const playlist = [
            {"title": "Track 03 - Exercise", "url": "https:\\/\\/cdn.books.test\\/u1\\/track03.mp3"},
            {"title": "Track 04 - Review", "file": "/audio/track04.m4a"}
          ];
        </script>
      </body>
    </html>
    """
    all_links = extract_links_from_html(html_page, "https://books.test/unit1", MediaFilter.ALL)
    urls = [l.url for l in all_links]
    assert "https://books.test/audio/track01.mp3" in urls
    assert "https://cdn.books.test/u1/track02.mp3?token=abc" in urls
    assert "https://cdn.books.test/u1/track03.mp3" in urls
    assert "https://books.test/audio/track04.m4a" in urls
    assert "https://books.test/docs/workbook.pdf" in urls

    audio_only = extract_links_from_html(html_page, "https://books.test/unit1", MediaFilter.AUDIO_ONLY)
    audio_urls = [l.url for l in audio_only]
    assert len(audio_urls) == 4
    assert "https://books.test/docs/workbook.pdf" not in audio_urls

    titles_by_url = {l.url: l.title for l in audio_only}
    assert titles_by_url["https://cdn.books.test/u1/track03.mp3"] == "Track 03 - Exercise"
    assert titles_by_url["https://books.test/audio/track04.m4a"] == "Track 04 - Review"


def test_extract_links_from_gdrive_embedded_folder():
    embed_html = """
    <div class="flip-entry">
      <a href="https://drive.google.com/file/d/FILEID000010/view">
        <div class="flip-entry-title">Track 10.mp3</div>
      </a>
    </div>
    <div class="flip-entry">
      <a href="https://drive.google.com/file/d/FILEID000002/view">
        <div class="flip-entry-title">Track 2.mp3</div>
      </a>
    </div>
    <div class="flip-entry">
      <a href="https://drive.google.com/file/d/FILEID000001/view">
        <div class="flip-entry-title">Notes.pdf</div>
      </a>
    </div>
    """
    links = extract_links_from_gdrive_embedded_html(embed_html, MediaFilter.AUDIO_ONLY)
    assert [l.title for l in links] == ["Track 2.mp3", "Track 10.mp3"]
    assert links[0].url == "https://drive.google.com/file/d/FILEID000002/view"


def test_extract_links_from_m3u_playlist():
    m3u = """#EXTM3U
#EXTINF:123,Unit 1 Part A
/mp3/u1_a.mp3
#EXTINF:145,Unit 1 Part B
https://cdn.books.test/mp3/u1_b.mp3
"""
    links = extract_links_from_m3u(m3u, "https://books.test/list.m3u", MediaFilter.AUDIO_ONLY)
    assert len(links) == 2
    assert links[0].url == "https://books.test/mp3/u1_a.mp3"
    assert links[0].title == "Unit 1 Part A"
    assert links[1].url == "https://cdn.books.test/mp3/u1_b.mp3"


def test_scan_qr_with_multiple_urls_in_single_payload(tmp_path: Path):
    payload = "https://books.test/a.mp3\nhttps://books.test/b.mp3\nhttps://books.test/c.mp3"
    qr_path = tmp_path / "multi_url_qr.png"
    qr_path.write_bytes(_make_qr_png(payload))
    detections = scan_qr_codes(qr_path)
    assert [d.url for d in detections] == [
        "https://books.test/a.mp3",
        "https://books.test/b.mp3",
        "https://books.test/c.mp3",
    ]


def _multi_mp3_transport(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == "https://books.test/chapter1":
        html = b"""
        <html><head><title>Chapter 1 Audio</title></head><body>
          <a href="/mp3/ch1_01.mp3">\xc3\xb4 2~3</a>
          <a href="/mp3/ch1_02.mp3">B\xc3\xa0i nghe 2</a>
          <a href="/mp3/ch1_03.mp3">Listening 3</a>
          <a href="/pdf/extra.pdf">Extra PDF</a>
        </body></html>
        """
        return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, content=html)

    if url.startswith("https://books.test/mp3/"):
        body = f"ID3-DATA-FOR-{url}".encode("utf-8")
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/mpeg", "Content-Length": str(len(body))},
            content=body,
        )

    return httpx.Response(404, content=b"Not Found")


@pytest.mark.asyncio
async def test_e2e_single_qr_expands_and_downloads_multiple_mp3s_flat_original_names(tmp_path: Path):
    output_dir = tmp_path / "multi_mp3_out"
    prev_transport = job_manager.http_transport
    prev_settings = job_manager.get_settings().model_copy(deep=True)
    job_manager.http_transport = httpx.MockTransport(_multi_mp3_transport)

    try:
        # Verify default AppSettings: original filename (not Vietnamese anchor text 'ô 2~3'), flat in 1 directory
        defaults = AppSettings()
        assert defaults.naming_mode.value == "original"
        assert defaults.create_subfolder_for_multi_links is False

        job_manager.update_settings(
            defaults.model_copy(
                update={
                    "output_dir": str(output_dir),
                    "capture_dir": prev_settings.capture_dir,
                    "media_filter": MediaFilter.AUDIO_ONLY,
                    "auto_expand_links": False,
                }
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            batch_id = (await client.post("/api/batches")).json()["batch_id"]
            qr_bytes = _make_qr_png("https://books.test/chapter1")
            up = await client.post(
                f"/api/batches/{batch_id}/upload",
                files=[("files", ("Chuong_01.png", qr_bytes, "image/png"))],
            )
            assert up.status_code == 200

            for _ in range(40):
                b = (await client.get(f"/api/batches/{batch_id}")).json()
                if not b["is_scanning"] and b["summary"]["ready"] == 1:
                    break
                await asyncio.sleep(0.05)

            dl = await client.post(
                f"/api/batches/{batch_id}/download",
                json={
                    "output_dir": str(output_dir),
                    "media_filter": "audio_only",
                },
            )
            assert dl.status_code == 200

            batch_data = None
            for _ in range(60):
                batch_data = (await client.get(f"/api/batches/{batch_id}")).json()
                if not batch_data["is_downloading"] and batch_data["summary"]["completed"] == 3:
                    break
                await asyncio.sleep(0.05)

            assert batch_data is not None
            assert batch_data["summary"]["total"] == 3
            assert batch_data["summary"]["completed"] == 3

            # All MP3s saved directly inside output_dir (no subfolder) with original filenames
            files = sorted(p.name for p in output_dir.iterdir() if p.is_file())
            subdirs = [p for p in output_dir.iterdir() if p.is_dir()]
            assert subdirs == []
            assert files == ["ch1_01.mp3", "ch1_02.mp3", "ch1_03.mp3"]
    finally:
        job_manager.http_transport = prev_transport
        job_manager.update_settings(prev_settings)


@pytest.mark.asyncio
async def test_e2e_proactive_expand_endpoint_before_download(tmp_path: Path):
    prev_transport = job_manager.http_transport
    prev_settings = job_manager.get_settings().model_copy(deep=True)
    job_manager.http_transport = httpx.MockTransport(_multi_mp3_transport)

    try:
        job_manager.update_settings(
            prev_settings.model_copy(
                update={
                    "media_filter": MediaFilter.AUDIO_ONLY,
                    "auto_expand_links": True,
                    "create_subfolder_for_multi_links": False,
                }
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            batch_id = (await client.post("/api/batches")).json()["batch_id"]
            qr_bytes = _make_qr_png("https://books.test/chapter1")
            await client.post(
                f"/api/batches/{batch_id}/upload",
                files=[("files", ("Unit_01.png", qr_bytes, "image/png"))],
            )

            # With auto_expand_links=True, scanning automatically expands the webpage into 3 MP3 tracks!
            batch_data = None
            for _ in range(50):
                batch_data = (await client.get(f"/api/batches/{batch_id}")).json()
                if not batch_data["is_scanning"] and batch_data["summary"]["ready"] == 3:
                    break
                await asyncio.sleep(0.05)

            assert batch_data is not None
            assert batch_data["summary"]["ready"] == 3
            assert [i["track_index"] for i in batch_data["items"]] == [1, 2, 3]
            assert all(i["subfolder_name"] is None for i in batch_data["items"])
            assert all(i["custom_filename"] is None for i in batch_data["items"])
    finally:
        job_manager.http_transport = prev_transport
        job_manager.update_settings(prev_settings)
