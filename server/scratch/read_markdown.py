import sys

# Set encoding to utf-8 for stdout
sys.stdout.reconfigure(encoding='utf-8')

with open("scratch/ocr_result.md", "r", encoding="utf-8") as f:
    content = f.read()
    # Print first 2000 chars
    print(content[:2000])
