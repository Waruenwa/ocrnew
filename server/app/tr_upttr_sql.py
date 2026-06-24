from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException


TR_UPTTR_TABLE = "dbo.TT_UpTTR"

TR_UPTTR_FIELD_COLUMNS = {
    "personId": "PIE",
    "houseCode": "PHIE",
    "personName": "PNBme",
    "birthDate": "PBE",
    "age": "PAhe",
    "gender": "PSDX",
    "nationality": "PNbtionality",
    "status": "PAdStatus",
    "motherName": "MNBme",
    "motherId": "MIE",
    "motherNationality": "MNbtionality",
    "fatherName": "DNBme",
    "fatherId": "DIE",
    "fatherNationality": "DNbtionality",
    "address": "AEdress",
    "postalCode": "PPstalCode",
    "moveInDate": "AEdress_In",
    "remark": "Remark",
    "updateDate": "UpEate",
}

TR_UPTTR_REQUIRED_FIELDS = (
    "personId",
    "houseCode",
    "personName",
)

THAI_MONTHS = {
    "\u0e21\u0e01\u0e23\u0e32\u0e04\u0e21": 1,
    "\u0e01\u0e38\u0e21\u0e20\u0e32\u0e1e\u0e31\u0e19\u0e18\u0e4c": 2,
    "\u0e01\u0e38\u0e21\u0e20\u0e32\u0e1e\u0e31\u0e19\u0e18\u0e4c": 2,
    "\u0e21\u0e35\u0e19\u0e32\u0e04\u0e21": 3,
    "\u0e40\u0e21\u0e29\u0e32\u0e22\u0e19": 4,
    "\u0e1e\u0e24\u0e29\u0e20\u0e32\u0e04\u0e21": 5,
    "\u0e21\u0e34\u0e16\u0e38\u0e19\u0e32\u0e22\u0e19": 6,
    "\u0e01\u0e23\u0e01\u0e0e\u0e32\u0e04\u0e21": 7,
    "\u0e2a\u0e34\u0e07\u0e2b\u0e32\u0e04\u0e21": 8,
    "\u0e01\u0e31\u0e19\u0e22\u0e32\u0e22\u0e19": 9,
    "\u0e15\u0e38\u0e25\u0e32\u0e04\u0e21": 10,
    "\u0e1e\u0e24\u0e28\u0e08\u0e34\u0e01\u0e32\u0e22\u0e19": 11,
    "\u0e18\u0e31\u0e19\u0e27\u0e32\u0e04\u0e21": 12,
}


