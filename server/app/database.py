from __future__ import annotations

from functools import lru_cache

from pymongo import MongoClient
from pymongo.collection import Collection

from app.config import load_settings


@lru_cache(maxsize=4)
def _build_mongo_client(uri: str) -> MongoClient:
    return MongoClient(uri, serverSelectionTimeoutMS=5000)


def get_jobs_collection() -> Collection:
    settings = load_settings()
    client = _build_mongo_client(settings.mongodb_uri)
    database = client[settings.mongodb_database]
    return database[settings.mongodb_jobs_collection]


def get_imports_collection() -> Collection:
    settings = load_settings()
    client = _build_mongo_client(settings.mongodb_uri)
    database = client[settings.mongodb_database]
    return database[settings.mongodb_imports_collection]


def init_db() -> None:
    jobs_collection = get_jobs_collection()
    imports_collection = get_imports_collection()
    jobs_collection.database.client.admin.command("ping")
    jobs_collection.create_index("created_at")
    jobs_collection.create_index("updated_at")
    imports_collection.create_index("created_at")
    imports_collection.create_index("updated_at")
    imports_collection.create_index("status")
    imports_collection.create_index("document_category")
    imports_collection.create_index("source_fingerprint", unique=True)
