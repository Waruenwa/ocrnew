import urllib.request
import json
from pymongo import MongoClient

client = MongoClient("mongodb://127.0.0.1:27017")
db = client.ocrstudio
collection = db.ocr_imports

# Find the latest import
latest_import = collection.find_one(sort=[("updated_at", -1)])

if latest_import:
    import_id = latest_import["_id"]
    print(f"Retrying OCR for Import ID: {import_id}")
    
    url = f"http://127.0.0.1:8000/api/imports/{import_id}/retry-ocr"
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print("Successfully triggered retry!")
                print(json.loads(response.read().decode()))
            else:
                print(f"Failed to retry. Status code: {response.status}")
    except Exception as e:
        print(f"Error: {e}")
else:
    print("No imports found.")
