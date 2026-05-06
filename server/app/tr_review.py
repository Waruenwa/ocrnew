from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


TR_REVIEW_VERSION = 1
TR_PERSON_ID_PATTERN = r"[0-9๐-๙]-[0-9๐-๙]{4}-[0-9๐-๙]{4,5}-[0-9๐-๙]{1,2}-[0-9๐-๙]"
TR_HOUSE_CODE_PATTERN = r"[0-9๐-๙]{4}-[0-9๐-๙]{6}-[0-9๐-๙]"
TR_MONTH_PATTERN = (
    r"(?:มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|"
    r"กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)"
)


@dataclass(frozen=True)
class TrFieldTemplate:
    key: str
    label: str
    page_number: int
    bbox: tuple[float, float, float, float] | None = None
    fallback_index: int | None = None


TR_FIELD_TEMPLATES: tuple[TrFieldTemplate, ...] = (
    TrFieldTemplate("tableName", "table name", 1, None),
    TrFieldTemplate("personId", "ID", 1, (0.25, 0.12, 0.47, 0.155), 0),
    TrFieldTemplate("houseCode", "รหัสบ้าน", 1, (0.66, 0.12, 0.86, 0.155)),
    TrFieldTemplate("personName", "ชื่อ", 1, (0.14, 0.158, 0.54, 0.2)),
    TrFieldTemplate("gender", "เพศ", 1, (0.55, 0.158, 0.65, 0.2)),
    TrFieldTemplate("nationality", "สัญชาติ", 1, (0.67, 0.158, 0.78, 0.2)),
    TrFieldTemplate("birthDate", "วันเกิด", 1, (0.14, 0.2, 0.36, 0.238)),
    TrFieldTemplate("age", "อายุ", 1, (0.38, 0.2, 0.46, 0.238)),
    TrFieldTemplate("status", "สถานภาพที่อยู่", 1, (0.66, 0.2, 0.79, 0.238)),
    TrFieldTemplate("motherName", "มารดา", 1, (0.14, 0.248, 0.34, 0.288)),
    TrFieldTemplate("motherId", "ID มารดา", 1, (0.35, 0.248, 0.55, 0.288), 1),
    TrFieldTemplate("motherNationality", "สัญชาติ มารดา", 1, (0.66, 0.248, 0.78, 0.288)),
    TrFieldTemplate("fatherName", "บิดา", 1, (0.14, 0.29, 0.34, 0.33)),
    TrFieldTemplate("fatherId", "ID บิดา", 1, (0.35, 0.29, 0.55, 0.33), 2),
    TrFieldTemplate("fatherNationality", "สัญชาติ บิดา", 1, (0.66, 0.29, 0.78, 0.33)),
    TrFieldTemplate("address", "ที่อยู่", 1, (0.14, 0.335, 0.8, 0.382)),
    TrFieldTemplate("moveInDate", "เข้ามาอยู่วันที่", 1, (0.15, 0.41, 0.47, 0.46)),
    TrFieldTemplate("remark", "Remark", 1, (0.15, 0.49, 0.78, 0.54)),
    TrFieldTemplate("updateDate", "Update Date", 1, (0.15, 0.68, 0.55, 0.72)),
)


def build_tr_review_data(pages: list[dict[str, object]]) -> dict[str, object]:
    sorted_pages = sorted(pages, key=lambda page: int(page.get("page_number", 0)))
    full_text = "\n".join(_page_text(page) for page in sorted_pages if _page_text(page).strip()).strip()
    id_values = _extract_id_values(full_text)
    parsed_values = _extract_tr_values_from_text(full_text)
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
        value = parsed_values.get(template.key)
        source = "tr_text_parser" if value else None

        if not value and template.fallback_index is not None:
            value = id_values[template.fallback_index] if len(id_values) > template.fallback_index else None
            source = "regex_id" if value else None

        if not value:
            value = _fallback_value(template.key, full_text)
            source = "regex" if value else None

        if not value:
            template_value = _extract_template_value(page, template)
            if _is_valid_tr_value(template.key, template_value):
                value = template_value
                source = "template_bbox"

        fields[template.key] = _make_field(
            value=value,
            page_number=template.page_number if value and template.bbox is not None else None,
            bbox=template.bbox if value and template.bbox is not None else None,
            source=source,
        )

    return {
        "version": TR_REVIEW_VERSION,
        "documentType": "tr",
        "fields": fields,
        "keywordHits": [],
    }


