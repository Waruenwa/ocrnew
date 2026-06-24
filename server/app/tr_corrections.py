from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR


TR_NAME_CORRECTION_PATH = BASE_DIR / "data_storage" / "tr_name_corrections.json"
TR_NAME_CORRECTION_FIELDS = {"personName", "motherName", "fatherName"}

_CORRECTION_CACHE: tuple[float | None, dict[str, str]] | None = None


def load_tr_name_token_corrections() -> dict[str, str]:
    global _CORRECTION_CACHE
    mtime = TR_NAME_CORRECTION_PATH.stat().st_mtime if TR_NAME_CORRECTION_PATH.exists() else None
    if _CORRECTION_CACHE is not None and _CORRECTION_CACHE[0] == mtime:
        return dict(_CORRECTION_CACHE[1])

    corrections: dict[str, str] = {}
    if TR_NAME_CORRECTION_PATH.exists():
        try:
            payload = json.loads(TR_NAME_CORRECTION_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                source = str(entry.get("source") or "").strip()
                replacement = str(entry.get("replacement") or "").strip()
                enabled = entry.get("enabled", True)
                if enabled and _looks_like_name_token(source) and _looks_like_name_token(replacement):
                    corrections[source] = replacement

    _CORRECTION_CACHE = (mtime, corrections)
    return dict(corrections)


def learn_tr_name_corrections_from_review(
    *,
    original_review_data: Any,
    corrected_result: Any,
    updated_by: str,
) -> list[dict[str, Any]]:
    original_fields = _extract_fields(original_review_data)
    corrected_fields = _extract_fields(corrected_result)
    if not original_fields or not corrected_fields:
        return []

    entries = _load_correction_entries()
    learned: list[dict[str, Any]] = []
    for field_name in TR_NAME_CORRECTION_FIELDS:
        before = _field_value(original_fields.get(field_name))
        after = _field_value(corrected_fields.get(field_name))
        if not before or not after or before == after:
            continue
        for source, replacement in _candidate_token_pairs(before, after):
            entry = _upsert_correction_entry(
                entries,
                source=source,
                replacement=replacement,
                field_name=field_name,
                updated_by=updated_by,
                before=before,
                after=after,
            )
            learned.append(entry)

    if learned:
        _save_correction_entries(entries)
    return learned


def _load_correction_entries() -> list[dict[str, Any]]:
    if not TR_NAME_CORRECTION_PATH.exists():
        return []
    try:
        payload = json.loads(TR_NAME_CORRECTION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _save_correction_entries(entries: list[dict[str, Any]]) -> None:
    global _CORRECTION_CACHE
    TR_NAME_CORRECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "scope": "tr_name_token_corrections",
        "entries": sorted(
            entries,
            key=lambda entry: (str(entry.get("source") or ""), str(entry.get("replacement") or "")),
        ),
    }
    TR_NAME_CORRECTION_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _CORRECTION_CACHE = None


def _upsert_correction_entry(
    entries: list[dict[str, Any]],
    *,
    source: str,
    replacement: str,
    field_name: str,
    updated_by: str,
    before: str,
    after: str,
) -> dict[str, Any]:
    for entry in entries:
        if entry.get("source") == source and entry.get("replacement") == replacement:
            entry["count"] = int(entry.get("count") or 0) + 1
            fields = entry.setdefault("fields", [])
            if isinstance(fields, list) and field_name not in fields:
                fields.append(field_name)
            examples = entry.setdefault("examples", [])
            if isinstance(examples, list):
                examples.append({"before": before, "after": after, "updatedBy": updated_by})
                del examples[:-5]
            entry["enabled"] = True
            return entry

    entry = {
        "source": source,
        "replacement": replacement,
        "enabled": True,
        "count": 1,
        "fields": [field_name],
        "examples": [{"before": before, "after": after, "updatedBy": updated_by}],
    }
    entries.append(entry)
    return entry


def _extract_fields(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    fields = payload.get("fields")
    return fields if isinstance(fields, dict) else None


def _field_value(field: Any) -> str | None:
    value = field.get("value") if isinstance(field, dict) else field
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", str(value)).strip()
    return normalized or None


def _candidate_token_pairs(before: str, after: str) -> list[tuple[str, str]]:
    before_tokens = before.split()
    after_tokens = after.split()
    if len(before_tokens) != len(after_tokens):
        return []
    pairs: list[tuple[str, str]] = []
    for source, replacement in zip(before_tokens, after_tokens, strict=False):
        if source == replacement:
            continue
        if _looks_like_name_token(source) and _looks_like_name_token(replacement):
            pairs.append((source, replacement))
    return pairs


def _looks_like_name_token(value: str) -> bool:
    cleaned = value.strip()
    if len(cleaned) < 2 or len(cleaned) > 48:
        return False
    if any(character.isdigit() for character in cleaned):
        return False
    if not any("\u0E00" <= character <= "\u0E7F" for character in cleaned):
        return False
    if cleaned in {"นาย", "นาง", "นางสาว", "เด็กชาย", "เด็กหญิง", "ชาย", "หญิง", "ไทย"}:
        return False
    return True
