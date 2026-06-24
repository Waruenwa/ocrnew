from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Iterable

from app.tr_corrections import load_tr_name_token_corrections


TR_REVIEW_VERSION = 9
TR_PERSON_ID_PATTERN = r"[0-9๐-๙]-[0-9๐-๙]{4}-[0-9๐-๙]{4,5}-[0-9๐-๙]{1,2}-[0-9๐-๙]"
TR_HOUSE_CODE_PATTERN = r"[0-9๐-๙]{4}-[0-9๐-๙]{6}-[0-9๐-๙]"
TR_MONTH_PATTERN = (
    r"(?:มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|"
    r"กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)"
)
TR_MONTH_NAMES = (
    "มกราคม",
    "กุมภาพันธ์",
    "มีนาคม",
    "เมษายน",
    "พฤษภาคม",
    "มิถุนายน",
    "กรกฎาคม",
    "สิงหาคม",
    "กันยายน",
    "ตุลาคม",
    "พฤศจิกายน",
    "ธันวาคม",
)
TR_DECEASED_KEYWORDS = ("ตาย", "เสียชีวิต")
TR_NATIONALITY_VALUES = {
    "ไทย",
    "จีน",
    "ลาว",
    "พม่า",
    "กัมพูชา",
    "มาเลเซีย",
    "เวียดนาม",
    "อินเดีย",
    "อเมริกัน",
    "ญี่ปุ่น",
}


@dataclass(frozen=True)
class TrFieldTemplate:
    key: str
    label: str
    page_number: int
    bbox: tuple[float, float, float, float] | None = None
    fallback_index: int | None = None


TR_FIELD_TEMPLATES: tuple[TrFieldTemplate, ...] = (
    TrFieldTemplate("tableName", "table name", 1, None),
    TrFieldTemplate("personId", "ID", 1, (0.22, 0.252, 0.46, 0.286), 0),
    TrFieldTemplate("houseCode", "รหัสบ้าน", 1, (0.59, 0.252, 0.82, 0.286)),
    TrFieldTemplate("personName", "ชื่อ", 1, (0.13, 0.287, 0.43, 0.318)),
    TrFieldTemplate("gender", "เพศ", 1, (0.47, 0.287, 0.55, 0.318)),
    TrFieldTemplate("nationality", "สัญชาติ", 1, (0.69, 0.287, 0.78, 0.318)),
    TrFieldTemplate("birthDate", "วันเกิด", 1, (0.13, 0.322, 0.34, 0.355)),
    TrFieldTemplate("age", "อายุ", 1, (0.34, 0.322, 0.43, 0.355)),
    TrFieldTemplate("status", "สถานภาพที่อยู่", 1, (0.62, 0.322, 0.75, 0.36)),
    TrFieldTemplate("motherName", "มารดา", 1, (0.13, 0.36, 0.32, 0.395)),
    # Keep the two ID crops row-specific: the mother's dash must never be
    # mistaken for the father's ID.
    TrFieldTemplate("motherId", "ID มารดา", 1, (0.32, 0.372, 0.55, 0.397), 1),
    TrFieldTemplate("motherNationality", "สัญชาติ มารดา", 1, (0.57, 0.36, 0.69, 0.395)),
    TrFieldTemplate("fatherName", "บิดา", 1, (0.13, 0.397, 0.32, 0.43)),
    TrFieldTemplate("fatherId", "ID บิดา", 1, (0.32, 0.407, 0.55, 0.432), 2),
    TrFieldTemplate("fatherNationality", "สัญชาติ บิดา", 1, (0.57, 0.397, 0.69, 0.43)),
    TrFieldTemplate("address", "ที่อยู่", 1, (0.13, 0.43, 0.82, 0.47)),
    TrFieldTemplate("postalCode", "รหัสไปรษณีย์", 1, None),
    TrFieldTemplate("moveInDate", "เข้ามาอยู่วันที่", 1, (0.13, 0.525, 0.60, 0.565)),
    TrFieldTemplate("remark", "Remark", 1, (0.13, 0.56, 0.58, 0.605)),
    TrFieldTemplate("updateDate", "Update Date", 1, (0.42, 0.70, 0.86, 0.745)),
)

TR_PARENT_LAYOUT_ROW_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "mother": (0.12, 0.352, 0.72, 0.389),
    "father": (0.12, 0.390, 0.72, 0.438),
}

TR_PARENT_LAYOUT_FIELDS: dict[str, tuple[str, str, str]] = {
    "mother": ("motherName", "motherId", "motherNationality"),
    "father": ("fatherName", "fatherId", "fatherNationality"),
}


TR_REQUIRED_FIELD_KEYS: tuple[str, ...] = (
    "personId",
    "houseCode",
    "personName",
    "gender",
    "nationality",
    "birthDate",
    "age",
    "status",
    "updateDate",
)

TR_NAME_FIELD_KEYS = {"personName", "motherName", "fatherName"}
TR_SLOT_FIRST_FIELD_KEYS = {
    template.key
    for template in TR_FIELD_TEMPLATES
    if template.bbox is not None
}
TR_NAME_FORBIDDEN_VALUES = {
    "ชาย",
    "หญิง",
    "ไทย",
    "เจ้าบ้าน",
    "ผู้อาศัย",
}
TR_NAME_FORBIDDEN_PARTS = {
    "table",
    "tbody",
    "tr",
    "td",
    "th",
}
TR_NAME_OCR_REPAIRS: tuple[tuple[str, str], ...] = (
    ("ร์อ", "ร่อ"),
)
TR_NAME_TOKEN_CORRECTIONS: dict[str, str] = {
    "ณศิลดา": "ณศิลตา",
    "จุประเสริฐ": "จูประเสริฐ",
    "คำริว": "คำริ้ว",
    "คําริว": "คำริ้ว",
    "อ๋าไรซิง": "อ่ำไร่ขิง",
    "อ่าไรซิง": "อ่ำไร่ขิง",
    "อ่าไรจิง": "อ่ำไร่ขิง",
    "อ่าไร่ชิง": "อ่ำไร่ขิง",
    "อำไร่ขิง": "อ่ำไร่ขิง",
}
TR_ADDRESS_TOKEN_CORRECTIONS: dict[str, str] = {
    "ไม้ดำ": "ไผ่ต่ำ",
}
TR_ADDRESS_HOUSE_NUMBER_PATTERN = (
    r"[0-9๐-๙]+(?:/[0-9๐-๙]+)?(?:\s*-\s*[0-9๐-๙]+(?:/[0-9๐-๙]+)?)?"
)
TR_DATE_TOKEN_CORRECTIONS: dict[str, str] = {
    "พวภาคม": "พฤษภาคม",
    "พฤศภาคม": "พฤษภาคม",
    "พฤศจิกาคม": "พฤษภาคม",
}


def get_tr_field_template(key: str) -> TrFieldTemplate | None:
    return next((template for template in TR_FIELD_TEMPLATES if template.key == key), None)


def validate_tr_field_value(key: str, value: str | None) -> bool:
    return _is_valid_tr_value(key, value)


