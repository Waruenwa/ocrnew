from pymongo import MongoClient
import json

client = MongoClient("mongodb://127.0.0.1:27017")
db = client.ocrstudio
collection = db.ocr_imports

# Find the specific import from the logs
target_id = "26b2633d88d748f6bdc790441ccc6503"
doc = collection.find_one({"_id": target_id})

if not doc:
    # If not found, get the latest one
    doc = collection.find_one(sort=[("updated_at", -1)])

if doc:
    print(f"Import ID: {doc['_id']}")
    print(f"Source Filename: {doc.get('source_filename')}")
    print(f"Status: {doc.get('status')}")
    
    ocr_markdown = doc.get("ocr_markdown")
    if ocr_markdown:
        print("\n--- Markdown Preview (First 20 lines) ---")
        # Use repr to see exact characters if encoding is weird
        lines = ocr_markdown.splitlines()
        for line in lines[:20]:
            print(line)
    else:
        print("\nocr_markdown is None or Empty.")
        
    # Check pages
    pages = doc.get("pages", [])
    if pages:
        print(f"\nTotal pages in DB: {len(pages)}")
        first_page = pages[0]
        print(f"Page 1 Markdown: {repr(first_page.get('markdown'))[:200]}...")
else:
    print("No document found.")
