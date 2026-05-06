import sys
import os
import argparse
import base64
import csv
import json
import io
import re
import urllib.request
import pandas as pd
from pathlib import Path
from PIL import Image
from urllib.parse import urlparse

# ============================================================
# CONFIG - บังคับใช้ API Key และ Model นี้เท่านั้นเพื่อให้ได้ผลลัพธ์ที่ถูกต้อง
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


def load_dotenv() -> None:
    for env_path in (PROJECT_DIR / ".env", BASE_DIR / ".env"):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value


def get_first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def env_csv(name: str, default: list[str]) -> list[str]:
    raw_value = os.getenv(name)
    if not raw_value:
        return default
    values = [value.strip() for value in raw_value.split(",") if value.strip()]
    return values or default


load_dotenv()

API_KEY = get_first_env(
    "PDF_TO_EXCEL_API_KEY",
    "TYPHOON_API_KEY",
    "TYPHOON_OCR_API_KEY",
    "OCR_API_KEY",
)
API_URL = get_first_env("PDF_TO_EXCEL_API_URL", "OCR_BASE_URL") or "https://api.opentyphoon.ai/v1/chat/completions"
MODEL = get_first_env("PDF_TO_EXCEL_MODEL", "OCR_MODEL") or "typhoon-ocr"
IMAGES = env_csv("PDF_TO_EXCEL_IMAGES", ["23.png"])
OUTPUT = os.getenv("PDF_TO_EXCEL_OUTPUT", "reportexcel.xlsx")
CHUNK_ROWS = int(os.getenv("PDF_TO_EXCEL_CHUNK_ROWS", "8") or "0")
# ============================================================

PROMPT = """Extract the visible table rows from this image.
The image may NOT show the column header row. Treat each horizontal line as one data row.

Return ONLY a raw JSON array. Do not return a header object.

Use exactly these 8 keys for each row:
"No", "OA", "Account No.", "Customer Name", "วันที่ต้องยื่นขอเฉลี่ยทรัพย์", "Billcode Update", "จำนวนวันถึงปัจจุบัน", "TAT to day"

Rules:
- Output only JSON array, for example: [{"No":"1","OA":"1007","Account No.":"4505787009503032","Customer Name":"...","วันที่ต้องยื่นขอเฉลี่ยทรัพย์":"06-Feb-26","Billcode Update":"67097","จำนวนวันถึงปัจจุบัน":"83","TAT to day":"2.61-90"}]
- Include every visible row.
- Keep all values as strings.
- Empty cells must be "".
- Do not add markdown or explanation."""


def image_to_base64(path: str) -> str:
    with Image.open(path) as img:
        return pil_image_to_base64(img)


def pil_image_to_base64(img: Image.Image) -> str:
    img = img.convert("RGB")
    max_dim = 2000
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return b64


def is_ollama_generate_url(raw_url: str) -> bool:
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return False
    return parsed.path.rstrip("/") == "/api/generate"


def call_ollama_generate(b64_image: str, api_url: str, model: str) -> str:
    payload = {
        "model": model,
        "prompt": PROMPT,
        "images": [b64_image],
        "stream": False,
        "options": {
            "temperature": 0.0,
            "top_p": 0.6,
            "repeat_penalty": 1.15,
            "num_predict": int(get_first_env("PDF_TO_EXCEL_NUM_PREDICT", "OCR_NUM_PREDICT") or "4096"),
        },
    }

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    timeout_seconds = int(get_first_env("PDF_TO_EXCEL_TIMEOUT_SECONDS", "OCR_TIMEOUT_SECONDS") or "300")
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return str(result.get("response") or "").strip()


def call_chat_completions(b64_image: str, api_key: str, api_url: str, model: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                    },
                ],
            }
        ],
        "max_tokens": 4096,
        "temperature": 0.0,
    }

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"].strip()


def call_api(b64_image: str, api_key: str | None, api_url: str, model: str) -> str:
    if is_ollama_generate_url(api_url):
        return call_ollama_generate(b64_image, api_url, model)

    if not api_key:
        raise RuntimeError(
            "Missing API key for chat completions endpoint. Set PDF_TO_EXCEL_API_KEY "
            "or use OCR_BASE_URL ending with /api/generate."
        )
    return call_chat_completions(b64_image, api_key, api_url, model)