def insert_tr_upttr_from_review_result(
    corrected_result: Any,
    *,
    updated_by: str | None,
) -> None:
    fields = _extract_fields(corrected_result)
    missing_fields = [
        field_name for field_name in TR_UPTTR_REQUIRED_FIELDS if not _field_value(fields, field_name)
    ]
    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail=f"TR result is missing required field(s): {', '.join(missing_fields)}",
        )

    connection_string = _get_connection_string()
    if not connection_string:
        raise HTTPException(
            status_code=503,
            detail=(
                "SQL Server connection is not configured. Set MSSQL_CONNECTION_STRING "
                "or MSSQL_SERVER/MSSQL_DATABASE/MSSQL_USERNAME/MSSQL_PASSWORD."
            ),
        )

    try:
        import pyodbc
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="pyodbc is not installed on the server. Install server requirements again.",
        ) from exc

    now = datetime.now(timezone.utc)
    flags = corrected_result.get("flags") if isinstance(corrected_result, dict) else None
    deceased_date = None
    if isinstance(flags, dict):
        deceased_date = _parse_thai_date(str(flags.get("deceasedDate") or ""))

    address = _field_value(fields, "address")
    address_parts = _split_tr_address(address)
    postal_code = _field_value(fields, "postalCode") or str(address_parts["postal_code"] or "")
    values = {
        "UpEate": _parse_thai_date(_field_value(fields, "updateDate")),
        "PIE": _compact_person_id(_field_value(fields, "personId")),
        "PHIE": _compact_house_code(_field_value(fields, "houseCode")),
        "PNBme": _field_value(fields, "personName"),
        "PBE": _parse_thai_date(_field_value(fields, "birthDate")),
        "PAhe": _parse_int(_field_value(fields, "age")),
        "PSDX": _field_value(fields, "gender"),
        "PNbtionality": _field_value(fields, "nationality"),
        "PAdStatus": _field_value(fields, "status"),
        "MNBme": _field_value(fields, "motherName"),
        "MIE": _compact_person_id(_field_value(fields, "motherId")),
        "MNbtionality": _field_value(fields, "motherNationality"),
        "DNBme": _field_value(fields, "fatherName"),
        "DIE": _compact_person_id(_field_value(fields, "fatherId")),
        "DNbtionality": _field_value(fields, "fatherNationality"),
        "AEdress": address,
        "AEdress1": address_parts["address1"],
        "AEdress2": address_parts["address2"],
        "RPad": address_parts["road"],
        "PSovince": address_parts["province"],
        "ANphur": address_parts["amphur"],
        "DJstrict": address_parts["district"],
        "PPstalCode": _normalize_postal_code(postal_code),
        "AEdress_In": _parse_thai_date(_field_value(fields, "moveInDate")),
        "Renark": _field_value(fields, "remark"),
        "PStatus": "\u0e40\u0e2a\u0e35\u0e22\u0e0a\u0e35\u0e27\u0e34\u0e15"
        if _is_deceased(corrected_result)
        else None,
        "UpdatedDate": now,
        "UpdatedBy": (updated_by or "")[:5] or None,
        "UpdatedTime": now.strftime("%H:%M:%S"),
        "Renark_DED": "Y" if _is_deceased(corrected_result) else None,
        "Renark_DED_Date": deceased_date,
    }

    columns = list(values)
    column_sql = ", ".join(f"[{column}]" for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {TR_UPTTR_TABLE} ({column_sql}) VALUES ({placeholders})"

    try:
        with pyodbc.connect(connection_string, timeout=10) as connection:
            cursor = connection.cursor()
            cursor.execute(sql, [values[column] for column in columns])
            connection.commit()
    except pyodbc.Error as exc:
        raise HTTPException(status_code=502, detail=f"SQL Server insert failed: {exc}") from exc


def _get_connection_string() -> str | None:
    explicit = os.getenv("MSSQL_CONNECTION_STRING")
    if explicit:
        return explicit

    server = os.getenv("MSSQL_SERVER")
    database = os.getenv("MSSQL_DATABASE")
    username = os.getenv("MSSQL_USERNAME")
    password = os.getenv("MSSQL_PASSWORD")
    if not server or not database:
        return None

    driver = os.getenv("MSSQL_DRIVER", "ODBC Driver 17 for SQL Server")
    trust_cert = os.getenv("MSSQL_TRUST_SERVER_CERTIFICATE", "yes")
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
        f"TrustServerCertificate={trust_cert}",
    ]
    if username:
        parts.extend([f"UID={username}", f"PWD={password or ''}"])
    else:
        parts.append("Trusted_Connection=yes")
    return ";".join(parts) + ";"


def _extract_fields(corrected_result: Any) -> dict[str, Any]:
    if not isinstance(corrected_result, dict):
        raise HTTPException(status_code=400, detail="corrected_result must be an object")
    raw_fields = corrected_result.get("fields")
    if not isinstance(raw_fields, dict):
        raise HTTPException(status_code=400, detail="corrected_result.fields is required")
    return raw_fields


def _field_value(fields: dict[str, Any], key: str) -> str:
    raw_field = fields.get(key)
    if isinstance(raw_field, dict):
        raw_value = raw_field.get("value")
    else:
        raw_value = raw_field
    if raw_value is None:
        return ""
    value = str(raw_value).strip()
    return "" if value in {"-", "#", "##########"} else value


def _compact_person_id(value: str) -> str:
    return _compact_digits(value)


def _compact_house_code(value: str) -> str:
    return _compact_digits(value)


def _compact_digits(value: str) -> str:
    return re.sub(r"\D", "", _thai_digits_to_ascii(value))


def _normalize_postal_code(value: str) -> str | None:
    digits = _compact_digits(value)
    return digits[:5] if len(digits) >= 5 else None


def _thai_digits_to_ascii(value: str) -> str:
    return value.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789"))


