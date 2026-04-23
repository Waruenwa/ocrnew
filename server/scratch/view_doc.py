from pymongo import MongoClient
import json
from bson import ObjectId

client = MongoClient("mongodb://127.0.0.1:27017")
db = client.ocrstudio
collection = db.ocr_imports

target_id = "530b5da5cc524466814a2004a2ea8ddb"
doc = collection.find_one({"_id": target_id})

if doc:
    # Print keys
    print(f"Keys: {list(doc.keys())}")
    
    # Check specifically for extraction related fields
    for key in ["extracted_data", "extracted_values", "case_details", "result"]:
        if key in doc:
            print(f"\n--- {key} ---")
            print(json.dumps(doc[key], indent=2, ensure_ascii=False))
            
    # Also check ocr_markdown if it contains "คำพิพากษา"
    ocr_markdown = doc.get("ocr_markdown", "")
    if ocr_markdown and "คำพิพากษา" in ocr_markdown:
        print("\nFound 'คำพิพากษา' in ocr_markdown")
        # Print a bit of context
        idx = ocr_markdown.find("คำพิพากษา")
        print(ocr_markdown[idx:idx+200])
else:
    print("Document not found.")
