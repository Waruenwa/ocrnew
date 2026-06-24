from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


REVIEW_FIELDS = (
    "personId",
    "houseCode",
    "personName",
    "gender",
    "nationality",
    "birthDate",
    "age",
    "status",
    "motherName",
    "motherId",
    "motherNationality",
    "fatherName",
    "fatherId",
    "fatherNationality",
    "address",
    "moveInDate",
    "remark",
    "updateDate",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize TR OCR quality for one batch metadata file.")
    parser.add_argument("metadata_path")
    args = parser.parse_args()

    path = Path(args.metadata_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    records = [record for record in data.get("records", []) if isinstance(record, dict)]

    ocr_counts = Counter(str(record.get("ocr_status") or "") for record in records)
    quality_counts = Counter(str(record.get("ocr_quality") or "") for record in records)
    missing_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    rows: list[dict[str, object]] = []

    for record in records:
        review_data = record.get("review_data") if isinstance(record.get("review_data"), dict) else {}
        fields = review_data.get("fields") if isinstance(review_data.get("fields"), dict) else {}
        missing = [
            field_name
            for field_name in REVIEW_FIELDS
            if not ((fields.get(field_name) or {}).get("value"))
        ]
        missing_counts.update(missing)

        issues = [
            issue
            for issue in record.get("field_validation_issues") or []
            if isinstance(issue, dict)
        ]
        issue_counts.update(str(issue.get("issue") or "") for issue in issues)
        vision_errors = record.get("vision_errors") or []
        rows.append(
            {
                "page": record.get("page_number"),
                "record_id": record.get("record_id"),
                "ocr_status": record.get("ocr_status"),
                "ocr_quality": record.get("ocr_quality"),
                "missing": missing,
                "issues": issues,
                "vision_error_count": len(vision_errors),
                "personName": (fields.get("personName") or {}).get("value"),
                "motherName": (fields.get("motherName") or {}).get("value"),
                "motherId": (fields.get("motherId") or {}).get("value"),
                "fatherName": (fields.get("fatherName") or {}).get("value"),
                "fatherId": (fields.get("fatherId") or {}).get("value"),
            }
        )

    print(
        json.dumps(
            {
                "batch_id": data.get("batch_id"),
                "status": data.get("status"),
                "ocr_counts": dict(ocr_counts),
                "quality_counts": dict(quality_counts),
                "missing_counts": dict(missing_counts),
                "issue_counts": dict(issue_counts),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