def parse_html_table(html: str) -> list[dict]:
    rows_html = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    if not rows_html:
        return []

    all_rows = []
    for row_html in rows_html:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL | re.IGNORECASE)
        cells = [re.sub(r"<[^>]+>", " ", c).strip() for c in cells]
        if cells:
            all_rows.append(cells)

    if not all_rows:
        return []

    headers = all_rows[0]
    data_rows = all_rows[1:]

    records = []
    for row in data_rows:
        while len(row) < len(headers):
            row.append("")
        rec = {headers[i]: row[i] for i in range(len(headers))}
        records.append(rec)

    return records


FIXED_HEADERS = [
    "No", "OA", "Account No.", "Customer Name",
    "วันที่ต้องยื่นขอเฉลี่ยทรัพย์", "Billcode Update",
    "จำนวนวันถึงปัจจุบัน", "TAT to day",
    "ผลการตั้งเรื่องขอเฉลี่ยทรัพย์ที่ศาล",
    "วันที่ศาลอนุญาติเฉลี่ยทรัพย์",
    "ติดตามผลกรมได้รับคำสั่งอนุญาติ",
    "ผลการตรวจสอบเงินที่กรม", "หมายเหตุ",
]


def flat_list_to_rows(flat: list, n_cols: int = 8) -> list[dict]:
    headers = FIXED_HEADERS[:n_cols]
    rows = []
    for i in range(0, len(flat), n_cols):
        chunk = [str(v) for v in flat[i:i + n_cols]]
        if len(chunk) < 2:
            break
        row = {headers[j]: chunk[j] if j < len(chunk) else "" for j in range(len(headers))}
        rows.append(row)
    return rows


def table_list_to_rows(table: list, n_cols: int = 8) -> list[dict]:
    headers = FIXED_HEADERS[:n_cols]
    rows = []
    for values in table:
        if not isinstance(values, list):
            continue
        if not values:
            continue
        row = {headers[i]: str(values[i]) if i < len(values) else "" for i in range(len(headers))}
        rows.append(row)
    return rows


def parse_text_rows(text: str) -> list[dict]:
    headers = FIXED_HEADERS[:8]
    rows = []
    text = text.replace("\\n", "\n")
    row_pattern = re.compile(
        r"(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s+"
        r"(\d{2}-[A-Za-z]{3}-\d{2})\s+(\d+)\s+(\d+)\s+([0-9.>\-]+)"
    )
    row_without_no_pattern = re.compile(
        r"(\d+)\s+(\d+)\s+(.+?)\s+"
        r"(\d{2}-[A-Za-z]{3}-\d{2})\s+(\d+)\s+(\d+)\s+([0-9.>\-]+)"
    )

    for line in text.splitlines():
        match = row_pattern.search(line)
        if match:
            values = [value.strip() for value in match.groups()]
        else:
            match = row_without_no_pattern.search(line)
            if not match:
                continue
            values = [str(len(rows) + 1), *(value.strip() for value in match.groups())]
        rows.append({headers[i]: values[i] for i in range(len(headers))})

    return rows


def parse_csv_tokens(text: str) -> list[dict]:
    marker = "},"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = text.replace("\\n", "\n").strip()
    if not text:
        return []

    try:
        tokens = next(csv.reader([text], skipinitialspace=True))
    except csv.Error:
        return []

    headers = FIXED_HEADERS[:8]
    rows = []
    index = 0
    while index + 7 < len(tokens):
        chunk = [token.strip() for token in tokens[index:index + 8]]
        if not chunk[0].isdigit() or not chunk[1].isdigit():
            index += 1
            continue
        if not re.fullmatch(r"\d{2}-[A-Za-z]{3}-\d{2}", chunk[4]):
            index += 1
            continue
        rows.append({headers[i]: chunk[i] for i in range(len(headers))})
        index += 8
    return rows


def parse_rows_array_from_text(content: str) -> list[dict]:
    rows_key = re.search(r'"rows"\s*:', content)
    if not rows_key:
        return []

    array_start = content.find("[", rows_key.end())
    if array_start < 0:
        return []

    try:
        data, _ = json.JSONDecoder().raw_decode(content[array_start:])
    except json.JSONDecodeError:
        return []

    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], list):
        return table_list_to_rows(data)
    return []


