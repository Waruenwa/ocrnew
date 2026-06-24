from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR


REFERENCE_DIR = BASE_DIR / "data_storage" / "reference"
PROVINCES_PATH = REFERENCE_DIR / "provinces.json"
DISTRICTS_PATH = REFERENCE_DIR / "districts.json"
SUBDISTRICTS_PATH = REFERENCE_DIR / "subdistricts.json"

LOCATION_PATTERN = re.compile(
    r"(?P<sub_marker>ต\.|ตำบล|แขวง)\s*(?P<subdistrict>.+?)\s+"
    r"(?P<district_marker>อ\.|อำเภอ|เขต)\s*(?P<district>.+?)\s+"
    r"(?:(?P<province_marker>จ\.|จังหวัด)\s*)?(?P<province>กรุงเทพมหานคร|[^\s]+)\s*$"
)


@dataclass(frozen=True)
class LocationEntry:
    province_code: int
    district_code: int
    province: str
    district: str
    subdistrict: str
    postal_code: str


@dataclass(frozen=True)
class AddressLocationValidation:
    valid: bool
    corrected_address: str | None
    confidence: float
    issue: str | None
    message: str | None
    postal_code: str | None
    candidate: dict[str, str] | None
    parsed: dict[str, str]


def validate_and_correct_tr_address(address: str | None) -> AddressLocationValidation:
    text = _normalize_text(address)
    parsed = _parse_address_location(text)
    if parsed is None:
        return AddressLocationValidation(
            valid=False,
            corrected_address=None,
            confidence=0.0,
            issue="address_location_parse_failed",
            message="Address did not contain a clear ต./อ./จ. location pattern.",
            postal_code=None,
            candidate=None,
            parsed={},
        )

    reference = _load_location_reference()
    if not reference:
        return AddressLocationValidation(
            valid=False,
            corrected_address=None,
            confidence=0.0,
            issue="address_reference_missing",
            message="Thai location reference files were not found or could not be loaded.",
            postal_code=None,
            candidate=None,
            parsed=parsed.values,
        )

    best_entry, scores = _best_location_match(reference, parsed.values)
    confidence = min(scores.values()) if scores else 0.0
    candidate = (
        {
            "province": best_entry.province,
            "district": best_entry.district,
            "subdistrict": best_entry.subdistrict,
            "postalCode": best_entry.postal_code,
        }
        if best_entry is not None
        else None
    )

    if best_entry is None or confidence < 0.82:
        return AddressLocationValidation(
            valid=False,
            corrected_address=None,
            confidence=confidence,
            issue="address_location_not_found",
            message="Address locality did not match the Thai province/district/subdistrict reference.",
            postal_code=best_entry.postal_code if best_entry is not None else None,
            candidate=candidate,
            parsed=parsed.values,
        )

    exact = (
        _location_key(parsed.values["province"]) == _location_key(best_entry.province)
        and _location_key(parsed.values["district"]) == _location_key(best_entry.district)
        and _location_key(parsed.values["subdistrict"]) == _location_key(best_entry.subdistrict)
    )
    if exact:
        return AddressLocationValidation(
            valid=True,
            corrected_address=None,
            confidence=confidence,
            issue=None,
            message=None,
            postal_code=best_entry.postal_code,
            candidate=candidate,
            parsed=parsed.values,
        )

    corrected = _replace_address_components(text, parsed.spans, best_entry)
    return AddressLocationValidation(
        valid=True,
        corrected_address=corrected,
        confidence=confidence,
        issue="address_location_corrected",
        message="Address locality was corrected from Thai province/district/subdistrict reference.",
        postal_code=best_entry.postal_code,
        candidate=candidate,
        parsed=parsed.values,
    )


@dataclass(frozen=True)
class _ParsedAddressLocation:
    values: dict[str, str]
    spans: dict[str, tuple[int, int]]


def _parse_address_location(address: str) -> _ParsedAddressLocation | None:
    match = LOCATION_PATTERN.search(address)
    if not match:
        return None
    values = {
        "province": _clean_location_value(match.group("province")),
        "district": _clean_location_value(match.group("district")),
        "subdistrict": _clean_location_value(match.group("subdistrict")),
    }
    if not all(values.values()):
        return None
    return _ParsedAddressLocation(
        values=values,
        spans={
            "province": match.span("province"),
            "district": match.span("district"),
            "subdistrict": match.span("subdistrict"),
        },
    )


def _replace_address_components(
    address: str,
    spans: dict[str, tuple[int, int]],
    entry: LocationEntry,
) -> str:
    replacements = {
        "province": entry.province,
        "district": entry.district,
        "subdistrict": entry.subdistrict,
    }
    corrected = address
    for key, value in sorted(replacements.items(), key=lambda item: spans[item[0]][0], reverse=True):
        start, end = spans[key]
        corrected = f"{corrected[:start]}{value}{corrected[end:]}"
    return _normalize_text(corrected)


