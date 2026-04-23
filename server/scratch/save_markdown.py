from pymongo import MongoClient
import json

client = MongoClient("mongodb://127.0.0.1:27017")
db = client.ocrstudio
collection = db.ocr_imports

target_id = "530b5da5cc524466814a2004a2ea8ddb"
doc = collection.find_one({"_id": target_id})

if doc and doc.get("ocr_markdown"):
    with open("scratch/ocr_result.md", "w", encoding="utf-8") as f:
        f.write(doc["ocr_markdown"])
    print("Saved markdown to scratch/ocr_result.md")
else:
    print("Document or markdown not found.")