def normalize_tr_field_value(key: str, value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _clean_field_value(value, key)
    return cleaned or None


def build_tr_review_data(pages: list[dict[str, object]]) -> dict[str, object]:
    sorted_pages = sorted(pages, key=lambda page: int(page.get("page_number", 0)))
    full_text = "\n".join(_page_text(page) for page in sorted_pages if _page_text(page).strip()).strip()
    id_values = _extract_id_values(full_text)
    parsed_values = _extract_tr_values_from_text(full_text)
    _repair_tr_values_from_layout_text(parsed_values, full_text)
    source_dash_fields = _detect_tr_source_dash_fields(full_text)
    has_parent_dash_before_id = _has_parent_dash_before_secondary_id(full_text)
    parent_single_id_is_father = (
        len(id_values) == 2
        and parsed_values.get("fatherId") == id_values[1]
        and bool(parsed_values.get("fatherName"))
    )
    fields: dict[str, dict[str, object]] = {}

    for template in TR_FIELD_TEMPLATES:
        if template.key == "tableName":
            fields[template.key] = _make_field(
                value="TT_UpTTR",
                page_number=None,
                bbox=None,
                source="constant",
            )
            continue

        page = _find_page(sorted_pages, template.page_number)
        template_value = _extract_template_value(page, template)
        if template.key in source_dash_fields:
            fields[template.key] = _make_field(
                value=None,
                page_number=template.page_number if template.bbox is not None else None,
                bbox=template.bbox,
                source="source_dash",
                review_status="empty_in_source",
            )
            continue

        value = None
        source = None

        if (
            template.key in TR_SLOT_FIRST_FIELD_KEYS
            and _is_valid_tr_value(template.key, template_value)
        ):
            value = template_value
            source = "template_bbox_locked"

        if not value:
            value = parsed_values.get(template.key)
            source = "tr_text_parser" if value else None

        if (
            not value
            and template.fallback_index is not None
            and not (template.key == "motherId" and has_parent_dash_before_id)
            and not (template.key == "motherId" and parent_single_id_is_father)
            and not (
                template.key in {"motherId", "fatherId"}
                and len(id_values) <= 2
                and not has_parent_dash_before_id
            )
        ):
            fallback_index = (
                1
                if template.key == "fatherId" and has_parent_dash_before_id
                else template.fallback_index
            )
            value = id_values[fallback_index] if len(id_values) > fallback_index else None
            source = "regex_id" if value else None

        if not value:
            value = _fallback_value(template.key, full_text)
            source = "regex" if value else None

        if not value and _is_valid_tr_value(template.key, template_value):
            value = template_value
            source = "template_bbox"

        fields[template.key] = _make_field(
            value=value,
            page_number=template.page_number if value and template.bbox is not None else None,
            bbox=template.bbox if value and template.bbox is not None else None,
            source=source,
        )

    _apply_parent_layout_y_post_check(fields, sorted_pages)
    _annotate_tr_correction_evidence(fields, full_text)
    quality_issues = _build_field_quality_issues(fields, sorted_pages)
    return {
        "version": TR_REVIEW_VERSION,
        "documentType": "tr",
        "flags": _build_tr_flags(full_text),
        "fields": fields,
        "qualityIssues": quality_issues,
        "keywordHits": [],
    }


def _build_field_quality_issues(
    fields: dict[str, dict[str, object]],
    pages: list[dict[str, object]],
) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for field_name in ("personName", "motherName", "fatherName"):
        field = fields.get(field_name)
        if not isinstance(field, dict):
            continue

        current_value = str(field.get("value") or "").strip()
        alternatives = _collect_name_field_alternatives(field_name, pages, current_value)
        if len(alternatives) <= 1:
            continue

        field["alternatives"] = alternatives
        issues.append(
            {
                "field": field_name,
                "type": "name_ocr_disagreement",
                "severity": "review",
                "message": "Multiple OCR sources disagree on this Thai name; review against the page crop.",
                "alternatives": alternatives,
            }
        )
    return issues


def _annotate_tr_correction_evidence(
    fields: dict[str, dict[str, object]],
    source_text: str,
) -> None:
    corrections = {
        **TR_NAME_TOKEN_CORRECTIONS,
        **load_tr_name_token_corrections(),
    }
    if not corrections:
        return

    for field_name in TR_NAME_FIELD_KEYS:
        field = fields.get(field_name)
        if not isinstance(field, dict):
            continue
        value = str(field.get("value") or "")
        applied = [
            {
                "type": "name_token_correction",
                "source": source,
                "replacement": replacement,
            }
            for source, replacement in corrections.items()
            if source in source_text and replacement in value
        ]
        if applied:
            field["appliedCorrections"] = applied
            field["reviewStatus"] = "corrected_by_rule"
            field["source"] = (
                f"{field.get('source')}+name_token_correction"
                if field.get("source")
                else "name_token_correction"
            )


def _collect_name_field_alternatives(
    field_name: str,
    pages: list[dict[str, object]],
    current_value: str,
) -> list[dict[str, object]]:
    candidates: list[dict[str, str]] = []
    if current_value:
        candidates.append({"value": current_value, "source": "selected"})

    for page in pages:
        for source_name, text in _page_text_variants(page):
            parsed_values = _extract_tr_values_from_text(text)
            value = parsed_values.get(field_name)
            if not _is_valid_tr_value(field_name, value):
                continue
            candidates.append({"value": str(value), "source": source_name})

    seen: set[str] = set()
    alternatives: list[dict[str, object]] = []
    for candidate in candidates:
        value = candidate["value"]
        if value in seen:
            continue
        seen.add(value)
        alternatives.append(
            {
                "value": value,
                "sources": [
                    item["source"]
                    for item in candidates
                    if item["value"] == value
                ],
            }
        )
    return alternatives


def _page_text_variants(page: dict[str, object]) -> list[tuple[str, str]]:
    variants: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field_name in (
        "corrected_markdown",
        "markdown",
        "raw_markdown",
        "original_markdown",
        "cleaned_markdown",
    ):
        value = page.get(field_name)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        variants.append((field_name, text))
    return variants


def _build_tr_flags(text: str) -> dict[str, object]:
    normalized = _normalize_text(text)
    deceased_date = _extract_deceased_date(normalized)
    is_deceased = deceased_date is not None
    return {
        "deceased": is_deceased,
        "lifeStatus": "deceased" if is_deceased else "alive_or_unknown",
        "deceasedDate": deceased_date,
        "matchedKeywords": list(TR_DECEASED_KEYWORDS) if is_deceased else [],
    }


def _extract_deceased_date(text: str) -> str | None:
    if re.search(r"(?:ไม่|ไม่ได้|มิได้).{0,12}(?:ตาย|เสียชีวิต)", text):
        return None
    for keyword in TR_DECEASED_KEYWORDS:
        keyword_index = text.find(keyword)
        if keyword_index < 0:
            continue
        tail = text[keyword_index : keyword_index + 140]
        deceased_date = _extract_first_thai_date(tail)
        if deceased_date:
            return deceased_date
    return None


def _extract_tr_values_from_text(text: str) -> dict[str, str]:
    values: dict[str, str] = _extract_tr_values_from_lines(text)
    normalized = _strip_tr_label_noise(_normalize_text(text))
    id_values = _extract_id_values(normalized)
    has_parent_dash_before_id = _has_parent_dash_before_secondary_id(normalized)
    if id_values:
        values.setdefault("personId", id_values[0])
    if len(id_values) > 1 and has_parent_dash_before_id:
        values.setdefault("fatherId", id_values[1])
    elif len(id_values) > 2:
        values.setdefault("motherId", id_values[1])
    if len(id_values) > 2 and not has_parent_dash_before_id:
        values.setdefault("fatherId", id_values[2])

    house_code = _fallback_value("houseCode", normalized)
    if house_code:
        values.setdefault("houseCode", house_code)

    birth_date = _fallback_value("birthDate", normalized)
    if birth_date:
        values.setdefault("birthDate", birth_date)
    date_values = _extract_thai_date_values(text)
    if date_values:
        values.setdefault("birthDate", date_values[0])
    if len(date_values) >= 2:
        values.setdefault("moveInDate", date_values[1])
    if len(date_values) >= 3:
        values.setdefault("updateDate", date_values[-1])
    elif len(date_values) >= 2 and not values.get("updateDate"):
        values.setdefault("updateDate", date_values[-1])

    if house_code and birth_date and house_code in normalized:
        _, after_house = normalized.split(house_code, 1)
        if birth_date in after_house:
            before_birth, after_birth = after_house.split(birth_date, 1)
            person_bits = before_birth.strip().split()
            person_values = _extract_compact_person_values(person_bits)
            if person_values:
                for key, value in person_values.items():
                    if not _is_valid_tr_value(key, values.get(key)):
                        values[key] = value
            elif len(person_bits) >= 3:
                values.setdefault("personName", " ".join(person_bits).strip())

            mother_id = values.get("motherId")
            if mother_id and mother_id in after_birth:
                before_mother_id, after_mother_id = after_birth.split(mother_id, 1)
                before_mother_bits = before_mother_id.strip().split()
                if before_mother_bits:
                    age_index = _first_numeric_token_index(before_mother_bits)
                    if age_index is not None:
                        values.setdefault("age", before_mother_bits[age_index])
                        tail = before_mother_bits[age_index + 1 :]
                        status_index = _first_status_token_index(tail)
                        if status_index is not None:
                            values.setdefault("status", tail[status_index])
                            mother_name_bits = tail[status_index + 1 :]
                        else:
                            mother_name_bits = tail[1:] if len(tail) > 1 else []
                        if mother_name_bits:
                            values.setdefault("motherName", " ".join(mother_name_bits).strip())

                father_id = values.get("fatherId")
                if father_id and father_id in after_mother_id:
                    before_father_id, after_father_id = after_mother_id.split(father_id, 1)
                    mother_tail_bits = before_father_id.strip().split()
                    if mother_tail_bits:
                        values.setdefault("motherNationality", mother_tail_bits[0])
                        if len(mother_tail_bits) > 1:
                            values.setdefault("fatherName", " ".join(mother_tail_bits[1:]).strip())

                    after_father_bits = after_father_id.strip().split()
                    if after_father_bits:
                        values.setdefault("fatherNationality", after_father_bits[0])

    parent_values_without_ids = _extract_parent_values_without_ids(
        normalized,
        values,
        birth_date,
    )
    for key, value in parent_values_without_ids.items():
        values.setdefault(key, value)

    age = _fallback_value("age", normalized)
    if age:
        values.setdefault("age", age)
    address = _fallback_value("address", normalized)
    if address:
        values.setdefault("address", address)
    remark = _fallback_value("remark", normalized)
    if remark:
        values.setdefault("remark", remark)

    return {
        key: cleaned
        for key, value in values.items()
        if (cleaned := _clean_field_value(value, key)) and _is_valid_tr_value(key, cleaned)
    }


def _extract_compact_person_values(tokens: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    if len(tokens) < 3:
        return values

    status_index = _first_status_token_index(tokens)
    if status_index is not None:
        values["status"] = tokens[status_index]
        tokens = tokens[:status_index]

    gender_index = next(
        (index for index, token in enumerate(tokens) if token in {"ชาย", "หญิง"}),
        None,
    )
    if gender_index is None or gender_index == 0:
        return values

    values["gender"] = tokens[gender_index]
    name = " ".join(tokens[:gender_index]).strip()
    if _is_valid_tr_value("personName", name):
        values["personName"] = name

    nationality = tokens[gender_index + 1] if len(tokens) > gender_index + 1 else None
    if _is_valid_tr_value("nationality", nationality):
        values["nationality"] = str(nationality)

    return values


def _repair_tr_values_from_layout_text(values: dict[str, str], text: str) -> None:
    normalized_lines = [
        line
        for raw_line in text.splitlines()
        if (line := _normalize_text(raw_line).strip())
    ]
    normalized_text = _normalize_text(text)

    if not _is_valid_tr_value("houseCode", values.get("houseCode")):
        house_code = _extract_house_code_value(normalized_text)
        if house_code:
            values["houseCode"] = house_code

    if values.get("personName") and not _is_valid_tr_value("gender", values.get("gender")):
        inferred_gender = _infer_gender_from_name(values["personName"])
        if inferred_gender:
            values["gender"] = inferred_gender

    if not _is_valid_tr_value("nationality", values.get("nationality")):
        gender = values.get("gender")
        for index, line in enumerate(normalized_lines):
            if gender and line == gender:
                nationality = _next_valid_line_value(normalized_lines, index + 1, "nationality")
                if nationality:
                    values["nationality"] = nationality
                    break

    if not _is_valid_tr_value("status", values.get("status")):
        status_index = _first_status_token_index(normalized_lines)
        if status_index is not None:
            values["status"] = normalized_lines[status_index]

    _repair_parent_values_from_layout_lines(values, normalized_lines)

    if not _is_valid_tr_value("moveInDate", values.get("moveInDate")):
        move_in_date = _extract_move_in_date_from_layout_lines(normalized_lines, values.get("birthDate"))
        if move_in_date:
            values["moveInDate"] = move_in_date

    remark = values.get("remark")
    if remark:
        values["remark"] = _repair_remark_value(remark)


def _next_valid_line_value(lines: list[str], start_index: int, field_name: str) -> str | None:
    for line in lines[start_index : start_index + 5]:
        cleaned = _clean_field_value(line, field_name)
        if _is_valid_tr_value(field_name, cleaned):
            return cleaned
    return None


def _extract_move_in_date_from_layout_lines(lines: list[str], birth_date: str | None) -> str | None:
    remark_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _is_remark_line_marker(line)
        ),
        None,
    )

    for index, line in enumerate(lines):
        if not _is_locality_line_marker(line):
            continue
        stop_index = remark_index if remark_index is not None and remark_index > index else min(len(lines), index + 10)
        date_value = _first_valid_move_in_date_from_lines(lines[index + 1 : stop_index], birth_date)
        if date_value:
            return date_value

    if remark_index is not None:
        start_index = max(0, remark_index - 8)
        date_value = _first_valid_move_in_date_from_lines(lines[start_index:remark_index], birth_date)
        if date_value:
            return date_value

    return None


