from pymongo import MongoClient

client = MongoClient("mongodb://127.0.0.1:27017")
db = client.ocrstudio
collection = db.ocr_imports

target_id = "530b5da5cc524466814a2004a2ea8ddb"
doc = collection.find_one({"_id": target_id})

if doc:
    print(f"Status: {doc.get('status')}")
else:
    print("Not found")
