from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STORAGE_DIR = DATA_DIR / "storage"
IMPORTS_SOURCE_DIR = STORAGE_DIR / "incoming"
IMPORTS_ORIGINAL_DIR = STORAGE_DIR / "original"
IMPORTS_DERIVED_DIR = STORAGE_DIR / "derived"
IMPORTS_ARCHIVE_DIR = STORAGE_DIR / "archive"
JOBS_STORAGE_DIR = STORAGE_DIR / "jobs"
JOBS_ORIGINAL_DIR = JOBS_STORAGE_DIR / "original"
JOBS_DERIVED_DIR = JOBS_STORAGE_DIR / "derived"
DEFAULT_CORS_ORIGINS = ("http://localhost:3000",)
DEFAULT_OCR_MODEL = "scb10x/typhoon-ocr1.5-3b:latest"
DEFAULT_OCR_TARGET_IMAGE_DIM = 1100
DEFAULT_OCR_TIMEOUT_SECONDS = 120
DEFAULT_OCR_NUM_PREDICT = 2200
DEFAULT_VISION_BASE_URL = ""
DEFAULT_VISION_MODEL = ""
DEFAULT_VISION_TIMEOUT_SECONDS = 45
DEFAULT_VISION_NUM_PREDICT = 600
DEFAULT_ANCHOR_PROVIDER = "auto"
DEFAULT_ANCHOR_LANGUAGE = "th"


def _parse_origins(raw_value: str | None) -> tuple[str, ...]:
    if not raw_value:
        return DEFAULT_CORS_ORIGINS
    values = tuple(origin.strip() for origin in raw_value.split(",") if origin.strip())
    return values or DEFAULT_CORS_ORIGINS


def _get_first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _parse_csv(raw_value: str | None) -> tuple[str, ...]:
    if not raw_value:
        return ()
    values = tuple(value.strip() for value in raw_value.split(",") if value.strip())
    return values


def _looks_local_url(raw_url: str | None) -> bool:
    if not raw_url:
        return False

    try:
        hostname = urlparse(raw_url).hostname
    except ValueError:
        return False

    if not hostname:
        return False
    if hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}:
        return True

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname.endswith(".local")

    return address.is_loopback or address.is_private or address.is_link_local


def _endpoint_ready(base_url: str | None, model: str | None, api_key: str | None) -> bool:
    if not base_url or not model:
        return False
    return bool(api_key or _looks_local_url(base_url))


