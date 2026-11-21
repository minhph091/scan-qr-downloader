from pathlib import Path

import numpy as np
import qrcode
from PIL import Image, ImageDraw

from backend.scanner import scan_qr_codes


def _make_qr_pil(data: str, box_size: int = 8) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def test_scan_clean_qr(tmp_path: Path):
    url = "https://publisher.example.com/book/chapter01.pdf"
    img_path = tmp_path / "clean.png"
    _make_qr_pil(url).save(img_path)

    detections = scan_qr_codes(img_path)
    assert len(detections) == 1
    assert detections[0].url == url


def test_scan_small_rotated_qr_on_shadowed_book_page(tmp_path: Path):
    url = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view"
    page = Image.new("RGB", (1800, 2400), color=(238, 232, 218))
    draw = ImageDraw.Draw(page)
    # Simulate lines of printed book text
    for y in range(180, 1750, 55):
        draw.rectangle([160, y, 1620, y + 12], fill=(65, 65, 65))

    # Place a small, slightly tilted QR in the bottom-right margin of the book page
    qr_img = _make_qr_pil(url, box_size=5).rotate(14, expand=True, fillcolor=(238, 232, 218))
    page.paste(qr_img, (1320, 1920))

    # Add uneven shadow gradient across the page
    arr = np.asarray(page, dtype=np.float32)
    gradient = np.linspace(1.0, 0.72, arr.shape[1], dtype=np.float32)[None, :, None]
    shadowed = np.clip(arr * gradient, 0, 255).astype(np.uint8)
    img_path = tmp_path / "book_page_shadowed.jpg"
    Image.fromarray(shadowed).save(img_path, quality=82)

    detections = scan_qr_codes(img_path)
    assert len(detections) >= 1
    assert any(d.url == url for d in detections)


def test_scan_multiple_qrs_in_one_image(tmp_path: Path):
    url1 = "https://example.com/files/track01.mp3"
    url2 = "https://example.com/files/track02.mp3"
    canvas = Image.new("RGB", (1200, 600), color="white")
    canvas.paste(_make_qr_pil(url1, box_size=6), (60, 100))
    canvas.paste(_make_qr_pil(url2, box_size=6), (660, 100))
    img_path = tmp_path / "two_qrs.png"
    canvas.save(img_path)

    detections = scan_qr_codes(img_path)
    urls = {d.url for d in detections}
    assert url1 in urls
    assert url2 in urls


def test_scan_blank_image_returns_empty(tmp_path: Path):
    img_path = tmp_path / "blank.png"
    Image.new("RGB", (600, 600), color=(240, 240, 240)).save(img_path)

    detections = scan_qr_codes(img_path)
    assert detections == []