def _extract_tr_values_from_text(text: str) -> dict[str, str]:
    values: dict[str, str] = _extract_tr_values_from_lines(text)
    normalized = _strip_tr_label_noise(_normalize_text(text))
    id_values = _extract_id_values(normalized)
    if id_values:
        values.setdefault("personId", id_values[0])
    if len(id_values) > 1:
        values.setdefault("motherId", id_values[1])
    if len(id_values) > 2:
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

    if house_code and birth_date and house_code in normalized:
        _, after_house = normalized.split(house_code, 1)
        if birth_date in after_house:
            before_birth, after_birth = after_house.split(birth_date, 1)
            person_bits = before_birth.strip().split()
            if len(person_bits) >= 3:
                if person_bits[-2] in {"ชาย", "หญิง"}:
                    values.setdefault("personName", " ".join(person_bits[:-2]).strip())
                    values.setdefault("gender", person_bits[-2])
                    values.setdefault("nationality", person_bits[-1])
                else:
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


def _extract_tr_values_from_lines(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    last_parent: str | None = None
    for raw_line in text.splitlines():
        line = _normalize_text(raw_line)
        if not line:
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
    statuses = {"ผู้อาศัย", "เจ้าบ้าน", "ผู้อยู่อาศัย"}
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
    for segment in _segments_in_bbox(page, template.bbox):
        text = _segment_text(segment)
        if text:
            values.append(text)

    value = _clean_field_value(" ".join(values), template.key)
    return value or None


def _segments_in_bbox(
    page: dict[str, object],
    bbox: tuple[float, float, float, float],
) -> Iterable[dict[str, object]]:
    raw_segments = page.get("segments") or []
    if not isinstance(raw_segments, list):
        return []

    left, top, right, bottom = _expand_bbox(bbox, 0.008)
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
        if _overlap_ratio(segment_bbox, (left, top, right, bottom)) >= 0.18:
            matches.append(segment)

    return sorted(matches, key=lambda segment: (_normalize_bbox(segment.get("bbox")) or (0, 0, 0, 0))[1])


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
        match = re.search(rf"\b{TR_HOUSE_CODE_PATTERN}\b", text)
        return match.group(0).strip() if match else None
    if key == "birthDate":
        return _extract_first_thai_date(text)
    if key == "age":
        match = re.search(r"[0-9๐-๙]{4}\s+([0-9๐-๙]{1,3})\b", text)
        return match.group(1).strip() if match else None
    if key == "address":
        match = re.search(r"((?:[0-9๐-๙]+\s+)?(?:ซอย|ถนน|แขวง|ตำบล|เขต|อำเภอ|จังหวัด|กรุงเทพ)[^#\n]{8,160})", text)
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
    return None


def _is_valid_tr_value(key: str, value: str | None) -> bool:
    if value is None:
        return False
    cleaned = _normalize_text(value)
    if not cleaned or cleaned in {"-", "#", "##########"}:
        return False

    if "ท้องถิ่น" in cleaned or "เขตหนองจอก" in cleaned:
        return False
    if key in {"personId", "motherId", "fatherId"}:
        return bool(re.fullmatch(r"[0-9๐-๙]-[0-9๐-๙]{4}-[0-9๐-๙]{4,5}-[0-9๐-๙]{1,2}-[0-9๐-๙]", cleaned))
    if key == "houseCode":
        return bool(re.fullmatch(r"[0-9๐-๙]{4}-[0-9๐-๙]{6}-[0-9๐-๙]", cleaned))
    if key == "gender":
        return cleaned in {"ชาย", "หญิง"}
    if key in {"nationality", "motherNationality", "fatherNationality"}:
        return bool(cleaned) and len(cleaned) <= 16 and not any(character.isdigit() for character in cleaned)
    if key == "age":
        return bool(re.fullmatch(r"[0-9๐-๙]{1,3}", cleaned))
    if key in {"birthDate", "moveInDate", "updateDate"}:
        return bool(re.fullmatch(r"[0-9๐-๙]{1,2}\s+\S+\s+[0-9๐-๙]{4}", cleaned))
    if key in {"personName", "motherName", "fatherName"}:
        if any(character.isdigit() for character in cleaned):
            return False
        if any(token in cleaned for token in ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน")):
            return False
        return 1 <= len(cleaned) <= 80
    if key == "status":
        return len(cleaned) <= 32
    return True


def _clean_field_value(value: str, key: str) -> str:
    cleaned = _normalize_text(value)
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
        "moveInDate": ("เข้ามาอยู่วันที่", "Address_In"),
        "remark": ("Remark",),
        "updateDate": ("Update Date", "UpDate"),
    }.get(key, ())
    for token in label_noise:
        cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :.-")
    return cleaned


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\r", "\n").split())


def _make_field(
    *,
    value: str | None,
    page_number: int | None,
    bbox: tuple[float, float, float, float] | None,
    source: str | None,
) -> dict[str, object]:
    return {
        "value": value,
        "pageNumber": page_number,
        "bbox": list(bbox) if bbox is not None else None,
        "source": source,
    }
