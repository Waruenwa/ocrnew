from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_CRITICAL_FIELDS = ("personName", "personId", "houseCode")


def _thai_digits_to_ascii(value: str) -> str:
    return value.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789"))


def _normalize_value(field_name: str, value: Any) -> str:
    text = _thai_digits_to_ascii(str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    if field_name.lower().endswith("id") or field_name == "houseCode":
        return re.sub(r"\D+", "", text)
    return text


def _record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("record_id") or record.get("_id") or ""),
        str(record.get("original_filename") or ""),
        str(record.get("page_number") or ""),
    )


def _fixture_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("record_id") or entry.get("_id") or ""),
        str(entry.get("original_filename") or ""),
        str(entry.get("page_number") or entry.get("page") or ""),
    )


def _index_records(records: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        record_id, filename, page = _record_key(record)
        if record_id:
            by_key[(record_id, "", "")] = record
        if filename and page:
            by_key[("", filename, page)] = record
    return by_key


def _find_record(
    record_index: dict[tuple[str, str, str], dict[str, Any]],
    fixture_entry: dict[str, Any],
) -> dict[str, Any] | None:
    record_id, filename, page = _fixture_key(fixture_entry)
    if record_id:
        found = record_index.get((record_id, "", ""))
        if found is not None:
            return found
    if filename and page:
        return record_index.get(("", filename, page))
    return None


def _field_value(record: dict[str, Any], field_name: str) -> Any:
    review_data = record.get("review_data")
    fields = review_data.get("fields") if isinstance(review_data, dict) else None
    field = fields.get(field_name) if isinstance(fields, dict) else None
    if not isinstance(field, dict):
        return None
    return field.get("value")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate TR OCR field accuracy against a JSON fixture.")
    parser.add_argument("metadata_path", help="Batch metadata JSON containing records and review_data.")
    parser.add_argument("fixture_path", help="JSON list with expected fields per record/page.")
    parser.add_argument(
        "--critical-fields",
        default=",".join(DEFAULT_CRITICAL_FIELDS),
        help="Comma-separated fields that must all match for critical-field accuracy.",
    )
    args = parser.parse_args()

    metadata = json.loads(Path(args.metadata_path).read_text(encoding="utf-8-sig"))
    fixture = json.loads(Path(args.fixture_path).read_text(encoding="utf-8-sig"))
    if not isinstance(fixture, list):
        raise SystemExit("fixture_path must contain a JSON list")

    records = [record for record in metadata.get("records", []) if isinstance(record, dict)]
    record_index = _index_records(records)
    critical_fields = tuple(field.strip() for field in args.critical_fields.split(",") if field.strip())

    field_totals: defaultdict[str, int] = defaultdict(int)
    field_correct: defaultdict[str, int] = defaultdict(int)
    rows: list[dict[str, Any]] = []
    matched_records = 0
    critical_total = 0
    critical_correct = 0

    for entry in fixture:
        if not isinstance(entry, dict):
            continue
        expected = entry.get("expected")
        if not isinstance(expected, dict):
            continue
        record = _find_record(record_index, entry)
        if record is None:
            rows.append({"fixture": entry, "matched": False, "error": "record not found"})
            continue

        matched_records += 1
        field_results: dict[str, dict[str, Any]] = {}
        for field_name, expected_value in expected.items():
            actual_value = _field_value(record, field_name)
            expected_norm = _normalize_value(field_name, expected_value)
            actual_norm = _normalize_value(field_name, actual_value)
            matched = expected_norm == actual_norm
            field_totals[field_name] += 1
            if matched:
                field_correct[field_name] += 1
            field_results[field_name] = {
                "expected": expected_value,
                "actual": actual_value,
                "matched": matched,
            }

        if all(field in expected for field in critical_fields):
            critical_total += 1
            if all(field_results.get(field, {}).get("matched") for field in critical_fields):
                critical_correct += 1

        rows.append(
            {
                "record_id": record.get("record_id"),
                "original_filename": record.get("original_filename"),
                "page_number": record.get("page_number"),
                "ocr_quality": record.get("ocr_quality"),
                "fields": field_results,
            }
        )

    field_accuracy = {
        field_name: {
            "correct": field_correct[field_name],
            "total": total,
            "accuracy": round(field_correct[field_name] / total, 4) if total else None,
        }
        for field_name, total in sorted(field_totals.items())
    }
    print(
        json.dumps(
            {
                "batch_id": metadata.get("batch_id"),
                "fixture_count": len(fixture),
                "matched_records": matched_records,
                "field_accuracy": field_accuracy,
                "critical_fields": critical_fields,
                "critical_field_accuracy": {
                    "correct": critical_correct,
                    "total": critical_total,
                    "accuracy": round(critical_correct / critical_total, 4) if critical_total else None,
                },
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
