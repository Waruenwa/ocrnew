from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.core.config import Settings, load_settings


class DuplicateKeyError(Exception):
    """Raised when SQL document storage hits a duplicate document key."""


class _SqlDocumentCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, field_name: str, direction: int) -> "_SqlDocumentCursor":
        reverse = direction < 0

        def sort_key(document: dict[str, Any]) -> Any:
            value = document.get(field_name)
            return "" if value is None else value

        self._documents.sort(key=sort_key, reverse=reverse)
        return self

    def limit(self, limit: int) -> "_SqlDocumentCursor":
        if limit >= 0:
            self._documents = self._documents[:limit]
        return self

    def __iter__(self):
        return iter(self._documents)


class _SqlDocumentCollection:
    def __init__(self, settings: Settings, record_type: str) -> None:
        self._settings = settings
        self._record_type = record_type
        self._table_name = settings.mssql_ocrdata_table
        self._table_sql = _quote_table_name(self._table_name)

    def create_index(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def find(
        self,
        query: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> _SqlDocumentCursor:
        documents = [
            self._apply_projection(document, projection)
            for document in self._load_all_documents()
            if _matches_query(document, query or {})
        ]
        return _SqlDocumentCursor(documents)

    def find_one(
        self,
        query: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        for document in self.find(query, projection):
            return document
        return None

    def insert_one(self, document: dict[str, Any]) -> None:
        normalized = self._normalize_document(document)
        self._raise_for_duplicate(normalized)
        self._insert_document(normalized)

    def replace_one(
        self,
        filter_query: dict[str, Any],
        document: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> None:
        current = self.find_one(filter_query)
        normalized = self._normalize_document(document)
        if current is None:
            if upsert:
                self.insert_one(normalized)
            return
        self._update_document_by_key(str(current["_id"]), normalized)

    def update_one(self, filter_query: dict[str, Any], update: dict[str, Any]) -> None:
        current = self.find_one(filter_query)
        if current is None:
            return
        updated = deepcopy(current)
        set_values = update.get("$set")
        if isinstance(set_values, dict):
            updated.update(set_values)
        self._update_document_by_key(str(current["_id"]), self._normalize_document(updated))

    def _apply_projection(
        self,
        document: dict[str, Any],
        projection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not projection:
            return deepcopy(document)
        included_fields = {field for field, include in projection.items() if include}
        if not included_fields:
            return deepcopy(document)
        projected = {field: deepcopy(document[field]) for field in included_fields if field in document}
        if "_id" in document and projection.get("_id", 1):
            projected["_id"] = document["_id"]
        return projected

    def _normalize_document(self, document: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(document)
        record_key = str(normalized.get("_id") or normalized.get("id") or "").strip()
        if not record_key:
            raise ValueError("SQL document records require _id or id")
        normalized["_id"] = record_key
        normalized.setdefault("id", record_key)
        return normalized

    def _metadata_values(self, document: dict[str, Any]) -> dict[str, Any]:
        return {
            "record_type": self._record_type,
            "record_key": str(document.get("_id") or document.get("id") or ""),
            "source_fingerprint": document.get("source_fingerprint"),
            "status": document.get("status"),
            "document_category": document.get("document_category"),
            "created_at": document.get("created_at"),
            "updated_at": document.get("updated_at"),
            "payload_json": json.dumps(document, ensure_ascii=False, default=str),
            "save_btn": str(document.get("save_btn") or "N").strip().upper()[:1] or "N",
        }

    def _load_all_documents(self) -> list[dict[str, Any]]:
        sql = f"SELECT payload_json FROM {self._table_sql} WHERE record_type = ?"
        with _connect(self._settings) as connection:
            cursor = connection.cursor()
            rows = cursor.execute(sql, self._record_type).fetchall()
        documents: list[dict[str, Any]] = []
        for row in rows:
            payload_json = row[0]
            if not payload_json:
                continue
            try:
                document = json.loads(payload_json)
            except json.JSONDecodeError:
                continue
            if isinstance(document, dict):
                documents.append(document)
        return documents

    def _raise_for_duplicate(self, document: dict[str, Any]) -> None:
        record_key = str(document["_id"])
        existing = self.find_one({"_id": record_key})
        if existing is not None:
            raise DuplicateKeyError(f"Duplicate record key: {record_key}")
        source_fingerprint = document.get("source_fingerprint")
        if source_fingerprint and self.find_one({"source_fingerprint": source_fingerprint}) is not None:
            raise DuplicateKeyError(f"Duplicate source fingerprint: {source_fingerprint}")

    def _insert_document(self, document: dict[str, Any]) -> None:
        values = self._metadata_values(document)
        with _connect(self._settings) as connection:
            cursor = connection.cursor()
            if not self._id_column_is_identity(cursor):
                next_id = cursor.execute(
                    f"SELECT ISNULL(MAX([id]), 0) + 1 FROM {self._table_sql} WITH (UPDLOCK, HOLDLOCK)"
                ).fetchone()[0]
                values = {"id": next_id, **values}
            columns = list(values)
            placeholders = ", ".join("?" for _ in columns)
            column_sql = ", ".join(f"[{column}]" for column in columns)
            sql = f"INSERT INTO {self._table_sql} ({column_sql}) VALUES ({placeholders})"
            cursor.execute(sql, *[values[column] for column in columns])
            connection.commit()

    def _update_document_by_key(self, record_key: str, document: dict[str, Any]) -> None:
        values = self._metadata_values(document)
        set_sql = ", ".join(f"[{column}] = ?" for column in values)
        sql = f"""
            UPDATE {self._table_sql}
            SET {set_sql}
            WHERE record_type = ? AND record_key = ?
        """
        with _connect(self._settings) as connection:
            cursor = connection.cursor()
            cursor.execute(sql, *[*values.values(), self._record_type, record_key])
            connection.commit()

    def _id_column_is_identity(self, cursor: Any) -> bool:
        object_name = _object_name_literal(self._table_name)
        row = cursor.execute(
            f"SELECT COLUMNPROPERTY(OBJECT_ID(N'{object_name}'), 'id', 'IsIdentity')"
        ).fetchone()
        return bool(row and row[0] == 1)


def _get_connection_string(settings: Settings) -> str | None:
    if settings.mssql_connection_string:
        return settings.mssql_connection_string
    if not settings.mssql_server or not settings.mssql_database:
        return None

    parts = [
        f"DRIVER={{{settings.mssql_driver}}}",
        f"SERVER={settings.mssql_server}",
        f"DATABASE={settings.mssql_database}",
        f"TrustServerCertificate={settings.mssql_trust_server_certificate}",
    ]
    if settings.mssql_username:
        parts.extend([f"UID={settings.mssql_username}", f"PWD={settings.mssql_password or ''}"])
    else:
        parts.append("Trusted_Connection=yes")
    return ";".join(parts) + ";"


def _connect(settings: Settings):
    connection_string = _get_connection_string(settings)
    if not connection_string:
        raise RuntimeError(
            "SQL Server storage is selected, but MSSQL connection settings are missing."
        )
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError("pyodbc is required for SQL Server storage.") from exc
    return pyodbc.connect(connection_string, timeout=10)


def _quote_table_name(table_name: str) -> str:
    parts = [part.strip("[] ") for part in table_name.split(".") if part.strip("[] ")]
    if len(parts) == 1:
        parts.insert(0, "dbo")
    return ".".join(f"[{part.replace(']', ']]')}]" for part in parts)


def _object_name_literal(table_name: str) -> str:
    parts = [part.strip("[] ") for part in table_name.split(".") if part.strip("[] ")]
    if len(parts) == 1:
        parts.insert(0, "dbo")
    return ".".join(parts).replace("'", "''")


def _matches_query(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not isinstance(expected, list):
                return False
            if not any(_matches_query(document, item) for item in expected if isinstance(item, dict)):
                return False
            continue

        exists = key in document
        actual = document.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                expected_values = expected.get("$in")
                if not isinstance(expected_values, list) or actual not in expected_values:
                    return False
            if "$exists" in expected:
                if exists != bool(expected["$exists"]):
                    return False
            continue

        if actual != expected:
            return False
    return True


def _init_sql_document_store(settings: Settings) -> None:
    table_sql = _quote_table_name(settings.mssql_ocrdata_table)
    object_name = _object_name_literal(settings.mssql_ocrdata_table)
    statements = [
        f"""
        IF OBJECT_ID(N'{object_name}', N'U') IS NULL
        BEGIN
            CREATE TABLE {table_sql} (
                [id] int IDENTITY(1,1) NOT NULL PRIMARY KEY
            );
        END;
        """,
        f"""
        IF COL_LENGTH(N'{object_name}', 'record_type') IS NULL
            ALTER TABLE {table_sql} ADD [record_type] nvarchar(30) NULL;
        """,
        f"""
        IF COL_LENGTH(N'{object_name}', 'record_key') IS NULL
            ALTER TABLE {table_sql} ADD [record_key] nvarchar(100) NULL;
        """,
        f"""
        IF COL_LENGTH(N'{object_name}', 'source_fingerprint') IS NULL
            ALTER TABLE {table_sql} ADD [source_fingerprint] nvarchar(200) NULL;
        """,
        f"""
        IF COL_LENGTH(N'{object_name}', 'status') IS NULL
            ALTER TABLE {table_sql} ADD [status] nvarchar(50) NULL;
        """,
        f"""
        IF COL_LENGTH(N'{object_name}', 'document_category') IS NULL
            ALTER TABLE {table_sql} ADD [document_category] nvarchar(100) NULL;
        """,
        f"""
        IF COL_LENGTH(N'{object_name}', 'created_at') IS NULL
            ALTER TABLE {table_sql} ADD [created_at] nvarchar(64) NULL;
        """,
        f"""
        IF COL_LENGTH(N'{object_name}', 'updated_at') IS NULL
            ALTER TABLE {table_sql} ADD [updated_at] nvarchar(64) NULL;
        """,
        f"""
        IF COL_LENGTH(N'{object_name}', 'payload_json') IS NULL
            ALTER TABLE {table_sql} ADD [payload_json] nvarchar(max) NULL;
        """,
        f"""
        IF COL_LENGTH(N'{object_name}', 'save_btn') IS NULL
            ALTER TABLE {table_sql} ADD [save_btn] nchar(10) NULL;
        """,
        f"""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = N'UX_ocrdata_record_type_key'
              AND object_id = OBJECT_ID(N'{object_name}')
        )
            CREATE UNIQUE INDEX [UX_ocrdata_record_type_key]
            ON {table_sql} ([record_type], [record_key])
            WHERE [record_type] IS NOT NULL AND [record_key] IS NOT NULL;
        """,
        f"""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = N'IX_ocrdata_record_type_updated_at'
              AND object_id = OBJECT_ID(N'{object_name}')
        )
            CREATE INDEX [IX_ocrdata_record_type_updated_at]
            ON {table_sql} ([record_type], [updated_at]);
        """,
        f"""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = N'UX_ocrdata_import_source_fingerprint'
              AND object_id = OBJECT_ID(N'{object_name}')
        )
            CREATE UNIQUE INDEX [UX_ocrdata_import_source_fingerprint]
            ON {table_sql} ([source_fingerprint])
            WHERE [record_type] = N'imports' AND [source_fingerprint] IS NOT NULL;
        """,
    ]
    with _connect(settings) as connection:
        cursor = connection.cursor()
        for statement in statements:
            cursor.execute(statement)
        connection.commit()


def get_jobs_collection() -> _SqlDocumentCollection:
    settings = load_settings()
    return _SqlDocumentCollection(settings, "jobs")


def get_imports_collection() -> _SqlDocumentCollection:
    settings = load_settings()
    return _SqlDocumentCollection(settings, "imports")


def init_db() -> None:
    settings = load_settings()
    _init_sql_document_store(settings)
