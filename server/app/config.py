from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
PREVIEWS_DIR = DATA_DIR / "previews"
DB_PATH = DATA_DIR / "ocr.sqlite3"
DEFAULT_CORS_ORIGINS = ("http://localhost:3000",)


def _parse_origins(raw_value: str | None) -> tuple[str, ...]:
    if not raw_value:
        return DEFAULT_CORS_ORIGINS
    values = tuple(origin.strip() for origin in raw_value.split(",") if origin.strip())
    return values or DEFAULT_CORS_ORIGINS


@dataclass(frozen=True)
class Settings:
    app_name: str
    cors_origins: tuple[str, ...]
    typhoon_base_url: str
    typhoon_ocr_api_key: str | None
    typhoon_api_key: str | None
    typhoon_text_model: str
    max_upload_mb: int

    @property
    def ocr_api_key(self) -> str | None:
        return self.typhoon_ocr_api_key or self.typhoon_api_key

    @property
    def text_api_key(self) -> str | None:
        return self.typhoon_api_key or self.typhoon_ocr_api_key

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def load_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Typhoon OCR Server"),
        cors_origins=_parse_origins(os.getenv("APP_CORS_ORIGINS")),
        typhoon_base_url=os.getenv("TYPHOON_BASE_URL", "https://api.opentyphoon.ai/v1"),
        typhoon_ocr_api_key=os.getenv("TYPHOON_OCR_API_KEY") or None,
        typhoon_api_key=os.getenv("TYPHOON_API_KEY") or None,
        typhoon_text_model=os.getenv("TYPHOON_TEXT_MODEL", "typhoon-v2.1-12b-instruct"),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "25")),
    )


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