def parse_dict_row_object(data: dict) -> list[dict]:
    headers = FIXED_HEADERS[:8]
    lower_map = {str(key).strip().lower(): value for key, value in data.items()}
    no_value = data.get("No") or data.get("no")
    if not no_value and str(data.get("header") or "").strip().isdigit():
        no_value = str(data.get("header")).strip()

    values = {
        "No": no_value,
        "OA": data.get("OA") or data.get("oa"),
        "Account No.": data.get("Account No.") or data.get("account_no") or data.get("account no."),
        "Customer Name": data.get("Customer Name") or data.get("customer_name") or data.get("customer name"),
        "วันที่ต้องยื่นขอเฉลี่ยทรัพย์": data.get("วันที่ต้องยื่นขอเฉลี่ยทรัพย์"),
        "Billcode Update": data.get("Billcode Update") or data.get("billcode_update"),
        "จำนวนวันถึงปัจจุบัน": data.get("จำนวนวันถึงปัจจุบัน"),
        "TAT to day": data.get("TAT to day") or data.get("tat_to_day"),
    }

    for header in headers:
        if values.get(header):
            continue
        normalized = header.strip().lower()
        if normalized in lower_map:
            values[header] = lower_map[normalized]

    if values.get("No") and values.get("OA") and values.get("Account No."):
        return [{header: str(values.get(header) or "") for header in headers}]
    return []


def parse_malformed_single_row(content: str) -> list[dict]:
    headers = FIXED_HEADERS[:8]

    header_array_match = re.search(
        r'"header"\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"([^"]+)"\s*,\s*'
        r'"(\d{2}-[A-Za-z]{3}-\d{2})"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]*)',
        content,
    )
    if header_array_match:
        values = [value.strip() for value in header_array_match.groups()]
        return [{headers[i]: values[i] for i in range(len(headers))}]

    tokens = re.findall(r'"([^"]*)"', content)
    if not tokens:
        return []

    for index, token in enumerate(tokens):
        if token != "No" or index + 15 >= len(tokens) or not tokens[index + 1].isdigit():
            continue
        candidate: dict[str, str] = {}
        cursor = index
        while cursor + 1 < len(tokens):
            key = tokens[cursor]
            value = tokens[cursor + 1]
            if key in headers:
                candidate[key] = value
                cursor += 2
                if key == "TAT to day":
                    break
                continue
            cursor += 1
        if candidate.get("No") and candidate.get("OA") and candidate.get("Account No."):
            return [{header: candidate.get(header, "") for header in headers}]

    if tokens[0].lower() == "header" and len(tokens) >= 9 and tokens[1].isdigit():
        no_value = tokens[1]
        value_start = None
        if "TAT to day" in tokens:
            value_start = tokens.index("TAT to day") + 1
        elif len(tokens) >= 10 and tokens[2] == "OA":
            value_start = 3
        elif len(tokens) >= 9 and tokens[2].isdigit():
            value_start = 2

        if value_start is None:
            return []

        raw_values = tokens[value_start:]
        if len(raw_values) < 7:
            return []

        values = [no_value, *raw_values[:7]]
        if len(raw_values) > 7:
            values[-1] = "-".join(raw_values[6:])

        if values[1].isdigit() and values[2].isdigit():
            return [{headers[i]: values[i] for i in range(len(headers))}]

    return []


def parse_response(content: str, image_path: str) -> list[dict]:
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            rows = parse_dict_row_object(data)
            if rows:
                print(f"  Parsed single JSON object row: {len(rows)} rows")
                return rows
        if isinstance(data, list) and data:
            if isinstance(data[0], dict):
                print(f"  Parsed as JSON array of objects: {len(data)} rows")
                return data
            rows = flat_list_to_rows(data)
            if rows:
                print(f"  Parsed flat JSON array: {len(rows)} rows")
                return rows
    except (json.JSONDecodeError, ValueError):
        pass

    rows = parse_malformed_single_row(content)
    if rows:
        print(f"  Parsed malformed single row: {len(rows)} rows")
        return rows

    rows = parse_rows_array_from_text(content)
    if rows:
        print(f"  Parsed rows array from text: {len(rows)} rows")
        return rows

    match = re.search(r"\[[\s\S]*?\]", content)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list) and data:
                if isinstance(data[0], dict):
                    print(f"  Parsed JSON array from text: {len(data)} rows")
                    return data
                if not all(str(value).strip().lower() in {"no", "oa", "account no.", "customer name"} for value in data[:4]):
                    rows = flat_list_to_rows(data)
                    if rows:
                        print(f"  Parsed flat array from text: {len(rows)} rows")
                        return rows
        except (json.JSONDecodeError, ValueError):
            pass

    rows = parse_csv_tokens(content)
    if rows:
        print(f"  Parsed CSV tokens from text: {len(rows)} rows")
        return rows

    match = re.search(r"\[[\s\S]*?\]", content)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list) and data:
                rows = flat_list_to_rows(data)
                if rows and rows[0].get("No", "").strip().lower() != "no":
                    print(f"  Parsed flat array from text: {len(rows)} rows")
                    return rows
        except (json.JSONDecodeError, ValueError):
            pass

    html_to_parse = content
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            for key, v in obj.items():
                normalized_key = str(key).strip().lower()
                if isinstance(v, str):
                    rows = parse_text_rows(v)
                    if rows:
                        print(f"  Parsed text rows from JSON object: {len(rows)} rows")
                        return rows
                    if "<tr" in v.lower():
                        html_to_parse = v
                        break
                    if normalized_key in {"header", "headers", "column", "columns"}:
                        continue
                if isinstance(v, list):
                    if normalized_key in {"header", "headers", "column", "columns"}:
                        continue
                    if v and isinstance(v[0], dict):
                        print(f"  Parsed list of objects from JSON object: {len(v)} rows")
                        return v
                    if v and isinstance(v[0], list):
                        rows = table_list_to_rows(v)
                    else:
                        rows = flat_list_to_rows(v)
                    if rows:
                        print(f"  Parsed flat list from JSON object: {len(rows)} rows")
                        return rows
        elif isinstance(obj, list) and obj and isinstance(obj[0], str):
            joined = " ".join(obj)
            if "<tr" in joined.lower():
                html_to_parse = joined
    except (json.JSONDecodeError, ValueError):
        pass

    if "<tr" in html_to_parse.lower():
        html_clean = html_to_parse.replace('\\"', '"').replace("colspan='\"", "colspan='").replace("\"'", "'")
        rows = parse_html_table(html_clean)
        if rows:
            print(f"  Parsed HTML table: {len(rows)} rows")
            return rows

    rows = parse_text_rows(content)
    if rows:
        print(f"  Parsed text rows from raw response: {len(rows)} rows")
        return rows

    print(f"  WARN: Could not parse response for {image_path}")
    return []


