import json
from server.pdf_to_excel import flat_list_to_rows

content = open("server/3.png_debug.txt", "r", encoding="utf-8").read()
try:
    obj = json.loads(content)
    print("Keys:", obj.keys())
    for k, v in obj.items():
        if isinstance(v, list):
            print(f"List length: {len(v)}")
            rows = flat_list_to_rows(v)
            print("Rows from flat_list_to_rows:", len(rows))
except Exception as e:
    print("Error:", e)