def _first_valid_move_in_date_from_lines(lines: list[str], birth_date: str | None) -> str | None:
    candidates: list[str] = []
    for index, line in enumerate(lines):
        candidates.append(line)
        if index + 2 < len(lines):
            candidates.append(" ".join(lines[index : index + 3]))
        if index + 1 < len(lines):
            candidates.append(" ".join(lines[index : index + 2]))

    for candidate in candidates:
        date_value = _extract_first_thai_date(candidate)
        if (
            date_value
            and _is_valid_tr_value("moveInDate", date_value)
            and _compact_tr_compare_value(date_value) != _compact_tr_compare_value(birth_date)
        ):
            return date_value
    return None


def _compact_tr_compare_value(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _is_locality_line_marker(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "ท้องถิ่น",
            "อำเภอ",
            "เขต",
            "เทศบาล",
            "ตำบล",
            "เธ—เนเธญเธเธ–เธดเนเธ",
            "เธญเธณเน€เธ เธญ",
            "เน€เธเธ•",
            "เน€เธ—เธจเธเธฒเธฅ",
            "เธ•เธณเธเธฅ",
        )
    )


def _is_remark_line_marker(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "บุคคลนี้",
            "ภูมิลำเนา",
            "สำหรับเจ้าหน้าที่",
            "เธเธธเธเธเธฅเธเธตเน",
            "เธ เธนเธกเธดเธฅเธณเน€เธเธฒ",
            "เธชเธณเธซเธฃเธฑเธเน€เธเนเธฒเธซเธเนเธฒเธ—เธตเน",
        )
    )


def _repair_parent_values_from_layout_lines(values: dict[str, str], lines: list[str]) -> None:
    birth_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _extract_first_thai_date(line)
        ),
        None,
    )
    if birth_index is None:
        return

    address_index = next(
        (
            birth_index + offset
            for offset, line in enumerate(lines[birth_index + 1 :], start=1)
            if _looks_like_address_line(line)
        ),
        None,
    )
    if address_index is None:
        return

    raw_parent_lines = _candidate_parent_lines(lines[birth_index + 1 : address_index], keep_nationality=True)
    _repair_parent_values_from_visible_block(values, raw_parent_lines)
    parsed_parent_values = _extract_parent_values_from_parent_lines(raw_parent_lines)
    for key, value in parsed_parent_values.items():
        if key in values and values[key]:
            continue
        if key in {"motherId", "fatherId"} and not value:
            values.pop(key, None)
            continue
        if _is_valid_tr_value(key, value):
            values[key] = value

    parent_lines = _candidate_parent_lines(lines[birth_index + 1 : address_index])
    if len(parent_lines) < 3:
        return

    for index, line in enumerate(parent_lines[:-1]):
        if not _looks_like_dash_value(line):
            continue
        next_id_index = next(
            (
                index + 1 + offset
                for offset, candidate in enumerate(parent_lines[index + 1 : index + 4])
                if re.fullmatch(TR_PERSON_ID_PATTERN, candidate)
            ),
            None,
        )
        if next_id_index is None:
            continue
        if index > 0 and _looks_like_person_name(parent_lines[index - 1]):
            values["motherName"] = parent_lines[index - 1]
            values.pop("motherId", None)
        father_name = next(
            (
                parent_lines[name_index]
                for name_index in range(index + 1, next_id_index)
                if _looks_like_person_name(parent_lines[name_index])
            ),
            None,
        )
        if father_name:
            values["fatherName"] = father_name
        values["fatherId"] = parent_lines[next_id_index]
        return


def _repair_parent_values_from_visible_block(values: dict[str, str], lines: list[str]) -> None:
    parent_name_entries = [
        (index, line)
        for index, line in enumerate(lines)
        if _looks_like_person_name(line)
    ]
    parent_id_entries = [
        (index, line)
        for index, line in enumerate(lines)
        if re.fullmatch(TR_PERSON_ID_PATTERN, line)
    ]
    nationalities = [
        line
        for line in lines
        if _is_valid_tr_value("motherNationality", line)
    ]
    parent_names = [line for _index, line in parent_name_entries]
    parent_ids = [line for _index, line in parent_id_entries]

    if len(parent_names) >= 2 and len(parent_ids) == 1:
        values["motherName"] = parent_names[0]
        values["fatherName"] = parent_names[1]
        values.pop("motherId", None)
        values.pop("fatherId", None)
        parent_id_index, parent_id = parent_id_entries[0]
        if parent_id_index > parent_name_entries[1][0]:
            values["fatherId"] = parent_id
        elif parent_id_index > parent_name_entries[0][0]:
            values["motherId"] = parent_id
    elif len(parent_names) >= 2 and len(parent_ids) >= 2:
        values["motherName"] = parent_names[0]
        values["motherId"] = parent_ids[0]
        values["fatherName"] = parent_names[1]
        values["fatherId"] = parent_ids[1]
    elif len(parent_names) >= 2 and not parent_ids:
        values["motherName"] = parent_names[0]
        values["fatherName"] = parent_names[1]
        values.pop("motherId", None)
        values.pop("fatherId", None)

    if len(nationalities) >= 2:
        values["motherNationality"] = nationalities[0]
        values["fatherNationality"] = nationalities[1]


def _detect_tr_source_dash_fields(text: str) -> set[str]:
    fields: set[str] = set()
    parent_lines = _extract_parent_block_lines_from_text(text)
    if not parent_lines:
        return fields

    parent_values = _extract_parent_values_from_parent_lines(parent_lines)
    for prefix in ("mother", "father"):
        parent_id_key = f"{prefix}Id"
        if parent_id_key in parent_values and not parent_values[parent_id_key]:
            fields.add(parent_id_key)
    return fields


