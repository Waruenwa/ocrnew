from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import manager_uploads as manager_uploads
from app.core.config import BASE_DIR, load_settings


def reset_records(batch_id: str) -> None:
    metadata = manager_uploads._load_batch_metadata(batch_id)
    records = [record for record in metadata.get("records", []) if isinstance(record, dict)]
    for record in records:
        record["ocr_status"] = "pending"
        record["ocr_error"] = None
        record["ocr_started_at"] = None
        record["processed_at"] = None
        record["ocr_quality"] = None
        record["review_data"] = None
        record["field_validation_issues"] = []
        record["ocr_candidate_outputs"] = None
        record["vision_errors"] = []
        record["vision_rescued_fields"] = []
        record["review_status"] = "unassigned"
    manager_uploads._apply_batch_ocr_summary(metadata, records)
    manager_uploads._write_metadata(manager_uploads._metadata_path_for_batch(batch_id), metadata)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset and rerun OCR for one manager upload batch.")
    parser.add_argument("batch_id")
    parser.add_argument("--no-reset", action="store_true")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env", override=True)
    _ = load_settings()
    if not args.no_reset:
        reset_records(args.batch_id)
    manager_uploads._process_batch_ocr_sync(args.batch_id)


if __name__ == "__main__":
    main()