def ocr_image(image_path: Path, api_key: str | None, api_url: str, model: str) -> list[dict]:
    print(f"\nProcessing {image_path} ...")
    b64 = image_to_base64(str(image_path))
    content = call_api(b64, api_key, api_url, model)
    # Write debug file
    Path(f"{image_path}_debug.txt").write_text(content, encoding="utf-8", errors="replace")
    return parse_response(content, str(image_path))


def detect_horizontal_table_lines(image_path: Path) -> list[int]:
    with Image.open(image_path) as img:
        gray = img.convert("L")
        width, height = gray.size
        runs: list[list[int]] = []
        for y in range(height):
            dark_pixels = sum(1 for x in range(width) if gray.getpixel((x, y)) < 130)
            if dark_pixels / max(width, 1) <= 0.20:
                continue
            if not runs or y > runs[-1][-1] + 1:
                runs.append([])
            runs[-1].append(y)

    centers = [round(sum(run) / len(run)) for run in runs if run]
    return [center for center in centers if 0 <= center <= height]


def renumber_rows(rows: list[dict], start_no: int = 1) -> None:
    for offset, row in enumerate(rows):
        row["No"] = str(start_no + offset)


def ocr_image_in_chunks(
    image_path: Path,
    api_key: str | None,
    api_url: str,
    model: str,
    chunk_rows: int,
) -> list[dict]:
    if chunk_rows <= 0:
        return ocr_image(image_path, api_key, api_url, model)

    lines = detect_horizontal_table_lines(image_path)
    row_count = max(len(lines) - 1, 0)
    if row_count < 2:
        return ocr_image(image_path, api_key, api_url, model)

    print(f"\nProcessing {image_path} in chunks ...")
    print(f"  Detected table rows: {row_count}")

    all_rows: list[dict] = []
    debug_parts: list[str] = []
    with Image.open(image_path) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size
        for chunk_index, row_start in enumerate(range(0, row_count, chunk_rows), start=1):
            row_end = min(row_start + chunk_rows, row_count)
            y0 = max(lines[row_start] - 2, 0)
            y1 = min(lines[row_end] + 2, height)
            crop = rgb.crop((0, y0, width, y1))
            b64 = pil_image_to_base64(crop)
            content = call_api(b64, api_key, api_url, model)
            debug_parts.append(f"===== chunk {chunk_index}: rows {row_start + 1}-{row_end} =====\n{content}")

            chunk_rows_data = parse_response(
                content,
                f"{image_path} chunk {chunk_index} rows {row_start + 1}-{row_end}",
            )
            renumber_rows(chunk_rows_data, row_start + 1)
            print(f"  Chunk {chunk_index} rows {row_start + 1}-{row_end}: {len(chunk_rows_data)} parsed rows")
            all_rows.extend(chunk_rows_data)

    Path(f"{image_path}_debug.txt").write_text("\n\n".join(debug_parts), encoding="utf-8", errors="replace")
    return all_rows