def _extract_parent_block_lines_from_text(text: str) -> list[str]:
    lines = [
        line
        for raw_line in text.splitlines()
        if (line := _normalize_text(raw_line).strip())
    ]
    birth_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _extract_first_thai_date(line)
        ),
        None,
    )
    if birth_index is None:
        return []

    address_index = next(
        (
            birth_index + offset
            for offset, line in enumerate(lines[birth_index + 1 :], start=1)
            if _looks_like_address_line(line)
        ),
        None,
    )
    if address_index is None:
        return []
    return _candidate_parent_lines(lines[birth_index + 1 : address_index], keep_nationality=True)


def _candidate_parent_lines(lines: list[str], *, keep_nationality: bool = False) -> list[str]:
    return [
        line
        for line in lines
        if not _looks_like_age_token(line)
        and _first_status_token_index([line]) != 0
        and (keep_nationality or not _is_valid_tr_value("nationality", line))
    ]


def _extract_parent_values_from_parent_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    parent_name_entries = [
        (index, line)
        for index, line in enumerate(lines)
        if _looks_like_person_name(line)
    ]
    parent_id_entries = [
        (index, line)
        for index, line in enumerate(lines)
        if re.fullmatch(TR_PERSON_ID_PATTERN, line)
    ]
    nationality_values = [
        line
        for line in lines
        if _is_valid_tr_value("motherNationality", line)
    ]
    if len(parent_name_entries) >= 2 and len(parent_id_entries) == 1:
        values["motherName"] = parent_name_entries[0][1]
        values["fatherName"] = parent_name_entries[1][1]
        parent_id_index, parent_id = parent_id_entries[0]
        if parent_id_index > parent_name_entries[1][0]:
            values["fatherId"] = parent_id
        elif parent_id_index > parent_name_entries[0][0]:
            values["motherId"] = parent_id
        if len(nationality_values) >= 2:
            values["motherNationality"] = nationality_values[0]
            values["fatherNationality"] = nationality_values[1]
        return values

    parent_blocks: list[dict[str, str]] = []
    tokens = [token for line in lines for token in line.split()]
    cursor = 0

    while cursor < len(tokens) and len(parent_blocks) < 2:
        while cursor < len(tokens) and (
            _looks_like_age_token(tokens[cursor])
            or _first_status_token_index([tokens[cursor]]) == 0
            or _looks_like_dash_value(tokens[cursor])
            or re.fullmatch(TR_PERSON_ID_PATTERN, tokens[cursor])
            or _is_valid_tr_value("nationality", tokens[cursor])
        ):
            cursor += 1

        name_tokens: list[str] = []
        while cursor < len(tokens):
            token = tokens[cursor]
            if (
                _looks_like_dash_value(token)
                or re.fullmatch(TR_PERSON_ID_PATTERN, token)
                or _is_valid_tr_value("nationality", token)
                or _looks_like_age_token(token)
                or _first_status_token_index([token]) == 0
                or any(character.isdigit() for character in token)
            ):
                break
            name_tokens.append(token)
            cursor += 1

        name = " ".join(name_tokens).strip()
        if not name:
            cursor += 1
            continue

        block: dict[str, str] = {"name": name}
        if cursor < len(tokens) and (
            _looks_like_dash_value(tokens[cursor])
            or re.fullmatch(TR_PERSON_ID_PATTERN, tokens[cursor])
        ):
            if _looks_like_dash_value(tokens[cursor]):
                block["id"] = ""
            else:
                block["id"] = tokens[cursor]
            cursor += 1

        if cursor < len(tokens) and _is_valid_tr_value("nationality", tokens[cursor]):
            block["nationality"] = tokens[cursor]
            cursor += 1

        if _is_valid_tr_value("motherName", block.get("name")):
            parent_blocks.append(block)

    for block, prefix in zip(parent_blocks, ("mother", "father"), strict=False):
        name = block.get("name")
        parent_id = block.get("id")
        nationality = block.get("nationality")
        if name:
            values[f"{prefix}Name"] = name
        if parent_id is not None and not (
            len(parent_blocks) == 2
            and sum(1 for item in parent_blocks if item.get("id")) == 1
        ):
            values[f"{prefix}Id"] = parent_id
        if nationality:
            values[f"{prefix}Nationality"] = nationality

    return values


def _has_parent_dash_before_secondary_id(text: str) -> bool:
    lines = [
        line
        for raw_line in text.splitlines()
        if (line := _normalize_text(raw_line).strip())
    ]
    for index, line in enumerate(lines[:-1]):
        if not (_looks_like_dash_value(line) or _line_has_dash_token(line)):
            continue
        candidates = [line, *lines[index + 1 : index + 4]]
        if any(re.search(TR_PERSON_ID_PATTERN, candidate) for candidate in candidates):
            return True
    return False


def _looks_like_dash_value(value: str) -> bool:
    cleaned = value.replace("\\", "").strip()
    return cleaned in {"-", "–", "—"}


def _line_has_dash_token(value: str) -> bool:
    return any(_looks_like_dash_value(token) for token in value.split())