@lru_cache(maxsize=1)
def _load_location_reference() -> tuple[LocationEntry, ...]:
    try:
        provinces = _load_json_list(PROVINCES_PATH)
        districts = _load_json_list(DISTRICTS_PATH)
        subdistricts = _load_json_list(SUBDISTRICTS_PATH)
    except (OSError, json.JSONDecodeError):
        return ()

    provinces_by_code = {
        _as_int(row.get("provinceCode")): str(row.get("provinceNameTh") or "").strip()
        for row in provinces
        if isinstance(row, dict)
    }
    districts_by_code = {
        _as_int(row.get("districtCode")): {
            "province_code": _as_int(row.get("provinceCode")),
            "name": str(row.get("districtNameTh") or "").strip(),
        }
        for row in districts
        if isinstance(row, dict)
    }

    entries: list[LocationEntry] = []
    for row in subdistricts:
        if not isinstance(row, dict):
            continue
        province_code = _as_int(row.get("provinceCode"))
        district_code = _as_int(row.get("districtCode"))
        province_name = provinces_by_code.get(province_code, "")
        district_info = districts_by_code.get(district_code) or {}
        district_name = str(district_info.get("name") or "").strip()
        subdistrict_name = str(row.get("subdistrictNameTh") or "").strip()
        postal_code = str(row.get("postalCode") or "").strip()
        if not province_name or not district_name or not subdistrict_name:
            continue
        entries.append(
            LocationEntry(
                province_code=province_code,
                district_code=district_code,
                province=province_name,
                district=district_name,
                subdistrict=subdistrict_name,
                postal_code=postal_code,
            )
        )
    return tuple(entries)


def _best_location_match(
    entries: tuple[LocationEntry, ...],
    values: dict[str, str],
) -> tuple[LocationEntry | None, dict[str, float]]:
    province_input = values["province"]
    district_input = values["district"]
    subdistrict_input = values["subdistrict"]

    province_candidates = _filter_best(
        entries,
        lambda entry: _similarity(province_input, entry.province),
        min_score=0.82,
    )
    if not province_candidates:
        return None, {}

    district_candidates = _filter_best(
        province_candidates,
        lambda entry: _similarity(district_input, entry.district),
        min_score=0.82,
    )
    if not district_candidates:
        return None, {"province": _similarity(province_input, province_candidates[0].province)}

    subdistrict_scores = sorted(
        (
            (
                _similarity(subdistrict_input, entry.subdistrict),
                _similarity(district_input, entry.district),
                _similarity(province_input, entry.province),
                entry,
            )
            for entry in district_candidates
        ),
        key=lambda item: (item[0], item[1], item[2]),
        reverse=True,
    )
    best_subdistrict_score, _best_district_score, _best_province_score, best_entry = subdistrict_scores[0]
    second_subdistrict_score = subdistrict_scores[1][0] if len(subdistrict_scores) > 1 else 0.0
    scores = {
        "province": _similarity(province_input, best_entry.province),
        "district": _similarity(district_input, best_entry.district),
        "subdistrict": best_subdistrict_score,
    }
    if scores["subdistrict"] < 0.82:
        district_postal_codes = {
            entry.postal_code
            for entry in district_candidates
            if entry.postal_code
        }
        if (
            scores["province"] >= 0.96
            and scores["district"] >= 0.96
            and scores["subdistrict"] >= 0.70
            and scores["subdistrict"] - second_subdistrict_score >= 0.20
            and len(district_postal_codes) == 1
        ):
            scores["subdistrict"] = 0.82
            return best_entry, scores
        return None, scores
    return best_entry, scores


def _filter_best(
    entries: tuple[LocationEntry, ...] | list[LocationEntry],
    scorer: Any,
    *,
    min_score: float,
) -> list[LocationEntry]:
    scored = [(scorer(entry), entry) for entry in entries]
    if not scored:
        return []
    best_score = max(score for score, _entry in scored)
    if best_score < min_score:
        return []
    return [entry for score, entry in scored if score >= max(min_score, best_score - 0.02)]


def _load_json_list(path: Path) -> list[Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, list) else []


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _similarity(left: str, right: str) -> float:
    left_key = _location_key(left)
    right_key = _location_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    return SequenceMatcher(None, left_key, right_key).ratio()


def _location_key(value: str) -> str:
    cleaned = _clean_location_value(value)
    cleaned = cleaned.replace("จังหวัด", "")
    cleaned = cleaned.replace("อำเภอ", "")
    cleaned = cleaned.replace("ตำบล", "")
    cleaned = cleaned.replace("แขวง", "")
    cleaned = cleaned.replace("เขต", "")
    cleaned = cleaned.replace("จ.", "")
    cleaned = cleaned.replace("อ.", "")
    cleaned = cleaned.replace("ต.", "")
    return re.sub(r"\s+", "", cleaned)


def _clean_location_value(value: str) -> str:
    cleaned = _normalize_text(value)
    cleaned = re.sub(r"[\s,;:]+$", "", cleaned)
    return cleaned.strip(" .-")


def _normalize_text(value: str | None) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split())
    text = re.sub(r"(^|\s)([ตอจ])\s*\.\s*", r"\1\2.", text)
    return text
