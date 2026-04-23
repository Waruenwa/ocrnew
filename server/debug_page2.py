from app.import_pipeline import get_imports_collection

doc = get_imports_collection().find_one({"_id": "f27e80f9cd07493db2622eb0c5dbfb17"})
pages = doc.get("pages", [])
for p in pages:
    if p.get("page_number") == 2:
        # Check corrected_markdown vs markdown
        md = p.get("markdown", "")
        corrected = p.get("corrected_markdown", "")
        raw = p.get("raw_markdown", "")
        
        print("=== MARKDOWN (used for segments) ===")
        non_empty = [l.strip() for l in md.split("\n") if l.strip()]
        for i, l in enumerate(non_empty, 1):
            print(f"  {i}: {l[:100]}")
        
        print(f"\n=== CORRECTED MARKDOWN ===")
        if corrected:
            non_empty2 = [l.strip() for l in corrected.split("\n") if l.strip()]
            for i, l in enumerate(non_empty2, 1):
                print(f"  {i}: {l[:100]}")
        else:
            print("  (empty)")
            
        print(f"\n=== RAW MARKDOWN ===")
        if raw:
            non_empty3 = [l.strip() for l in raw.split("\n") if l.strip()]
            for i, l in enumerate(non_empty3, 1):
                print(f"  {i}: {l[:100]}")
        else:
            print("  (empty)")
        
        print(f"\nMD == CORRECTED: {md == corrected}")
        print(f"MD == RAW: {md == raw}")
        break