def _resolve_dir(raw_value: str | None, default_path: Path) -> Path:
    if not raw_value:
        return default_path
    return Path(raw_value).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    app_name: str
    cors_origins: tuple[str, ...]
    mongodb_uri: str
    mongodb_database: str
    mongodb_jobs_collection: str
    mongodb_imports_collection: str
    imports_source_dir: Path
    imports_original_dir: Path
    imports_derived_dir: Path
    imports_archive_dir: Path
    jobs_original_dir: Path
    jobs_derived_dir: Path
    ocr_base_url: str
    ocr_api_key: str | None
    ocr_model: str
    ocr_compare_models: tuple[str, ...]
    text_base_url: str
    text_api_key: str | None
    text_model: str
    ocr_target_image_dim: int
    ocr_timeout_seconds: int
    ocr_num_predict: int
    vision_base_url: str
    vision_api_key: str | None
    vision_model: str
    vision_timeout_seconds: int
    vision_num_predict: int
    anchor_provider: str
    anchor_language: str
    max_upload_mb: int

    @property
    def ocr_ready(self) -> bool:
        return _endpoint_ready(self.ocr_base_url, self.ocr_model, self.ocr_api_key)

    @property
    def extraction_ready(self) -> bool:
        return _endpoint_ready(self.text_base_url, self.text_model, self.text_api_key)

    @property
    def vision_ready(self) -> bool:
        # Vision/Qwen is intentionally disabled in the current OCR-only baseline.
        return False

    @property
    def text_client_api_key(self) -> str:
        return self.text_api_key or "EMPTY"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def load_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "OCR Server"),
        cors_origins=_parse_origins(os.getenv("APP_CORS_ORIGINS")),
        mongodb_uri=os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        mongodb_database=os.getenv("MONGODB_DATABASE", "ocrstudio"),
        mongodb_jobs_collection=os.getenv("MONGODB_JOBS_COLLECTION", "ocr_jobs"),
        mongodb_imports_collection=os.getenv("MONGODB_IMPORTS_COLLECTION", "ocr_imports"),
        imports_source_dir=_resolve_dir(os.getenv("IMPORTS_SOURCE_DIR"), IMPORTS_SOURCE_DIR),
        imports_original_dir=_resolve_dir(
            os.getenv("IMPORTS_ORIGINAL_DIR"),
            IMPORTS_ORIGINAL_DIR,
        ),
        imports_derived_dir=_resolve_dir(
            os.getenv("IMPORTS_DERIVED_DIR"),
            IMPORTS_DERIVED_DIR,
        ),
        imports_archive_dir=_resolve_dir(os.getenv("IMPORTS_ARCHIVE_DIR"), IMPORTS_ARCHIVE_DIR),
        jobs_original_dir=_resolve_dir(
            os.getenv("JOBS_ORIGINAL_DIR"),
            JOBS_ORIGINAL_DIR,
        ),
        jobs_derived_dir=_resolve_dir(
            os.getenv("JOBS_DERIVED_DIR"),
            JOBS_DERIVED_DIR,
        ),
        ocr_base_url=_get_first_env("OCR_BASE_URL", "TYPHOON_BASE_URL") or "https://api.opentyphoon.ai/v1",
        ocr_api_key=_get_first_env("OCR_API_KEY", "TYPHOON_OCR_API_KEY", "TYPHOON_API_KEY"),
        ocr_model=os.getenv("OCR_MODEL") or DEFAULT_OCR_MODEL,
        ocr_compare_models=_parse_csv(os.getenv("OCR_COMPARE_MODELS")),
        text_base_url=_get_first_env("TEXT_BASE_URL", "TYPHOON_TEXT_BASE_URL", "TYPHOON_BASE_URL")
        or "https://api.opentyphoon.ai/v1",
        text_api_key=_get_first_env("TEXT_API_KEY", "TYPHOON_API_KEY", "TYPHOON_OCR_API_KEY"),
        text_model=_get_first_env("TEXT_MODEL", "TYPHOON_TEXT_MODEL") or "typhoon-v2.1-12b-instruct",
        ocr_target_image_dim=int(os.getenv("OCR_TARGET_IMAGE_DIM", str(DEFAULT_OCR_TARGET_IMAGE_DIM))),
        ocr_timeout_seconds=int(os.getenv("OCR_TIMEOUT_SECONDS", str(DEFAULT_OCR_TIMEOUT_SECONDS))),
        ocr_num_predict=int(os.getenv("OCR_NUM_PREDICT", str(DEFAULT_OCR_NUM_PREDICT))),
        vision_base_url=_get_first_env("VISION_BASE_URL", "VLM_BASE_URL")
        or DEFAULT_VISION_BASE_URL,
        vision_api_key=_get_first_env("VISION_API_KEY", "VLM_API_KEY"),
        vision_model=_get_first_env("VISION_MODEL", "VLM_MODEL") or DEFAULT_VISION_MODEL,
        vision_timeout_seconds=int(
            os.getenv("VISION_TIMEOUT_SECONDS", str(DEFAULT_VISION_TIMEOUT_SECONDS))
        ),
        vision_num_predict=int(os.getenv("VISION_NUM_PREDICT", str(DEFAULT_VISION_NUM_PREDICT))),
        anchor_provider=os.getenv("ANCHOR_PROVIDER", DEFAULT_ANCHOR_PROVIDER),
        anchor_language=os.getenv("ANCHOR_LANGUAGE", DEFAULT_ANCHOR_LANGUAGE),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "50")),
    )


def ensure_data_dirs() -> None:
    settings = load_settings()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    settings.imports_source_dir.mkdir(parents=True, exist_ok=True)
    settings.imports_original_dir.mkdir(parents=True, exist_ok=True)
    settings.imports_derived_dir.mkdir(parents=True, exist_ok=True)
    settings.imports_archive_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_original_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_derived_dir.mkdir(parents=True, exist_ok=True)
