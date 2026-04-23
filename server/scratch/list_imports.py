from pymongo import MongoClient

client = MongoClient("mongodb://127.0.0.1:27017")
db = client.ocrstudio
collection = db.ocr_imports

for doc in collection.find().sort("updated_at", -1).limit(5):
    print(f"ID: {doc['_id']} | Filename: {doc.get('source_filename')} | Updated: {doc.get('updated_at')}")
