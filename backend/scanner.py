from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import zxingcpp
from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass

URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"'\]\)]+", flags=re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(
    r"^(?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]{2,})+(?:/[^\s]*)?$",
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class QrDetection:
    raw_text: str
    url: Optional[str]
    stage: str


def extract_urls_from_qr_text(raw_text: str) -> list[str]:
    """Extract ALL normalized HTTP/HTTPS URLs embedded in a single QR payload."""
    text = (raw_text or "").strip()
    if not text:
        return []

    urls: list[str] = []
    for match in URL_IN_TEXT_RE.finditer(text):
        cleaned = match.group(0).rstrip(".,;:")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)

    if urls:
        return urls

    # Fallback: line-by-line bare domains (e.g. www.example.com/a.mp3)
    for line in text.splitlines():
        token = line.strip()
        if BARE_DOMAIN_RE.match(token):
            normalized = f"https://{token}"
            if normalized not in urls:
                urls.append(normalized)

    return urls


def extract_url_from_qr_text(raw_text: str) -> Optional[str]:
    """Extract the first normalized HTTP/HTTPS URL from decoded QR content."""
    urls = extract_urls_from_qr_text(raw_text)
    return urls[0] if urls else None


def _load_bgr_image(image_path: Path) -> np.ndarray:
    """Load image via Pillow (handles Unicode paths, EXIF orientation, WEBP, HEIC) -> BGR ndarray."""
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        rgb = np.asarray(img, dtype=np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _decode_zxing(image: np.ndarray, *, try_harder: bool = True) -> list[str]:
    results = zxingcpp.read_barcodes(
        image,
        formats=zxingcpp.BarcodeFormat.QRCode,
        try_rotate=True,
        try_downscale=True,
        try_invert=True,
        binarizer=zxingcpp.Binarizer.LocalAverage if try_harder else zxingcpp.Binarizer.GlobalHistogram,
    )
    texts: list[str] = []
    for res in results:
        text = (res.text or "").strip()
        if text and text not in texts:
            texts.append(text)
    return texts


def _decode_opencv(image: np.ndarray) -> list[str]:
    detector = cv2.QRCodeDetector()
    texts: list[str] = []
    try:
        ok, decoded_info, _, _ = detector.detectAndDecodeMulti(image)
        if ok and decoded_info:
            for item in decoded_info:
                text = (item or "").strip()
                if text and text not in texts:
                    texts.append(text)
    except cv2.error:
        pass

    if not texts:
        try:
            text, _, _ = detector.detectAndDecode(image)
            text = (text or "").strip()
            if text:
                texts.append(text)
        except cv2.error:
            pass
    return texts


def _generate_preprocessed_variants(bgr: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Build targeted visual enhancements for shadowed, low-contrast, or blurry book photos."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    variants: list[tuple[str, np.ndarray]] = []

    # 1. CLAHE contrast enhancement + slight sharpening (great for book page shadows)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.5)
    sharpened = cv2.addWeighted(enhanced, 1.6, blurred, -0.6, 0)
    variants.append(("clahe_sharpen", sharpened))

    # 2. Adaptive Gaussian threshold (handles uneven page lighting)
    adaptive = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        8,
    )
    variants.append(("adaptive_thresh", adaptive))

    # 3. Otsu binary threshold
    _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(("otsu_thresh", otsu))

    # 4. Upscale if image is relatively small
    h, w = gray.shape[:2]
    if max(h, w) < 1600:
        upscaled = cv2.resize(
            sharpened,
            (int(w * 1.8), int(h * 1.8)),
            interpolation=cv2.INTER_CUBIC,
        )
        variants.append(("upscaled_1.8x", upscaled))

    return variants


def _generate_overlapping_tiles(bgr: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """
    Create overlapping crops for full-page smartphone photos where the QR code
    occupies only a small corner or margin of the book page.
    """
    h, w = bgr.shape[:2]
    if min(h, w) < 500:
        return []

    tiles: list[tuple[str, np.ndarray]] = []
    # 2x2 overlapping grid (60% width/height each) + center crop
    step_h = int(h * 0.4)
    step_w = int(w * 0.4)
    win_h = int(h * 0.6)
    win_w = int(w * 0.6)

    coords = [
        ("tile_tl", 0, 0, win_w, win_h),
        ("tile_tr", step_w, 0, w, win_h),
        ("tile_bl", 0, step_h, win_w, h),
        ("tile_br", step_w, step_h, w, h),
        ("tile_center", int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)),
    ]
    for name, x1, y1, x2, y2 in coords:
        crop = bgr[y1:y2, x1:x2]
        if crop.size > 0:
            tiles.append((name, crop))
    return tiles


def _find_qr_candidate_rois(bgr: np.ndarray, max_rois: int = 8) -> list[np.ndarray]:
    """Locate square-ish dense regions that look like a QR block inside a full page."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    img_area = float(h * w)

    # Detect high-density edges typical of QR modules
    grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.convertScaleAbs(cv2.magnitude(grad_x, grad_y))
    _, thresh = cv2.threshold(mag, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel_size = max(5, min(25, min(h, w) // 50))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    scored: list[tuple[float, tuple[int, int, int, int]]] = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        area = cw * ch
        if area < img_area * 0.002 or area > img_area * 0.65:
            continue
        aspect = cw / float(max(ch, 1))
        if 0.65 <= aspect <= 1.55:
            # Prefer square regions
            squareness = 1.0 - abs(1.0 - aspect)
            scored.append((squareness * area, (x, y, cw, ch)))

    scored.sort(key=lambda item: item[0], reverse=True)
    rois: list[np.ndarray] = []
    for _, (x, y, cw, ch) in scored[:max_rois]:
        pad_x = int(cw * 0.2)
        pad_y = int(ch * 0.2)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(w, x + cw + pad_x)
        y2 = min(h, y + ch + pad_y)
        crop = bgr[y1:y2, x1:x2]
        if crop.size > 0:
            if max(crop.shape[:2]) < 450:
                crop = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            rois.append(crop)
    return rois


def scan_qr_codes(image_path: str | Path) -> list[QrDetection]:
    """
    Multi-stage QR code scanner optimized for photos of book pages:
      1. Fast pass: zxing-cpp directly on original image.
      2. OpenCV QRCodeDetector pass.
      3. Preprocessed full-image passes (CLAHE + sharpen, adaptive threshold, Otsu, upscale).
      4. Overlapping tile pass for small QR codes in full-page photos.
      5. Morphological ROI candidate cropping + upscale pass.
    """
    path = Path(image_path)
    bgr = _load_bgr_image(path)

    found_texts: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _collect(texts: list[str], stage: str) -> None:
        for t in texts:
            if t not in seen:
                seen.add(t)
                found_texts.append((t, stage))

    def _build_detections() -> list[QrDetection]:
        output: list[QrDetection] = []
        seen_urls: set[str] = set()
        for raw, stage in found_texts:
            urls = extract_urls_from_qr_text(raw)
            if not urls:
                output.append(QrDetection(raw_text=raw, url=None, stage=stage))
            else:
                for u in urls:
                    if u not in seen_urls:
                        seen_urls.add(u)
                        output.append(QrDetection(raw_text=raw, url=u, stage=stage))
        return output

    # Stage 1: Fast direct scan with zxing-cpp
    _collect(_decode_zxing(bgr, try_harder=True), "zxing_original")
    if found_texts:
        return _build_detections()

    # Stage 2: OpenCV native QR detector
    _collect(_decode_opencv(bgr), "opencv_original")
    if found_texts:
        return _build_detections()

    # Stage 3: Preprocessed variants (shadows, blur, low contrast)
    for variant_name, img_variant in _generate_preprocessed_variants(bgr):
        _collect(_decode_zxing(img_variant, try_harder=True), f"zxing_{variant_name}")
        if found_texts:
            break
        _collect(_decode_opencv(img_variant), f"opencv_{variant_name}")
        if found_texts:
            break

    if found_texts:
        return _build_detections()

    # Stage 4: Overlapping tiles (for small QR codes in high-res book photos)
    for tile_name, tile_img in _generate_overlapping_tiles(bgr):
        _collect(_decode_zxing(tile_img, try_harder=True), f"zxing_{tile_name}")
        if not found_texts:
            gray_tile = cv2.cvtColor(tile_img, cv2.COLOR_BGR2GRAY)
            clahe_tile = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray_tile)
            _collect(_decode_zxing(clahe_tile, try_harder=True), f"zxing_{tile_name}_clahe")

    if found_texts:
        return _build_detections()

    # Stage 5: Candidate square ROI crops + upscale
    for idx, roi in enumerate(_find_qr_candidate_rois(bgr), start=1):
        _collect(_decode_zxing(roi, try_harder=True), f"zxing_roi_{idx}")
        if not found_texts:
            gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _, otsu_roi = cv2.threshold(gray_roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            _collect(_decode_zxing(otsu_roi, try_harder=True), f"zxing_roi_{idx}_otsu")
        if found_texts:
            break

    return _build_detections()