def ocr_image_grid_rows(
    image_path: Path,
    api_key: str | None,
    api_url: str,
    model: str,
    scale: int,
) -> list[dict]:
    lines = detect_horizontal_table_lines(image_path)
    row_count = max(len(lines) - 1, 0)
    if row_count < 1:
        return ocr_image(image_path, api_key, api_url, model)

    print(f"\nProcessing {image_path} with grid row OCR ...")
    print(f"  Detected table rows: {row_count}")

    all_rows: list[dict] = []
    debug_parts: list[str] = []
    with Image.open(image_path) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size
        for row_index in range(row_count):
            y0 = max(lines[row_index] - 2, 0)
            y1 = min(lines[row_index + 1] + 2, height)
            crop = rgb.crop((0, y0, width, y1))
            if scale > 1:
                crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)

            content = call_api(pil_image_to_base64(crop), api_key, api_url, model)
            debug_parts.append(f"===== row {row_index + 1} =====\n{content}")
            rows = parse_response(content, f"{image_path} row {row_index + 1}")

            if rows:
                all_rows.append(rows[0])
                print(f"  Row {row_index + 1}: parsed")
            else:
                print(f"  Row {row_index + 1}: no parse")

    Path(f"{image_path}_debug.txt").write_text("\n\n".join(debug_parts), encoding="utf-8", errors="replace")
    return all_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR table images and export to Excel.")
    parser.add_argument(
        "images",
        nargs="*",
        help="Image file(s) to OCR. Example: python pdf_to_excel.py 23.png",
    )
    parser.add_argument("-o", "--output", default=OUTPUT, help="Excel output path.")
    parser.add_argument("--api-url", default=API_URL, help="OpenAI-compatible chat completions URL.")
    parser.add_argument("--model", default=MODEL, help="OCR model name.")
    parser.add_argument(
        "--chunk-rows",
        type=int,
        default=CHUNK_ROWS,
        help="Split table images into chunks of N detected rows before OCR. Use 0 to disable.",
    )
    parser.add_argument(
        "--grid",
        action="store_true",
        help="OCR each detected table row separately and merge the rows into one Excel file.",
    )
    parser.add_argument(
        "--grid-scale",
        type=int,
        default=int(os.getenv("PDF_TO_EXCEL_GRID_SCALE", "2")),
        help="Upscale each detected row crop before OCR.",
    )
    return parser.parse_args()


def resolve_image_path(image_file: str) -> Path:
    path = Path(image_file)
    if path.exists():
        return path

    script_relative_path = BASE_DIR / image_file
    if script_relative_path.exists():
        return script_relative_path

    return path


def main():
    args = parse_args()
    images = args.images or IMAGES

    if not API_KEY and not is_ollama_generate_url(args.api_url):
        print(
            "Missing API key for chat completions. Set PDF_TO_EXCEL_API_KEY, "
            "or set PDF_TO_EXCEL_API_URL/OCR_BASE_URL to an /api/generate endpoint."
        )
        sys.exit(2)

    all_rows: list[dict] = []
    ref_headers: list[str] = []

    for img_file in images:
        image_path = resolve_image_path(img_file)
        if not image_path.exists():
            print(f"SKIP: {img_file} not found.")
            continue

        if args.grid:
            rows = ocr_image_grid_rows(
                image_path,
                API_KEY,
                args.api_url,
                args.model,
                args.grid_scale,
            )
        else:
            rows = ocr_image_in_chunks(image_path, API_KEY, args.api_url, args.model, args.chunk_rows)

        if not rows:
            print(f"  No data from {img_file}")
            continue

        filtered = []
        for row in rows:
            vals = list(row.values())
            first = str(vals[0]).strip().lower() if vals else ""
            if first in ("no", "no.", "#", "ลำดับ", "num"):
                continue
            filtered.append(row)

        print(f"  >> {len(filtered)} data rows")

        if not ref_headers and filtered:
            ref_headers = list(filtered[0].keys())

        all_rows.extend(filtered)

    if not all_rows:
        print("\nNo data extracted from any image. Exiting.")
        sys.exit(1)

    df = pd.DataFrame(all_rows)

    if ref_headers:
        existing = [c for c in ref_headers if c in df.columns]
        extra = [c for c in df.columns if c not in ref_headers]
        df = df[existing + extra]

    df.to_excel(args.output, index=False)
    print(f"\nDone! Saved {len(df)} rows -> {args.output}")


if __name__ == "__main__":
    main()
