from __future__ import annotations

import json
from pathlib import Path

from backend.models import AppSettings

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / ".app_data"
SESSIONS_DIR = DATA_DIR / "sessions"
CONFIG_FILE = DATA_DIR / "settings.json"
DEFAULT_OUTPUT_DIR = BASE_DIR / "downloads"
DEFAULT_CAPTURE_DIR = BASE_DIR / "captured_images"


def ensure_app_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> AppSettings:
    ensure_app_dirs()
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            settings = AppSettings.model_validate(raw)
            if not settings.output_dir:
                settings.output_dir = str(DEFAULT_OUTPUT_DIR)
            if not settings.capture_dir:
                settings.capture_dir = str(DEFAULT_CAPTURE_DIR)
            return settings
        except Exception:
            pass
    return AppSettings(
        output_dir=str(DEFAULT_OUTPUT_DIR),
        capture_dir=str(DEFAULT_CAPTURE_DIR),
    )


def save_settings(settings: AppSettings) -> AppSettings:
    ensure_app_dirs()
    if not settings.output_dir:
        settings.output_dir = str(DEFAULT_OUTPUT_DIR)
    if not settings.capture_dir:
        settings.capture_dir = str(DEFAULT_CAPTURE_DIR)
    CONFIG_FILE.write_text(
        json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return settings