def _split_tr_address(value: str) -> dict[str, str | None]:
    address = _normalize_address(value)
    parts: dict[str, str | None] = {
        "address1": None,
        "address2": None,
        "road": None,
        "province": None,
        "amphur": None,
        "district": None,
        "postal_code": None,
    }
    if not address:
        return parts

    postal_match = re.search(r"(?:^|\s)(\d{5})(?=\s*$)", address)
    if postal_match:
        parts["postal_code"] = postal_match.group(1)
        address = _remove_match(address, postal_match)

    street_address, address = _take_street_address_part(address)
    parts["address2"] = street_address
    parts["road"] = _extract_road_name(street_address)
    parts["district"], address = _take_address_part(
        address,
        r"(?:ต\.|ตำบล|แขวง)\s*([^\s]+)",
    )
    parts["amphur"], address = _take_address_part(
        address,
        r"(?:อ\.|อำเภอ|เขต)\s*([^\s]+)",
    )
    parts["province"], address = _take_address_part(
        address,
        r"(?:จ\.|จังหวัด)\s*([^\s]+)",
    )

    if not parts["province"]:
        bangkok_match = re.search(r"(?:^|\s)(กรุงเทพมหานคร)(?=\s|$)", address)
        if bangkok_match:
            parts["province"] = bangkok_match.group(1)
            address = _remove_match(address, bangkok_match)

    address1, address2_overflow = _split_address_lines(address)
    if address2_overflow:
        parts["address2"] = _join_address_parts(parts["address2"], address2_overflow)
    parts["address1"] = _truncate_or_none(address1, 50)
    parts["address2"] = _truncate_or_none(parts["address2"], 100)
    parts["road"] = _truncate_or_none(parts["road"], 30)
    parts["province"] = _truncate_or_none(parts["province"], 50)
    parts["amphur"] = _truncate_or_none(parts["amphur"], 50)
    parts["district"] = _truncate_or_none(parts["district"], 50)
    parts["postal_code"] = _truncate_or_none(parts["postal_code"], 5)
    return parts


def _take_street_address_part(address: str) -> tuple[str | None, str]:
    match = re.search(
        r"((?:(?:ซ\.|ซอย|ถ\.|ถนน|ตรอก|ทาง)\s*[\S ]+?)(?=\s+(?:ต\.|ตำบล|แขวง|อ\.|อำเภอ|เขต|จ\.|จังหวัด|\d{5})|$))",
        address,
    )
    if not match:
        return None, address
    return _normalize_address(match.group(1)), _remove_match(address, match)


def _extract_road_name(street_address: str | None) -> str | None:
    if not street_address:
        return None
    match = re.search(
        r"(?:ถ\.|ถนน)\s*([\S ]+?)(?=\s+(?:ซ\.|ซอย|ตรอก|ทาง)|$)",
        street_address,
    )
    if not match:
        return None
    return _normalize_address(match.group(1))


def _join_address_parts(first: str | None, second: str | None) -> str | None:
    joined = _normalize_address(" ".join(part for part in (first, second) if part))
    return joined or None


def _take_address_part(address: str, pattern: str) -> tuple[str | None, str]:
    match = re.search(pattern, address)
    if not match:
        return None, address
    return _normalize_address(match.group(1)), _remove_match(address, match)


def _remove_match(address: str, match: re.Match[str]) -> str:
    return _normalize_address(f"{address[:match.start()]} {address[match.end():]}")


def _split_address_lines(address: str) -> tuple[str | None, str | None]:
    address = _normalize_address(address)
    if not address:
        return None, None
    if len(address) <= 50:
        return address, None

    split_at = address.rfind(" ", 0, 51)
    if split_at <= 0:
        split_at = 50
    return address[:split_at].strip(), address[split_at:].strip() or None


def _normalize_address(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value).replace(",", " ")).strip()


def _truncate_or_none(value: str | None, max_length: int) -> str | None:
    if not value:
        return None
    return value[:max_length]


def _parse_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid integer value: {value}") from None


def _parse_thai_date(value: str) -> date | None:
    if not value:
        return None
    parts = value.split()
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail=f"Invalid Thai date value: {value}")
    day_text, month_text, year_text = parts
    month = THAI_MONTHS.get(month_text)
    if month is None:
        raise HTTPException(status_code=400, detail=f"Invalid Thai month value: {month_text}")
    try:
        day = int(day_text)
        year = int(year_text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid Thai date value: {value}") from None
    if year > 2400:
        year -= 543
    try:
        return date(year, month, day)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid Thai date value: {value}") from None


def _is_deceased(corrected_result: Any) -> bool:
    if not isinstance(corrected_result, dict):
        return False
    flags = corrected_result.get("flags")
    if (
        isinstance(flags, dict)
        and flags.get("deceased") is True
        and str(flags.get("deceasedDate") or "").strip()
    ):
        return True
    fields = corrected_result.get("fields")
    if not isinstance(fields, dict):
        return False
    remark = _field_value(fields, "remark")
    if re.search(r"(?:ไม่|ไม่ได้|มิได้).{0,12}(?:ตาย|เสียชีวิต)", remark):
        return False
    return bool(
        re.search(r"(?:ตาย|เสียชีวิต).{0,80}[0-9๐-๙]{1,2}\s+\S+\s+[0-9๐-๙]{4}", remark)
    )
