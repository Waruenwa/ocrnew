from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import Depends, Header, HTTPException


DEFAULT_STAFF_USER_IDS: tuple[str, ...] = ()
AUTH_STAFF_LIST_PATHS = (
    "/getuserstaff",
    "/users",
    "/getusers",
    "/getUsers",
    "/TUser",
    "/tuser",
    "/staff",
)


def _auth_service_base_url() -> str:
    return os.getenv("AUTH_SERVICE_BASE_URL", "http://localhost:5900").rstrip("/")


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    username: str
    role_ocr: str


@dataclass(frozen=True)
class StaffUser:
    user_id: str
    username: str
    display_name: str


def _extract_user_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}

    for key in ("user", "User", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    return payload


def _string_value(payload: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _extract_user_list(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    if _string_value(payload, "id", "user_id", "UserID", "UserId", "ID", "EUserName", "username"):
        return [payload]

    for key in ("users", "Users", "data", "Data", "result", "Result", "TUser"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_user_list(value)
            if nested:
                return nested
    return []


def _parse_staff_user_id_filter() -> set[str]:
    raw_value = os.getenv("AUTH_STAFF_USER_IDS") or os.getenv("AUTH_STAFF_USER_ID") or ""
    values = [value.strip() for value in raw_value.split(",") if value.strip()]
    return set(values or DEFAULT_STAFF_USER_IDS)


def _staff_list_urls(path: str, staff_user_ids: set[str]) -> list[str]:
    urls: list[str] = []
    base_url = _auth_service_base_url()
    for staff_user_id in sorted(staff_user_ids):
        for key in ("UserID", "user_id", "id"):
            urls.append(f"{base_url}{path}?{urlencode({key: staff_user_id})}")
    urls.append(f"{base_url}{path}")
    return urls


def _configured_staff_users(staff_user_ids: set[str]) -> list[StaffUser]:
    return [
        StaffUser(user_id=staff_user_id, username=staff_user_id, display_name=staff_user_id)
        for staff_user_id in sorted(staff_user_ids)
    ]


def _fetch_current_user(authorization: str) -> AuthenticatedUser:
    request = Request(
        f"{_auth_service_base_url()}/getprofile",
        headers={"Authorization": authorization},
        method="GET",
    )

    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise HTTPException(status_code=401, detail="Authentication required") from exc
        raise HTTPException(status_code=502, detail="Unable to verify authentication") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Unable to verify authentication") from exc

    raw_user = _extract_user_payload(payload)
    role_ocr = _string_value(raw_user, "Role_ocr", "role_ocr", "role").strip().lower()
    username = _string_value(raw_user, "EUserName", "username", "Username", "TUserName", "name")
    user_id = _string_value(raw_user, "id", "user_id", "UserID", "UserId", "ID") or username

    if not role_ocr:
        raise HTTPException(status_code=403, detail="Role_ocr is required")

    return AuthenticatedUser(user_id=user_id, username=username, role_ocr=role_ocr)


def require_authenticated_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthenticatedUser:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    return _fetch_current_user(authorization)


def require_manager_user(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    if current_user.role_ocr != "manager":
        raise HTTPException(status_code=403, detail="Manager role is required")
    return current_user


def require_staff_user(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    if current_user.role_ocr != "staff":
        raise HTTPException(status_code=403, detail="Staff role is required")
    return current_user


def list_staff_users_from_auth_source(authorization: str) -> list[StaffUser]:
    last_error: Exception | None = None
    staff_user_id_filter = _parse_staff_user_id_filter()
    found_user_list = False

    for path in AUTH_STAFF_LIST_PATHS:
        for url in _staff_list_urls(path, staff_user_id_filter):
            request = Request(
                url,
                headers={"Authorization": authorization},
                method="GET",
            )
            try:
                with urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                last_error = exc
                if exc.code in {401, 403}:
                    raise HTTPException(status_code=403, detail="Unable to list staff users") from exc
                continue
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                continue

            raw_users = _extract_user_list(payload)
            if not raw_users:
                continue
            found_user_list = True

            staff_users: list[StaffUser] = []
            seen: set[str] = set()
            for raw_user in raw_users:
                role_ocr = _string_value(raw_user, "Role_ocr", "role_ocr", "role").lower()
                username = _string_value(raw_user, "EUserName", "username", "Username", "TUserName", "name")
                user_id = _string_value(raw_user, "id", "user_id", "UserID", "UserId", "ID") or username
                if staff_user_id_filter:
                    is_staff_candidate = user_id in staff_user_id_filter
                else:
                    is_staff_candidate = role_ocr == "staff"
                if not is_staff_candidate:
                    continue
                display_name = _string_value(
                    raw_user,
                    "display_name",
                    "DisplayName",
                    "TUserName",
                    "EName",
                    "name",
                    "Name",
                ) or username or user_id
                if not user_id and not username:
                    continue
                key = user_id or username
                if key in seen:
                    continue
                seen.add(key)
                staff_users.append(
                    StaffUser(
                        user_id=user_id,
                        username=username or user_id,
                        display_name=display_name,
                    )
                )
            if staff_users:
                return staff_users
            if not staff_user_id_filter:
                return []

    configured_staff_users = _configured_staff_users(staff_user_id_filter)
    if configured_staff_users:
        return configured_staff_users

    if last_error is not None:
        raise HTTPException(status_code=502, detail="Unable to list staff users") from last_error
    if found_user_list:
        return []
    return []
