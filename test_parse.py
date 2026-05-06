import json
from server.pdf_to_excel import parse_response

content = open("server/3.png_debug.txt", "r", encoding="utf-8").read()
rows = parse_response(content, "3.png")
print("Number of rows:", len(rows))