def _extract_tr_values_from_lines(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    last_parent: str | None = None
    normalized_lines = [
        line
        for raw_line in text.splitlines()
        if (line := _normalize_text(raw_line))
    ]
    table_values = _extract_tr_values_from_html_table(text)
    if table_values:
        values.update(table_values)
    else:
        values.update(_extract_tr_values_from_horizontal_rows(normalized_lines))
        for key, value in _extract_tr_values_from_vertical_lines(normalized_lines).items():
            values.setdefault(key, value)
    for line in normalized_lines:
        if "<table" in line or "<td" in line or "</table>" in line:
            continue

        if line.startswith("เลขประจำตัวประชาชน"):
            match = re.search(TR_PERSON_ID_PATTERN, line)
            if match:
                values["personId"] = match.group(0)
            continue

        if line.startswith("เลขรหัสประจำบ้าน"):
            match = re.search(TR_HOUSE_CODE_PATTERN, line)
            if match:
                values["houseCode"] = match.group(0)
            continue

        if line.startswith("ชื่อ "):
            values["personName"] = line.removeprefix("ชื่อ").strip()
            continue

        gender_match = re.search(r"เพศ\s+(\S+)", line)
        if gender_match:
            values["gender"] = gender_match.group(1).strip()
        nationality_match = re.search(r"สัญชาติ\s+(\S+)", line)
        if nationality_match:
            nationality = nationality_match.group(1).strip()
            if last_parent == "mother":
                values["motherNationality"] = nationality
            elif last_parent == "father":
                values["fatherNationality"] = nationality
            else:
                values["nationality"] = nationality
            continue

        if line.startswith("เกิดเมื่อ"):
            date_value = _extract_first_thai_date(line)
            if date_value:
                values["birthDate"] = date_value
            continue

        if line.startswith("อายุ"):
            match = re.search(r"อายุ\s+([0-9๐-๙]{1,3})", line)
            if match:
                values["age"] = match.group(1)
            continue

        if line.startswith("สถานภาพ"):
            values["status"] = line.removeprefix("สถานภาพ").strip()
            continue

        parent_match = re.match(
            rf"มารดาชื่อ\s+(.+?)(?:\s+({TR_PERSON_ID_PATTERN}))?$",
            line,
        )
        if parent_match:
            values["motherName"] = parent_match.group(1).strip()
            if parent_match.group(2):
                values["motherId"] = parent_match.group(2)
            last_parent = "mother"
            continue

        parent_match = re.match(
            rf"บิดาชื่อ\s+(.+?)(?:\s+({TR_PERSON_ID_PATTERN}))?$",
            line,
        )
        if parent_match:
            values["fatherName"] = parent_match.group(1).strip()
            if parent_match.group(2):
                values["fatherId"] = parent_match.group(2)
            last_parent = "father"
            continue

        if line.startswith("ที่อยู่"):
            values["address"] = line.removeprefix("ที่อยู่").strip()
            continue

        if line.startswith("เข้ามาอยู่เมื่อวันที่"):
            date_value = _extract_first_thai_date(line)
            if date_value:
                values["moveInDate"] = date_value
            continue

        if line.startswith("บันทึกเพิ่มเติม"):
            values["remark"] = line.removeprefix("บันทึกเพิ่มเติม").strip()
            continue

        if line.startswith("ปรับปรุงครั้งสุดท้าย"):
            date_value = _extract_first_thai_date(line)
            if date_value:
                values["updateDate"] = date_value
            continue

    return values


def _extract_tr_values_from_horizontal_rows(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    parent_rows: list[dict[str, str]] = []

    for line in lines:
        if _has_markup_noise(line):
            continue

        ids = _extract_id_values(line)
        house_code = _extract_house_code_value(line)
        if ids:
            values.setdefault("personId", ids[0])
            if len(ids) > 1:
                values.setdefault("motherId", ids[1])
            if len(ids) > 2:
                values.setdefault("fatherId", ids[2])
        if house_code:
            values.setdefault("houseCode", house_code)

        person_values = _extract_horizontal_person_row(line)
        for key, value in person_values.items():
            values.setdefault(key, value)

        birth_values = _extract_horizontal_birth_row(line)
        for key, value in birth_values.items():
            values.setdefault(key, value)

        parent_row = _extract_horizontal_parent_row(line)
        if parent_row:
            parent_rows.append(parent_row)

        if _looks_like_address_line(line):
            values.setdefault("address", line)

    if len(parent_rows) < 2:
        return values

    for parent_row, prefix in zip(parent_rows[:2], ("mother", "father"), strict=False):
        name = parent_row.get("name")
        parent_id = parent_row.get("id")
        nationality = parent_row.get("nationality")
        if name and _is_valid_tr_value(f"{prefix}Name", name):
            values.setdefault(f"{prefix}Name", name)
        if parent_id == "-":
            values.pop(f"{prefix}Id", None)
        elif parent_id and _is_valid_tr_value(f"{prefix}Id", parent_id):
            values.setdefault(f"{prefix}Id", parent_id)
        if nationality and _is_valid_tr_value(f"{prefix}Nationality", nationality):
            values.setdefault(f"{prefix}Nationality", nationality)

    return values


def _extract_horizontal_person_row(line: str) -> dict[str, str]:
    tokens = line.split()
    if len(tokens) < 3:
        return {}
    gender = tokens[-2]
    nationality = tokens[-1]
    name = " ".join(tokens[:-2]).strip()
    if (
        _is_valid_tr_value("personName", name)
        and _is_valid_tr_value("gender", gender)
        and _is_valid_tr_value("nationality", nationality)
    ):
        return {
            "personName": name,
            "gender": gender,
            "nationality": nationality,
        }
    return {}


def _extract_horizontal_birth_row(line: str) -> dict[str, str]:
    birth_date = _extract_first_thai_date(line)
    if not birth_date or birth_date not in line:
        return {}
    tail = line.split(birth_date, 1)[1].strip().split()
    if not tail:
        return {"birthDate": birth_date}

    values = {"birthDate": birth_date}
    if _looks_like_age_token(tail[0]):
        values["age"] = tail[0]
        tail = tail[1:]
    status_index = _first_status_token_index(tail[:3])
    if status_index is not None:
        values["status"] = tail[status_index]
    return values


def _extract_horizontal_parent_row(line: str) -> dict[str, str] | None:
    tokens = line.split()
    if len(tokens) < 2:
        return None
    nationality = tokens[-1] if _is_valid_tr_value("motherNationality", tokens[-1]) else None
    working_tokens = tokens[:-1] if nationality else tokens
    if not working_tokens:
        return None

    parent_id = working_tokens[-1] if re.fullmatch(TR_PERSON_ID_PATTERN, working_tokens[-1]) else None
    if parent_id is None and _looks_like_dash_value(working_tokens[-1]):
        parent_id = "-"
    if parent_id is None and nationality is None:
        return None
    name_tokens = working_tokens[:-1] if parent_id is not None else working_tokens
    name = " ".join(name_tokens).strip()
    if any(token in {"ชาย", "หญิง", "เจ้าบ้าน", "ผู้อาศัย"} for token in name.split()):
        return None
    if not _looks_like_person_name(name):
        return None
    return {
        "name": name,
        "id": parent_id or "",
        "nationality": nationality or "",
    }


def _extract_tr_values_from_html_table(text: str) -> dict[str, str]:
    if "<table" not in text or "<td" not in text:
        return {}

    values: dict[str, str] = {}
    for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", text, flags=re.IGNORECASE | re.DOTALL):
        cells = [
            _clean_html_cell(cell_match.group(1))
            for cell_match in re.finditer(
                r"<td[^>]*>(.*?)</td>",
                row_match.group(1),
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        for label, value in zip(cells[0::2], cells[1::2], strict=False):
            _apply_label_value(values, label, value)
    return values


def _clean_html_cell(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return _normalize_text(unescape(text))


def _apply_label_value(values: dict[str, str], label: str, value: str) -> None:
    label = label.strip()
    value = value.strip()
    if not label or not value:
        return

    if label == "เลขประจำตัวประชาชน":
        match = re.search(TR_PERSON_ID_PATTERN, value)
        if match:
            values["personId"] = match.group(0)
        return

    if label == "เลขรหัสประจำบ้าน":
        match = re.search(TR_HOUSE_CODE_PATTERN, value)
        if match:
            values["houseCode"] = match.group(0)
        return

    if label == "ชื่อ":
        values["personName"] = value
        inferred_gender = _infer_gender_from_name(value)
        if inferred_gender:
            values.setdefault("gender", inferred_gender)
        return

    if label == "เพศ":
        gender_match = re.search(r"\b(ชาย|หญิง)\b", value)
        if gender_match:
            values["gender"] = gender_match.group(1)
        nationality_match = re.search(r"สัญชาติ\s+(\S+)", value)
        if nationality_match:
            values["nationality"] = nationality_match.group(1)
        return

    if label == "เกิดเมื่อ":
        date_value = _extract_first_thai_date(value)
        if date_value:
            values["birthDate"] = date_value
        age_match = re.search(r"อายุ\s+([0-9๐-๙]{1,3})", value)
        if age_match:
            values["age"] = age_match.group(1)
        return

    if label == "สถานภาพ":
        values["status"] = value
        return

    if label == "มารดาชื่อ":
        _apply_parent_value(values, "mother", value)
        return

    if label == "บิดาชื่อ":
        _apply_parent_value(values, "father", value)
        return

    if label == "สัญชาติ":
        if "fatherName" in values:
            values["fatherNationality"] = value
        elif "motherName" in values:
            values["motherNationality"] = value
        return


def _apply_parent_value(values: dict[str, str], prefix: str, value: str) -> None:
    parent_match = re.match(rf"(.+?)\s+({TR_PERSON_ID_PATTERN}|-)$", value)
    if parent_match:
        values[f"{prefix}Name"] = parent_match.group(1).strip()
        if parent_match.group(2) != "-":
            values[f"{prefix}Id"] = parent_match.group(2)
        return
    values[f"{prefix}Name"] = value.strip()


def _extract_tr_values_from_vertical_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    birth_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _extract_first_thai_date(line)
        ),
        None,
    )
    if birth_index is None:
        return values

    person_id_index = next(
        (
            index
            for index, line in enumerate(lines[:birth_index])
            if _extract_id_values(line)
        ),
        None,
    )
    if person_id_index is not None:
        values["personId"] = _extract_id_values(lines[person_id_index])[0]
        for name_index, line in enumerate(lines[person_id_index + 1 : birth_index], start=person_id_index + 1):
            person_values = _extract_person_values_from_name_line(line)
            if person_values:
                values.update(person_values)
                _repair_person_nationality_from_pre_birth_lines(
                    values,
                    lines[name_index + 1 : birth_index],
                )
                break

    birth_date = _extract_first_thai_date(lines[birth_index])
    if birth_date:
        values["birthDate"] = birth_date

    cursor = birth_index + 1
    if cursor < len(lines) and _looks_like_age_token(lines[cursor]):
        values["age"] = lines[cursor]
        cursor += 1

    if cursor < len(lines):
        house_match = re.fullmatch(TR_HOUSE_CODE_PATTERN, lines[cursor])
        if house_match:
            values["houseCode"] = house_match.group(0)
            cursor += 1

    if cursor < len(lines):
        status_index = _first_status_token_index(lines[cursor : cursor + 2])
        if status_index == 0:
            values["status"] = lines[cursor]
            cursor += 1

    if cursor < len(lines) and _is_valid_tr_value("nationality", lines[cursor]):
        values["nationality"] = lines[cursor]
        cursor += 1

    if "personName" in values:
        inferred_gender = _infer_gender_from_name(values["personName"])
        if inferred_gender:
            values.setdefault("gender", inferred_gender)

    if cursor < len(lines) and "status" not in values:
        status_index = _first_status_token_index(lines[cursor : cursor + 4])
        if status_index is not None:
            values["status"] = lines[cursor + status_index]
            cursor += status_index + 1

    parent_values = _extract_parent_blocks_from_lines(lines[cursor:])
    values.update(parent_values)
    return values


def _repair_person_nationality_from_pre_birth_lines(
    values: dict[str, str],
    lines: list[str],
) -> None:
    if _is_valid_tr_value("nationality", values.get("nationality")):
        return
    for line in lines[:3]:
        if _is_valid_tr_value("nationality", line):
            values["nationality"] = line
            return
        if _is_valid_tr_value("gender", line):
            values.setdefault("gender", line)


def _extract_person_values_from_name_line(line: str) -> dict[str, str]:
    if not _looks_like_person_name(line):
        return {}
    tokens = line.split()
    values: dict[str, str] = {}
    if len(tokens) >= 3 and tokens[-2] in {"ชาย", "หญิง"} and _is_valid_tr_value("nationality", tokens[-1]):
        values["personName"] = " ".join(tokens[:-2]).strip()
        values["gender"] = tokens[-2]
        values["nationality"] = tokens[-1]
        return values
    values["personName"] = line
    inferred_gender = _infer_gender_from_name(line)
    if inferred_gender:
        values["gender"] = inferred_gender
    return values


def _infer_gender_from_name(name: str) -> str | None:
    if name.startswith(("นาย", "เด็กชาย", "ด.ช.")):
        return "ชาย"
    if name.startswith(("นาง", "นางสาว", "น.ส.", "เด็กหญิง", "ด.ญ.")):
        return "หญิง"
    return None


def _extract_parent_blocks_from_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    blocks: list[dict[str, str]] = []
    index = 0
    stop_markers = ("ท้องถิ่น", "บุคคลนี้", "สำหรับเจ้าหน้าที่")
    while index < len(lines) and len(blocks) < 2:
        line = lines[index]
        if any(line.startswith(marker) for marker in stop_markers) or _looks_like_address_line(line):
            break

        block: dict[str, str] | None = None
        parent_inline_match = re.match(
            rf"(.+?)\s+({TR_PERSON_ID_PATTERN}|-)$",
            line,
        )
        if parent_inline_match:
            block = {
                "name": parent_inline_match.group(1).strip(),
                "id": parent_inline_match.group(2).strip(),
            }
            index += 1
        else:
            id_match = re.fullmatch(TR_PERSON_ID_PATTERN, line)
            if id_match and index + 1 < len(lines):
                block = {"id": id_match.group(0), "name": lines[index + 1].strip()}
                index += 2
            elif _looks_like_person_name(line):
                block = {"name": line.strip()}
                index += 1
            else:
                index += 1
                continue

        if index < len(lines) and _is_valid_tr_value("nationality", lines[index]):
            block["nationality"] = lines[index]
            index += 1

        name = block.get("name")
        if name and _is_valid_tr_value("motherName", name):
            blocks.append(block)

    for block, prefix in zip(blocks, ("mother", "father"), strict=False):
        name = block.get("name")
        parent_id = block.get("id")
        nationality = block.get("nationality")
        if name:
            values[f"{prefix}Name"] = name
        if parent_id and parent_id != "-" and not (len(blocks) == 2 and sum(1 for item in blocks if item.get("id")) == 1):
            values[f"{prefix}Id"] = parent_id
        if nationality:
            values[f"{prefix}Nationality"] = nationality
    return values


def _looks_like_person_name(value: str) -> bool:
    cleaned = value.strip()
    if not cleaned or any(character.isdigit() for character in cleaned):
        return False
    if not any("\u0E00" <= character <= "\u0E7F" for character in cleaned):
        return False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        return False
    if cleaned in TR_NATIONALITY_VALUES:
        return False
    if cleaned in {"ไทย", "ชาย", "หญิง", "เจ้าบ้าน", "ผู้อาศัย"}:
        return False
    if cleaned in TR_MONTH_NAMES:
        return False
    if cleaned.startswith(("ท้องถิ่น", "บุคคลนี้", "สำหรับเจ้าหน้าที่", "อำเภอ", "เขต", "จังหวัด")):
        return False
    if _extract_first_thai_date(cleaned):
        return False
    return 1 <= len(cleaned) <= 80


def _looks_like_address_line(value: str) -> bool:
    return bool(
        re.match(
            rf"(?:{TR_ADDRESS_HOUSE_NUMBER_PATTERN})\s+(?:หมู่|ซอย|ถนน|แขวง|ต\.|ตำบล)",
            value.strip(),
        )
    )


def _extract_parent_values_without_ids(
    text: str,
    values: dict[str, str],
    birth_date: str | None,
) -> dict[str, str]:
    if not birth_date or birth_date not in text:
        return {}
    if (
        values.get("motherName")
        and values.get("motherNationality")
        and values.get("fatherName")
        and values.get("fatherNationality")
    ):
        return {}

    after_birth = text.split(birth_date, 1)[1]
    tokens = after_birth.strip().split()
    if not tokens:
        return {}

    cursor = 0
    if _looks_like_age_token(tokens[0]):
        values.setdefault("age", tokens[0])
        cursor = 1

    status_index = _first_status_token_index(tokens[cursor : cursor + 8])
    if status_index is not None:
        values.setdefault("status", tokens[cursor + status_index])
        cursor += status_index + 1

    parent_tokens: list[str] = []
    for token in tokens[cursor:]:
        if any(character.isdigit() for character in token):
            break
        if token in {
            "ที่อยู่",
            "ท้องถิ่น",
            "เข้ามาอยู่วันที่",
            "Remark",
            "Update",
        }:
            break
        parent_tokens.append(token)
        if len(parent_tokens) >= 12:
            break

    nationality_markers = [
        marker
        for marker in (values.get("nationality"), "ไทย")
        if marker and _is_valid_tr_value("nationality", marker)
    ]
    if not nationality_markers:
        return {}

    parsed: dict[str, str] = {}
    remaining = parent_tokens
    for name_key, nationality_key in (
        ("motherName", "motherNationality"),
        ("fatherName", "fatherNationality"),
    ):
        nationality_index = next(
            (
                index
                for index, token in enumerate(remaining)
                if token in nationality_markers
                and _is_valid_tr_value(nationality_key, token)
            ),
            None,
        )
        if nationality_index is None:
            break

        name = " ".join(remaining[:nationality_index]).strip()
        nationality = remaining[nationality_index].strip()
        if name and _is_valid_tr_value(name_key, name):
            parsed[name_key] = name
            parsed[nationality_key] = nationality
        remaining = remaining[nationality_index + 1 :]

    return parsed


def _looks_like_age_token(token: str) -> bool:
    return bool(re.fullmatch(r"[0-9๐-๙]{1,3}", token))


def _derive_age_from_thai_dates(
    birth_date: str | None,
    reference_date: str | None,
) -> str | None:
    birth_parts = _parse_thai_date_parts(birth_date)
    reference_parts = _parse_thai_date_parts(reference_date)
    if birth_parts is None or reference_parts is None:
        return None

    birth_day, birth_month, birth_year = birth_parts
    reference_day, reference_month, reference_year = reference_parts
    age = reference_year - birth_year
    if (reference_month, reference_day) < (birth_month, birth_day):
        age -= 1
    if 0 <= age <= 130:
        return str(age)
    return None


def _parse_thai_date_parts(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"([0-9๐-๙]{1,2})\s+(\S+)\s+([0-9๐-๙]{4})", value.strip())
    if not match:
        return None
    month_by_name = {name: index for index, name in enumerate(TR_MONTH_NAMES, start=1)}
    month = month_by_name.get(match.group(2))
    if month is None:
        return None
    return (
        int(_thai_digits_to_ascii(match.group(1))),
        month,
        int(_thai_digits_to_ascii(match.group(3))),
    )


def _thai_digits_to_ascii(value: str) -> str:
    return value.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789"))


def _compact_tr_digits(value: str) -> str:
    return "".join(character for character in _thai_digits_to_ascii(value) if character.isdigit())


def _is_valid_thai_person_id(value: str) -> bool:
    digits = _compact_tr_digits(value)
    if len(digits) != 13:
        return False
    checksum = (11 - sum(int(digits[index]) * (13 - index) for index in range(12)) % 11) % 10
    return checksum == int(digits[-1])


def _strip_tr_label_noise(value: str) -> str:
    cleaned = value
    for token in (
        "table name",
        "TT_UpTTR",
        "PIE",
        "PHIE",
        "PNBme",
        "PSDX",
        "PNbtionality",
        "PBE",
        "PAhe",
        "PAStatus",
        "MNBme",
        "MIE",
        "MNbtnionality",
        "DNBme",
        "DIE",
        "DNbtionality",
        "AEdress",
        "Address_In",
        "UpDate",
        "ID",
        "รหัสบ้าน",
        "ชื่อ",
        "เพศ",
        "สัญชาติ",
        "วันเกิด",
        "อายุ",
        "สถานภาพที่อยู่",
        "มารดา",
        "บิดา",
        "ที่อยู่",
        "เข้ามาอยู่วันที่",
        "Remark",
        "Update Date",
    ):
        cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"\([^)]*ท้องถิ่น[^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _first_numeric_token_index(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens):
        if re.fullmatch(r"[0-9๐-๙]{1,3}", token):
            return index
    return None


def _first_status_token_index(tokens: list[str]) -> int | None:
    statuses = {"ผู้อาศัย", "เจ้าบ้าน"}
    for index, token in enumerate(tokens):
        if token in statuses:
            return index
    return None


def _find_page(pages: list[dict[str, object]], page_number: int) -> dict[str, object] | None:
    return next((page for page in pages if int(page.get("page_number", 0)) == page_number), None)


def _page_text(page: dict[str, object]) -> str:
    for field_name in ("corrected_markdown", "markdown", "raw_markdown"):
        value = page.get(field_name)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _extract_template_value(page: dict[str, object] | None, template: TrFieldTemplate) -> str | None:
    if page is None or template.bbox is None:
        return None

    values: list[str] = []
    for segment in _segments_in_bbox(
        page,
        template.bbox,
        padding=_template_bbox_padding(template.key),
        min_overlap_ratio=_template_bbox_min_overlap_ratio(template.key),
    ):
        text = _segment_text(segment)
        if text:
            values.append(text)

    value = _clean_field_value(" ".join(values), template.key)
    return value or None


def _apply_parent_layout_y_post_check(
    fields: dict[str, dict[str, object]],
    pages: list[dict[str, object]],
) -> None:
    page = _find_page(pages, 1)
    if page is None or not isinstance(page.get("segments"), list) or not page.get("segments"):
        return

    for prefix, field_names in TR_PARENT_LAYOUT_FIELDS.items():
        row_bbox = TR_PARENT_LAYOUT_ROW_BBOXES[prefix]
        for field_name in field_names:
            field = fields.get(field_name)
            if isinstance(field, dict) and field.get("source") == "source_dash":
                continue
            template = get_tr_field_template(field_name)
            if template is None or template.bbox is None:
                continue

            value = _extract_layout_y_slot_value(
                page=page,
                field_name=field_name,
                row_bbox=row_bbox,
                slot_bbox=template.bbox,
            )
            if field_name in {"motherId", "fatherId"} and _looks_like_dash_value(value or ""):
                if not isinstance(field, dict):
                    field = {}
                    fields[field_name] = field
                field["value"] = None
                field["pageNumber"] = template.page_number
                field["bbox"] = list(template.bbox)
                field["source"] = "source_dash"
                field["reviewStatus"] = "empty_in_source"
                field["reviewNote"] = "Parent ID slot contains a dash in the source image."
                continue
            if not _is_valid_tr_value(field_name, value):
                continue
            if not isinstance(field, dict):
                field = {}
                fields[field_name] = field

            current_value = str(field.get("value") or "").strip()
            if current_value == value and field.get("source"):
                continue

            field["value"] = value
            field["pageNumber"] = template.page_number
            field["bbox"] = list(template.bbox)
            field["source"] = "layout_y_bbox"
            field["reviewStatus"] = "parsed"
            if current_value and current_value != value:
                field["reviewNote"] = (
                    "Value remapped by parent row Y position to avoid cross-row OCR leakage."
                )


def _extract_layout_y_slot_value(
    *,
    page: dict[str, object],
    field_name: str,
    row_bbox: tuple[float, float, float, float],
    slot_bbox: tuple[float, float, float, float],
) -> str | None:
    raw_segments = page.get("segments") or []
    if not isinstance(raw_segments, list):
        return None

    row_left, row_top, row_right, row_bottom = _expand_bbox(row_bbox, 0.002)
    slot_left, _slot_top, slot_right, _slot_bottom = _expand_bbox(slot_bbox, 0.004)
    matches: list[dict[str, object]] = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        segment_bbox = _normalize_bbox(segment.get("bbox"))
        if segment_bbox is None:
            continue
        center_x = (segment_bbox[0] + segment_bbox[2]) / 2
        center_y = (segment_bbox[1] + segment_bbox[3]) / 2
        in_row = row_top <= center_y <= row_bottom
        in_slot = slot_left <= center_x <= slot_right
        if in_row and in_slot:
            matches.append(segment)
            continue
        if (
            _overlap_ratio(segment_bbox, (row_left, row_top, row_right, row_bottom)) >= 0.35
            and _overlap_ratio(segment_bbox, (slot_left, row_top, slot_right, row_bottom)) >= 0.35
        ):
            matches.append(segment)

    matches.sort(key=lambda segment: (_normalize_bbox(segment.get("bbox")) or (0, 0, 0, 0))[0])
    raw_value = " ".join(_segment_text(segment) for segment in matches).strip()
    if field_name in {"motherId", "fatherId"} and (
        _looks_like_dash_value(raw_value) or _line_has_dash_token(raw_value)
    ):
        return "-"
    value = _clean_field_value(raw_value, field_name)
    return value or None


def _segments_in_bbox(
    page: dict[str, object],
    bbox: tuple[float, float, float, float],
    *,
    padding: float = 0.008,
    min_overlap_ratio: float = 0.18,
) -> Iterable[dict[str, object]]:
    raw_segments = page.get("segments") or []
    if not isinstance(raw_segments, list):
        return []

    left, top, right, bottom = _expand_bbox(bbox, padding)
    matches: list[dict[str, object]] = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        segment_bbox = _normalize_bbox(segment.get("bbox"))
        if segment_bbox is None:
            continue
        center_x = (segment_bbox[0] + segment_bbox[2]) / 2
        center_y = (segment_bbox[1] + segment_bbox[3]) / 2
        if left <= center_x <= right and top <= center_y <= bottom:
            matches.append(segment)
            continue
        if _overlap_ratio(segment_bbox, (left, top, right, bottom)) >= min_overlap_ratio:
            matches.append(segment)

    return sorted(matches, key=lambda segment: (_normalize_bbox(segment.get("bbox")) or (0, 0, 0, 0))[1])


def _template_bbox_padding(field_name: str) -> float:
    if field_name in TR_NAME_FIELD_KEYS:
        return 0.003
    if field_name == "address":
        return 0.006
    return 0.005


def _template_bbox_min_overlap_ratio(field_name: str) -> float:
    if field_name in TR_NAME_FIELD_KEYS or field_name == "address":
        return 0.45
    return 0.30


def _segment_text(segment: dict[str, object]) -> str:
    for field_name in ("corrected_text", "raw_text", "text"):
        value = segment.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _expand_bbox(
    bbox: tuple[float, float, float, float],
    padding: float,
) -> tuple[float, float, float, float]:
    left, top, right, bottom = bbox
    return (
        max(0.0, left - padding),
        max(0.0, top - padding),
        min(1.0, right + padding),
        min(1.0, bottom + padding),
    )


def _overlap_ratio(
    source: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
) -> float:
    left = max(source[0], target[0])
    top = max(source[1], target[1])
    right = min(source[2], target[2])
    bottom = min(source[3], target[3])
    if right <= left or bottom <= top:
        return 0.0
    overlap_area = (right - left) * (bottom - top)
    source_area = max((source[2] - source[0]) * (source[3] - source[1]), 0.000001)
    return overlap_area / source_area


def _extract_id_values(text: str) -> list[str]:
    pattern = re.compile(rf"\b{TR_PERSON_ID_PATTERN}\b")
    values: list[str] = []
    for match in pattern.finditer(text):
        value = match.group(0)
        if value not in values:
            values.append(value)
    return values


def _extract_house_code_value(text: str) -> str | None:
    pattern = re.compile(r"\b([0-9๐-๙]{4})[-+ ]([0-9๐-๙]{6})[-+ ]([0-9๐-๙])\b")
    for match in pattern.finditer(text):
        value = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        if _is_valid_tr_value("houseCode", value):
            return value
    return None


def _extract_thai_date_values(text: str) -> list[str]:
    values: list[str] = []
    for match in _thai_date_pattern().finditer(text):
        value = _normalize_thai_date_match(match)
        if value not in values:
            values.append(value)
    return values


def _extract_first_thai_date(text: str) -> str | None:
    match = _thai_date_pattern().search(text)
    return _normalize_thai_date_match(match) if match else None


def _thai_date_pattern() -> re.Pattern[str]:
    return re.compile(
        rf"(?:วันที่\s*)?([0-9๐-๙]{{1,2}})(?:\s+เดือน)?\s+"
        rf"({TR_MONTH_PATTERN})(?:\s+พ\.ศ\.)?\s+([0-9๐-๙]{{4}})"
    )


def _normalize_thai_date_match(match: re.Match[str]) -> str:
    day, month, year = match.group(1), match.group(2), match.group(3)
    return f"{day} {month} {year}"


def _fallback_value(key: str, text: str) -> str | None:
    if key == "houseCode":
        return _extract_house_code_value(text)
    if key == "birthDate":
        return _extract_first_thai_date(text)
    if key == "age":
        match = re.search(r"[0-9๐-๙]{4}\s+([0-9๐-๙]{1,3})\b", text)
        return match.group(1).strip() if match else None
    if key == "address":
        house_number_match = re.search(
            rf"((?:{TR_ADDRESS_HOUSE_NUMBER_PATTERN})\s+(?=หมู่|ซอย|ถนน|แขวง|ต\.|ตำบล).{{4,180}}?(?:กรุงเทพมหานคร|จ\.\s*\S+|จังหวัด\s*\S+))",
            text,
        )
        if house_number_match:
            address = house_number_match.group(1)
            for marker in ("ท้องถิ่น", "บุคคลนี้", "Remark", "Update Date"):
                address = address.split(marker, 1)[0]
            return address.strip()

        match = re.search(rf"((?:{TR_ADDRESS_HOUSE_NUMBER_PATTERN}\s+)?(?:ซอย|ถนน|แขวง|ตำบล|เขต|อำเภอ|จังหวัด|กรุงเทพ)[^#\n]{{8,160}})", text)
        if not match:
            return None
        address = match.group(1)
        for marker in ("ท้องถิ่น", "บุคคลนี้", "Remark", "Update Date"):
            address = address.split(marker, 1)[0]
        return address.strip()
    if key == "remark":
        for marker in ("บุคคลนี้", "มีภูมิลำเนา", "อยู่ในบ้านนี้"):
            index = text.find(marker)
            if index >= 0:
                remark = text[index : index + 140].strip()
                date_match = re.search(r"\b[0-9๐-๙]{1,2}\s+\S+\s+[0-9๐-๙]{4}\b", remark)
                if date_match:
                    remark = remark[: date_match.start()].strip()
                return remark
        if "ภูมิลำ" in text and "บ้า" in text:
            return "บุคคลนี้มีภูมิลำเนาอยู่ในบ้านนี้"
    return None


def _repair_remark_value(value: str) -> str:
    cleaned = _normalize_text(value)
    for marker in (
        "สำหรับเจ้าหน้าที่",
        "PageNumber",
        "Update Date",
    ):
        marker_index = cleaned.find(marker)
        if marker_index >= 0:
            cleaned = cleaned[:marker_index].strip()

    cleaned = re.sub(r"\b[0-9๐-๙]{4}[-+ ][0-9๐-๙]{6}[-+ ][0-9๐-๙]\b", " ", cleaned)
    cleaned = re.sub(r"\b(ชาย|หญิง|ไทย|เจ้าบ้าน|ผู้อาศัย)\b", " ", cleaned)
    if "บุคคล" in cleaned and "ภูมิ" in cleaned:
        return "บุคคลนี้มีภูมิลำเนาอยู่ในบ้านนี้"
    return re.sub(r"\s+", " ", cleaned).strip(" :.-")


def _is_valid_tr_value(key: str, value: str | None) -> bool:
    if value is None:
        return False
    cleaned = _normalize_text(value)
    if not cleaned or cleaned in {"-", "#", "##########"}:
        return False
    if _has_markup_noise(cleaned):
        return False

    if "ท้องถิ่น" in cleaned or "เขตหนองจอก" in cleaned:
        return False
    if key in {"personId", "motherId", "fatherId"}:
        return bool(
            re.fullmatch(
                r"[0-9๐-๙]-[0-9๐-๙]{4}-[0-9๐-๙]{4,5}-[0-9๐-๙]{1,2}-[0-9๐-๙]",
                cleaned,
            )
            and _is_valid_thai_person_id(cleaned)
        )
    if key == "houseCode":
        return bool(re.fullmatch(r"[0-9๐-๙]{4}-[0-9๐-๙]{6}-[0-9๐-๙]", cleaned))
    if key == "postalCode":
        return bool(re.fullmatch(r"[0-9๐-๙]{5}", cleaned))
    if key == "gender":
        return cleaned in {"ชาย", "หญิง"}
    if key in {"nationality", "motherNationality", "fatherNationality"}:
        return cleaned in TR_NATIONALITY_VALUES
    if key == "age":
        if not re.fullmatch(r"[0-9๐-๙]{1,3}", cleaned):
            return False
        age_value = int(_thai_digits_to_ascii(cleaned))
        return 0 <= age_value <= 130
    if key == "status":
        return cleaned in {"ผู้อาศัย", "เจ้าบ้าน"}
    if key in {"birthDate", "moveInDate", "updateDate"}:
        return bool(re.fullmatch(r"[0-9๐-๙]{1,2}\s+\S+\s+[0-9๐-๙]{4}", cleaned))
    if key in {"personName", "motherName", "fatherName"}:
        if not any("\u0E00" <= character <= "\u0E7F" for character in cleaned):
            return False
        lowered = cleaned.lower()
        if any(part in lowered for part in TR_NAME_FORBIDDEN_PARTS):
            return False
        if any(part in cleaned.split() for part in TR_NAME_FORBIDDEN_VALUES):
            return False
        if any(token in cleaned for token in ("อำเภอ", "จังหวัด", "แขวง", "ตำบล", "กรุงเทพ")):
            return False
        if re.search(r"(^|\s)(?:ต\.|อ\.|จ\.)", cleaned):
            return False
        if any(character.isdigit() for character in cleaned):
            return False
        if any(token in cleaned for token in TR_MONTH_NAMES):
            return False
        return 1 <= len(cleaned) <= 80
    return True


def _clean_field_value(value: str, key: str) -> str:
    cleaned = _strip_markup_noise(_normalize_text(value))
    cleaned = re.sub(r"^#+\s*", "", cleaned)
    label_noise = {
        "personId": ("ID", "PIE"),
        "houseCode": ("รหัสบ้าน", "PHIE"),
        "personName": ("ชื่อ", "PNBme"),
        "gender": ("เพศ", "PSDX"),
        "nationality": ("สัญชาติ", "PNbtionality"),
        "birthDate": ("วันเกิด", "PBE"),
        "age": ("อายุ", "PAhe"),
        "status": ("สถานภาพที่อยู่", "PAStatus"),
        "motherName": ("มารดา", "MNBme"),
        "motherId": ("ID", "MIE"),
        "motherNationality": ("สัญชาติ", "MNbtnionality"),
        "fatherName": ("บิดา", "DNBme"),
        "fatherId": ("ID", "DIE"),
        "fatherNationality": ("สัญชาติ", "DNbtionality"),
        "address": ("ที่อยู่", "AEdress"),
        "postalCode": ("รหัสไปรษณีย์", "PPstalCode", "Postal Code"),
        "moveInDate": ("เข้ามาอยู่วันที่", "Address_In"),
        "remark": ("Remark",),
        "updateDate": ("Update Date", "UpDate"),
    }.get(key, ())
    for token in label_noise:
        cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :.-")
    if key in TR_NAME_FIELD_KEYS:
        cleaned = _repair_tr_name_ocr_value(cleaned)
    if key == "address":
        cleaned = _repair_tr_address_ocr_value(cleaned)
    if key in {"birthDate", "moveInDate", "updateDate"}:
        cleaned = _repair_tr_date_ocr_value(cleaned)
    if key in {"personId", "motherId", "fatherId", "houseCode"}:
        cleaned = re.sub(r"\s*[-+]\s*", "-", cleaned)
    if key == "postalCode":
        cleaned = re.sub(r"\D", "", _thai_digits_to_ascii(cleaned))
    if key == "remark":
        cleaned = _repair_remark_value(cleaned)
    return cleaned


def _repair_tr_name_ocr_value(value: str) -> str:
    cleaned = _normalize_text(value)
    for source, replacement in TR_NAME_OCR_REPAIRS:
        cleaned = cleaned.replace(source, replacement)
    for source, replacement in TR_NAME_TOKEN_CORRECTIONS.items():
        cleaned = cleaned.replace(source, replacement)
    for source, replacement in load_tr_name_token_corrections().items():
        cleaned = cleaned.replace(source, replacement)
    return cleaned


def _repair_tr_address_ocr_value(value: str) -> str:
    cleaned = _normalize_text(value)
    for source, replacement in TR_ADDRESS_TOKEN_CORRECTIONS.items():
        cleaned = cleaned.replace(source, replacement)
    cleaned = re.sub(r"([0-9๐-๙])\s*-\s*([0-9๐-๙])", r"\1-\2", cleaned)
    return cleaned


def _repair_tr_date_ocr_value(value: str) -> str:
    cleaned = _normalize_text(value)
    for source, replacement in TR_DATE_TOKEN_CORRECTIONS.items():
        cleaned = cleaned.replace(source, replacement)
    return cleaned


def _has_markup_noise(value: str) -> bool:
    return bool(
        re.search(
            r"(?:<\s*/?\s*(?:table|tbody|tr|td|th)\b|&lt;\s*/?\s*(?:table|tbody|tr|td|th)\b|</|/>)",
            value,
            flags=re.IGNORECASE,
        )
    )


def _strip_markup_noise(value: str) -> str:
    cleaned = unescape(value)
    cleaned = re.sub(
        r"<\s*/?\s*(?:table|tbody|tr|td|th)[^>]*>",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _normalize_text(cleaned)


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\r", "\n").split())


def _make_field(
    *,
    value: str | None,
    page_number: int | None,
    bbox: tuple[float, float, float, float] | None,
    source: str | None,
    review_status: str | None = None,
) -> dict[str, object]:
    resolved_review_status = review_status or ("parsed" if value else "missing")
    return {
        "value": value,
        "pageNumber": page_number,
        "bbox": list(bbox) if bbox is not None else None,
        "source": source,
        "reviewStatus": resolved_review_status,
    }
