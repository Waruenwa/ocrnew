from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import manager_uploads as manager_uploads
from app.core.config import BASE_DIR, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Rerun OCR for selected page numbers in one batch.")
    parser.add_argument("batch_id")
    parser.add_argument("--pages", required=True, help="Comma-separated page numbers, for example 2,3,6")
    args = parser.parse_args()

    page_numbers = {
        int(value.strip())
        for value in args.pages.split(",")
        if value.strip()
    }

    load_dotenv(BASE_DIR / ".env", override=True)
    settings = load_settings()
    metadata = manager_uploads._load_batch_metadata(args.batch_id)
    records = [record for record in metadata.get("records", []) if isinstance(record, dict)]
    selected_records = [
        record
        for record in records
        if int(record.get("page_number") or 0) in page_numbers
    ]
    if not selected_records:
        raise SystemExit("No matching records found.")

    for record in selected_records:
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

    manager_uploads._apply_batch_ocr_summary(metadata, records)
    manager_uploads._write_metadata(manager_uploads._metadata_path_for_batch(args.batch_id), metadata)

    for record in selected_records:
        manager_uploads._process_tr_ocr_record(
            record,
            settings,
            on_processing_started=lambda: manager_uploads._write_batch_ocr_progress(metadata, records),
        )
        manager_uploads._apply_batch_ocr_summary(metadata, records)
        manager_uploads._write_metadata(manager_uploads._metadata_path_for_batch(args.batch_id), metadata)


if __name__ == "__main__":
    main()
