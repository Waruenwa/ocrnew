from pymongo import MongoClient

client = MongoClient("mongodb://127.0.0.1:27017")
db = client.ocrstudio
collection = db.ocr_imports

target_id = "530b5da5cc524466814a2004a2ea8ddb"
result = collection.update_one({"_id": target_id}, {"$set": {"status": "uploaded"}})

if result.modified_count > 0:
    print(f"Status reset to 'uploaded' for {target_id}")
else:
    print("No changes made or document not found.")
