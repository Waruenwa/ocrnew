from __future__ import annotations

import base64
import json
import re
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
from itertools import permutations
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
from openai import OpenAI
from PIL import Image, ImageFilter
from pypdf import PdfReader
from typhoon_ocr import ocr_document
from typhoon_ocr.ocr_utils import prepare_ocr_messages, render_pdf_to_base64png

from app.core.config import Settings


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


def _is_ollama_generate_url(raw_url: str) -> bool:
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return False
    return parsed.path.rstrip("/") == "/api/generate"


def _resolve_ocr_task_type(settings: Settings) -> str:
    normalized_model = settings.ocr_model.strip().lower()
    if "typhoon-ocr1.5" in normalized_model:
        return "v1.5"

    legacy_model_markers = (
        "typhoon-ocr-preview",
        "typhoon-ocr-7b",
        "typhoon-ocr-3b",
    )
    if any(marker in normalized_model for marker in legacy_model_markers):
        # Legacy Typhoon OCR models expect the older prompt family with anchor text.
        return "structure"
    return "v1.5"


def _resolve_ocr_repeat_penalty(task_type: str) -> float:
    return 1.1 if task_type == "v1.5" else 1.2


def _resolve_ocr_temperature(task_type: str) -> float:
    return 0.0 if task_type == "v1.5" else 0.1


def _resolve_ocr_top_p(task_type: str) -> float:
    return 0.4 if task_type == "v1.5" else 0.6


def validate_extension(file_path: Path) -> None:
    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type: {suffix}. Supported types: {supported}")


def count_pages(file_path: Path) -> int:
    validate_extension(file_path)
    if file_path.suffix.lower() != ".pdf":
        return 1

    reader = PdfReader(str(file_path))
    total_pages = len(reader.pages)
    if total_pages < 1:
        raise ValueError("The uploaded PDF has no readable pages.")
    return total_pages


def _normalize_ocr_response(raw_output: str) -> str:
    content = raw_output.strip()
    if not content:
        return raw_output

    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        if len(lines) >= 2:
            content = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        for key in ("natural_text", "markdown", "text", "content", "result"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return _repair_thai_mojibake_if_needed(_strip_ocr_non_text_blocks(value))

    for key in ("natural_text", "markdown", "text", "content", "result"):
        marker = f'"{key}"'
        marker_index = content.find(marker)
        if marker_index < 0:
            continue

        colon_index = content.find(":", marker_index + len(marker))
        if colon_index < 0:
            continue

        value = content[colon_index + 1 :].strip()
        if value.endswith("}"):
            value = value[:-1].rstrip()
        if value.startswith('"'):
            value = value[1:]
        if value.endswith('"'):
            value = value[:-1]

        value = (
            value.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
            .strip()
        )
        if value:
            return _repair_thai_mojibake_if_needed(_strip_ocr_non_text_blocks(value))

    return _repair_thai_mojibake_if_needed(_strip_ocr_non_text_blocks(raw_output))


def _strip_ocr_non_text_blocks(text: str) -> str:
    return re.sub(r"\n?\s*<figure>.*?</figure>\s*\n?", "\n", text, flags=re.IGNORECASE | re.DOTALL).strip()


def _repair_thai_mojibake_if_needed(text: str) -> str:
    if not _looks_like_thai_mojibake(text):
        return text

    repaired = _decode_cp874_mojibake(text)
    if not repaired or _looks_like_thai_mojibake(repaired):
        return text
    return repaired.strip()


def _looks_like_thai_mojibake(text: str) -> bool:
    if not text:
        return False

    compact = "".join(character for character in text if not character.isspace())
    if not compact:
        return False

    thai_count = sum(1 for character in compact if "\u0e00" <= character <= "\u0e7f")
    marker_count = (
        compact.count("\u0e40\u0e18")
        + compact.count("\u0e40\u0e19")
        + compact.count("\u0e42")
        + compact.count("\u20ac")
    )
    control_count = sum(1 for character in compact if 0x80 <= ord(character) <= 0x9F)
    surrogate_count = sum(1 for character in compact if 0xDC80 <= ord(character) <= 0xDCFF)
    marker_ratio = (marker_count + control_count + surrogate_count) / max(len(compact), 1)
    return thai_count >= 4 and marker_count >= 2 and marker_ratio >= 0.08


def _decode_cp874_mojibake(text: str) -> str | None:
    reconstructed = bytearray()
    try:
        for character in text:
            codepoint = ord(character)
            if 0xDC80 <= codepoint <= 0xDCFF:
                reconstructed.append(codepoint - 0xDC00)
                continue
            if codepoint <= 0xFF:
                reconstructed.append(codepoint)
                continue
            reconstructed.extend(character.encode("cp874", errors="surrogateescape"))
        return bytes(reconstructed).decode("utf-8")
    except UnicodeError:
        return None


def _adapt_ocr_prompt_for_model(prompt_text: str, task_type: str) -> str:
    if task_type != "v1.5":
        return prompt_text

    return (
        prompt_text.rstrip()
        + "\n\n"
        + "Additional OCR rules for Thai government forms and legal documents:\n"
        + "- Transcribe only visible printed or handwritten text from the document.\n"
        + "- Keep the original reading order and put each visually separate line on its own line.\n"
        + "- Do not describe seals, logos, Garuda emblems, signatures, stamps, borders, watermark artwork, or page decorations.\n"
        + "- Do not output <figure> blocks, visual summaries, explanations, tables, JSON, or markdown formatting.\n"
        + "- Preserve Thai digits, Arabic digits, ID numbers, house codes, dates, names, addresses, money amounts, and punctuation exactly as visible.\n"
        + "- Do not omit standalone numeric lines, Thai citizen IDs, house codes, short parent-name rows, nationality rows, or footer dates just because they look faint or isolated.\n"
        + "- For Thai personal names, do not autocorrect to a common spelling and do not infer from context. "
        + "Read the glyphs exactly, especially sara am (ำ), upper/lower vowels, tone marks, final consonants, and visually similar letters.\n"
        + "- If a character is unclear, keep the closest visible reading rather than inventing neighboring-field text."
    )


def _extract_json_object(raw_output: str) -> dict[str, object]:
    content = raw_output.strip()
    if not content:
        raise ValueError("Vision model returned an empty response.")

    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        if len(lines) >= 2:
            content = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        return parsed

    start_index = content.find("{")
    end_index = content.rfind("}")
    if start_index >= 0 and end_index > start_index:
        candidate = content[start_index : end_index + 1]
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Vision model did not return a valid JSON object.")


def _extract_prompt_and_image_from_messages(messages: list[dict[str, object]]) -> tuple[str, str]:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue

        prompt_text = ""
        image_base64 = ""
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                prompt_text = str(item.get("text") or "")
                continue
            if item.get("type") == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    raw_url = str(image_url.get("url") or "")
                    image_base64 = raw_url.split(",", 1)[1] if "," in raw_url else raw_url

        if prompt_text and image_base64:
            return prompt_text, image_base64

    raise ValueError("Unable to prepare OCR prompt and image for Ollama.")


def _run_ollama_generate_ocr(
    file_path: Path,
    settings: Settings,
    *,
    page_number: int | None,
    target_image_dim: int,
    task_type: str,
) -> str:
    messages = prepare_ocr_messages(
        pdf_or_image_path=str(file_path),
        task_type=task_type,
        target_image_dim=target_image_dim,
        page_num=page_number if page_number is not None and file_path.suffix.lower() == ".pdf" else 1,
        figure_language="Thai",
    )
    prompt_text, image_base64 = _extract_prompt_and_image_from_messages(messages)
    prompt_text = _adapt_ocr_prompt_for_model(prompt_text, task_type)
    payload = {
        "model": settings.ocr_model,
        "prompt": prompt_text,
        "images": [image_base64],
        "stream": False,
        "options": {
            "temperature": _resolve_ocr_temperature(task_type),
            "top_p": _resolve_ocr_top_p(task_type),
            "repeat_penalty": _resolve_ocr_repeat_penalty(task_type),
            "num_predict": settings.ocr_num_predict,
        },
    }
    request = urllib.request.Request(
        settings.ocr_base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.ocr_timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama OCR failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama OCR endpoint is not reachable: {exc}") from exc

    raw_output = str(response_payload.get("response") or "")
    if not raw_output.strip():
        raise RuntimeError(f"Ollama OCR returned an empty response: {response_payload}")
    normalized_output = _normalize_ocr_response(raw_output)
    if _looks_like_thai_mojibake(normalized_output):
        raise RuntimeError("Ollama OCR returned Thai mojibake text; retry with smaller OCR settings or another candidate.")
    return normalized_output


def _run_ocr(
    file_path: Path,
    settings: Settings,
    *,
    page_number: int | None,
    target_image_dim: int,
) -> str:
    task_type = _resolve_ocr_task_type(settings)
    kwargs = {
        "pdf_or_image_path": str(file_path),
        "base_url": settings.ocr_base_url,
        "api_key": settings.ocr_api_key,
        "model": settings.ocr_model,
        "task_type": task_type,
        "target_image_dim": target_image_dim,
    }
    if page_number is not None and file_path.suffix.lower() == ".pdf":
        kwargs["page_num"] = page_number
    if _is_ollama_generate_url(settings.ocr_base_url):
        return _run_ollama_generate_ocr(
            file_path,
            settings,
            page_number=page_number,
            target_image_dim=target_image_dim,
            task_type=task_type,
        )
    normalized_output = _normalize_ocr_response(ocr_document(**kwargs))
    if _looks_like_thai_mojibake(normalized_output):
        raise RuntimeError("OCR returned Thai mojibake text; retry with smaller OCR settings or another candidate.")
    return normalized_output


def _ocr_models_to_try(settings: Settings) -> tuple[str, ...]:
    models: list[str] = []
    for model in (settings.ocr_model, *settings.ocr_compare_models):
        normalized_model = model.strip()
        if normalized_model and normalized_model not in models:
            models.append(normalized_model)
    return tuple(models)


def _settings_for_ocr_model(settings: Settings, model: str) -> Settings:
    if model == settings.ocr_model:
        return settings
    return replace(settings, ocr_model=model)


def _short_model_name(model: str) -> str:
    return model.rsplit("/", 1)[-1].replace(":latest", "")


def _render_pdf_page_image(file_path: Path, page_number: int, target_image_dim: int = 1400) -> Image.Image:
    image_base64 = render_pdf_to_base64png(
        str(file_path),
        page_number,
        target_longest_image_dim=target_image_dim,
    )
    image = Image.open(BytesIO(base64.b64decode(image_base64)))
    image.load()
    return image.convert("RGB")


def render_page_preview(file_path: Path, page_number: int, target_image_dim: int = 1400) -> Image.Image:
    if file_path.suffix.lower() == ".pdf":
        return _render_pdf_page_image(file_path, page_number, target_image_dim=target_image_dim)

    with Image.open(file_path) as image:
        return image.convert("RGB")


def save_page_preview(
    file_path: Path,
    page_number: int,
    output_path: Path,
    target_image_dim: int = 1400,
) -> Path:
    preview_image = render_page_preview(file_path, page_number, target_image_dim=target_image_dim)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview_image.save(output_path, format="PNG")
    return output_path


def _should_use_cleaned_first(image: Image.Image) -> bool:
    grayscale = image.convert("L")
    histogram = grayscale.histogram()
    total_pixels = max(grayscale.width * grayscale.height, 1)
    dark_ratio = sum(histogram[:201]) / total_pixels
    mid_gray_ratio = sum(histogram[201:241]) / total_pixels
    return dark_ratio < 0.02 and mid_gray_ratio > 0.01


def _clean_image_for_ocr(image: Image.Image) -> Image.Image:
    grayscale = image.convert("L")
    # Bright watermark layers tend to sit in the high-gray range, so whitening
    # that band keeps the dark text while reducing OCR noise on stamped pages.
    cleaned = grayscale.point(lambda pixel: 255 if pixel > 215 else pixel)
    cleaned = cleaned.filter(ImageFilter.SHARPEN)
    return cleaned.convert("RGB")


def _extract_candidate_line_groups(markdown: str) -> list[list[str]]:
    groups: list[list[str]] = []
    current_group: list[str] = []
    inside_figure = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("## Page"):
            if current_group:
                groups.append(current_group)
                current_group = []
            continue

        lower_line = line.lower()
        if lower_line.startswith("<figure"):
            inside_figure = True
            continue
        if lower_line.startswith("</figure"):
            inside_figure = False
            continue
        if inside_figure:
            continue
        if line.startswith("<") and line.endswith(">"):
            continue

        current_group.append(line)

    if current_group:
        groups.append(current_group)

    return groups


def _prefers_own_review_block(line: str) -> bool:
    compact_length = _normalized_text_length(line)
    if compact_length <= 10:
        return True
    if line.endswith(":"):
        return True

    compact_line = "".join(character for character in line if not character.isspace())
    return compact_line in {
        "โจทก์",
        "จำเลย",
        "ระหว่าง",
        "รายละเอียด",
        "ความแพ่ง",
        "เรื่อง",
        "คำขอ",
    }


def _split_line_group_into_review_blocks(lines: list[str]) -> list[list[str]]:
    if not lines:
        return []

    return [[line] for line in lines]


def _extract_candidate_blocks(markdown: str) -> list[str]:
    candidate_blocks: list[str] = []
    for line_group in _extract_candidate_line_groups(markdown):
        for block_lines in _split_line_group_into_review_blocks(line_group):
            block_text = " ".join(line.strip() for line in block_lines if line.strip()).strip()
            if block_text:
                candidate_blocks.append(block_text)

    return candidate_blocks


def _extract_candidate_block_lines(markdown: str) -> list[list[str]]:
    candidate_block_lines: list[list[str]] = []
    for line_group in _extract_candidate_line_groups(markdown):
        for block_lines in _split_line_group_into_review_blocks(line_group):
            normalized_lines = [line.strip() for line in block_lines if line.strip()]
            if normalized_lines:
                candidate_block_lines.append(normalized_lines)

    return candidate_block_lines


def _extract_candidate_lines(markdown: str) -> list[str]:
    candidate_lines: list[str] = []
    for line_group in _extract_candidate_line_groups(markdown):
        candidate_lines.extend(line_group)

    return candidate_lines


def _score_ocr_markdown(markdown: str) -> float:
    candidate_lines = _extract_candidate_lines(markdown)
    candidate_blocks = _extract_candidate_blocks(markdown)
    non_space_characters = sum(1 for character in markdown if not character.isspace())
    replacement_characters = markdown.count("\ufffd")
    short_line_count = sum(1 for line in candidate_lines if _normalized_text_length(line) <= 2)
    repeated_watermark_penalty = 2800.0 if _looks_like_repeated_watermark_ocr(markdown) else 0.0
    mojibake_penalty = 3500.0 if _looks_like_thai_mojibake(markdown) else 0.0

    return (
        float(non_space_characters)
        + (len(candidate_lines) * 18.0)
        + (len(candidate_blocks) * 24.0)
        - (short_line_count * 12.0)
        - (replacement_characters * 80.0)
        - repeated_watermark_penalty
        - mojibake_penalty
    )


def _normalize_text_for_similarity(text: str) -> str:
    return "".join(character for character in text if not character.isspace()).lower()


LEGAL_OCR_SIGNALS = (
    "พิพากษา",
    "จำเลย",
    "โจทก์",
    "ให้ชำระ",
    "ดอกเบี้ย",
    "ค่าเสียหาย",
    "ค่าขาดประโยชน์",
    "คดี",
    "ศาล",
    "บาท",
)

GENERIC_IMAGE_DESCRIPTION_SIGNALS = (
    "ภาพนี้",
    "รายละเอียด",
    "ตราสัญลักษณ์",
    "สีดำและสีขาว",
    "องค์ประกอบ",
    "ตรงกลางภาพ",
    "ลักษณะ",
    "ประกอบด้วย",
)


def _count_signal_matches(markdown: str, signals: tuple[str, ...]) -> int:
    normalized_markdown = _normalize_text_for_similarity(markdown)
    return sum(
        1
        for signal in signals
        if _normalize_text_for_similarity(signal) in normalized_markdown
    )


def _looks_like_generic_image_description(markdown: str) -> bool:
    return (
        _count_signal_matches(markdown, GENERIC_IMAGE_DESCRIPTION_SIGNALS) >= 2
        and _count_signal_matches(markdown, LEGAL_OCR_SIGNALS) <= 1
    )


def _looks_like_repeated_watermark_ocr(markdown: str) -> bool:
    candidate_lines = [line.strip() for line in _extract_candidate_lines(markdown) if line.strip()]
    if len(candidate_lines) < 6:
        return False

    normalized_lines = [_normalize_text_for_similarity(line) for line in candidate_lines]
    normalized_lines = [line for line in normalized_lines if len(line) >= 8]
    if len(normalized_lines) < 6:
        return False

    line_counts = Counter(normalized_lines)
    most_common_line, most_common_count = line_counts.most_common(1)[0]
    repeated_ratio = most_common_count / max(len(normalized_lines), 1)
    has_watermark_phrase = "สำหรับเรียก" in most_common_line and "เท่านั้น" in most_common_line
    return (
        most_common_count >= 6
        and (
            repeated_ratio >= 0.35
            or (has_watermark_phrase and most_common_count >= 3)
        )
    )


def _cleaned_ocr_needs_comparison(markdown: str) -> bool:
    normalized_length = _normalized_text_length(markdown)
    candidate_blocks = _extract_candidate_blocks(markdown)
    if not markdown.strip():
        return True
    if _looks_like_generic_image_description(markdown):
        return True
    if _looks_like_thai_mojibake(markdown):
        return True
    if _looks_like_repeated_watermark_ocr(markdown):
        return True
    if normalized_length < 120:
        return True
    if len(candidate_blocks) <= 2 and normalized_length < 350:
        return True
    return False


def _prepare_binary_text_mask(image: Image.Image) -> np.ndarray:
    cleaned = _clean_image_for_ocr(image).convert("L")
    bitmap = np.array(cleaned)
    bitmap[bitmap > 200] = 255
    return (bitmap < 180).astype(np.uint8)


def _detect_text_components(binary_mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    page_height, page_width = binary_mask.shape
    page_area = max(page_height * page_width, 1)
    component_floor = max(12, int(page_area * 0.00001))
    component_ceiling = int(page_area * 0.01)

    component_count, _, stats, _ = cv2.connectedComponentsWithStats(binary_mask, connectivity=8)
    components: list[tuple[int, int, int, int, int]] = []
    for component_index in range(1, component_count):
        x, y, width, height, area = stats[component_index]
        if area < component_floor or height < 5 or width < 2:
            continue
        if area > component_ceiling:
            continue
        # Large emblems or watermarks can dominate the page and pull nearby
        # text into the wrong region, so keep the detector focused on text-ish
        # components.
        if width > 90 and height > 90:
            continue
        components.append((int(x), int(y), int(width), int(height), int(area)))

    components.sort(key=lambda component: (component[1], component[0]))
    return components


def _group_components_into_rows(
    components: list[tuple[int, int, int, int, int]],
) -> list[list[tuple[int, int, int, int, int]]]:
    rows: list[dict[str, object]] = []
    for component in components:
        _, y, _, height, _ = component
        center_y = y + height / 2
        for row in rows:
            tolerance = max(12.0, float(row["avg_height"]) * 0.85)
            if abs(center_y - float(row["center_y"])) > tolerance:
                continue

            row_items = row["items"]
            assert isinstance(row_items, list)
            row_items.append(component)
            top = min(item[1] for item in row_items)
            bottom = max(item[1] + item[3] for item in row_items)
            row["center_y"] = (top + bottom) / 2
            row["avg_height"] = sum(item[3] for item in row_items) / len(row_items)
            break
        else:
            rows.append(
                {
                    "center_y": center_y,
                    "avg_height": float(height),
                    "items": [component],
                }
            )

    grouped_rows = [row["items"] for row in rows]
    for row in grouped_rows:
        row.sort(key=lambda component: component[0])
    return grouped_rows


def _components_to_box(
    components: list[tuple[int, int, int, int, int]],
    page_width: int,
    page_height: int,
) -> tuple[int, int, int, int]:
    x0 = max(min(component[0] for component in components) - 6, 0)
    y0 = max(min(component[1] for component in components) - 4, 0)
    x1 = min(max(component[0] + component[2] for component in components) + 6, page_width - 1)
    y1 = min(max(component[1] + component[3] for component in components) + 4, page_height - 1)
    return (int(x0), int(y0), int(x1), int(y1))


def _cluster_row_components(
    row_components: list[tuple[int, int, int, int, int]],
    page_width: int,
    page_height: int,
) -> list[tuple[int, int, int, int]]:
    if not row_components:
        return []

    clusters: list[list[tuple[int, int, int, int, int]]] = [[row_components[0]]]
    for component in row_components[1:]:
        active_cluster = clusters[-1]
        previous = active_cluster[-1]
        previous_right = previous[0] + previous[2]
        gap = component[0] - previous_right
        average_height = sum(item[3] for item in active_cluster) / len(active_cluster)
        if gap <= max(36.0, average_height * 3.2):
            active_cluster.append(component)
            continue

        clusters.append([component])

    boxes: list[tuple[int, int, int, int]] = []
    for cluster in clusters:
        x0, y0, x1, y1 = _components_to_box(cluster, page_width, page_height)
        width = x1 - x0
        height = y1 - y0
        area = width * height
        center_x = (x0 + x1) / 2
        component_area = sum(item[4] for item in cluster)
        fill_ratio = component_area / max(area, 1)
        if y0 < page_height * 0.9:
            min_width = 28
            min_height = 10
            min_area = 220
        else:
            min_width = 20
            min_height = 16
            min_area = 600

        if width < min_width or height < min_height or area < min_area:
            is_center_short_line = (
                len(cluster) <= 2
                and abs(center_x - (page_width / 2)) <= page_width * 0.08
                and page_height * 0.12 <= y0 <= page_height * 0.45
                and width >= 18
                and height >= 12
            )
            if not is_center_short_line:
                continue

        # Circular court seals and stamps often survive the component filter as
        # sparse mid-page clusters. They create wide, tall boxes that drag
        # text selections away from the actual line content, especially on the
        # final page near signatures.
        is_center_stamp_like = (
            len(cluster) >= 8
            and width >= 160
            and height >= 60
            and fill_ratio < 0.12
            and page_width * 0.24 <= center_x <= page_width * 0.76
            and page_height * 0.18 <= y0 <= page_height * 0.75
        )
        if is_center_stamp_like:
            continue
        boxes.append((x0, y0, x1, y1))
    return boxes


def _reading_order_key(
    box: tuple[int, int, int, int],
    *,
    page_width: int,
    median_height: float,
) -> tuple[float, int, int]:
    x0, y0, _, y1 = box
    height = y1 - y0
    adjusted_y = float(y0)
    # Court documents often include tall left-side braces such as "ระหว่าง"
    # that semantically introduce the next two rows. Pulling those boxes slightly
    # upward keeps the clickable list closer to the OCR reading order.

    return (adjusted_y, x0, y0)


def _group_boxes_into_sections(
    boxes: list[tuple[int, int, int, int]],
) -> list[list[tuple[int, int, int, int]]]:
    if not boxes:
        return []

    median_height = float(np.median([box[3] - box[1] for box in boxes]))
    gap_threshold = max(28.0, median_height * 1.35)
    overlap_tolerance = max(10.0, median_height * 0.4)

    sections: list[list[tuple[int, int, int, int]]] = [[boxes[0]]]
    current_bottom = boxes[0][3]
    for box in boxes[1:]:
        gap = box[1] - current_bottom
        if gap <= gap_threshold or box[1] <= current_bottom + overlap_tolerance:
            sections[-1].append(box)
            current_bottom = max(current_bottom, box[3])
            continue

        sections.append([box])
        current_bottom = box[3]

    return sections


def _normalized_text_length(text: str) -> int:
    compact_text = "".join(character for character in text if not character.isspace())
    return max(len(compact_text), 1)


def _match_section_boxes_to_lines(
    lines: list[str],
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    if len(lines) != len(boxes) or len(lines) <= 1:
        return boxes
    if len(lines) > 7:
        return boxes

    text_lengths = [_normalized_text_length(line) for line in lines]
    box_widths = [box[2] - box[0] for box in boxes]
    max_text_length = max(text_lengths)
    max_box_width = max(box_widths)
    divisor = max(len(lines) - 1, 1)

    best_cost = float("inf")
    best_permutation: tuple[int, ...] | None = None
    for permutation in permutations(range(len(boxes))):
        cost = 0.0
        for line_index, box_index in enumerate(permutation):
            width_cost = abs(
                (text_lengths[line_index] / max_text_length) - (box_widths[box_index] / max_box_width)
            )
            order_cost = abs(line_index - box_index) / divisor
            cost += (width_cost * 1.35) + (order_cost * 0.35)

        if cost < best_cost:
            best_cost = cost
            best_permutation = permutation

    if best_permutation is None:
        return boxes

    return [boxes[box_index] for box_index in best_permutation]


def _detect_line_boxes(image: Image.Image) -> list[tuple[int, int, int, int]]:
    binary_mask = _prepare_binary_text_mask(image)
    page_height, page_width = binary_mask.shape
    components = _detect_text_components(binary_mask)
    if not components:
        return []

    row_boxes_list: list[list[tuple[int, int, int, int]]] = []
    for row in _group_components_into_rows(components):
        row_boxes = _cluster_row_components(row, page_width, page_height)
        if row_boxes:
            row_boxes_list.append(row_boxes)

    if not row_boxes_list:
        return []

    all_boxes = [box for row_boxes in row_boxes_list for box in row_boxes]
    median_height = float(np.median([box[3] - box[1] for box in all_boxes]))
    ordered_rows = sorted(
        row_boxes_list,
        key=lambda row_boxes: min(
            _reading_order_key(
                value,
                page_width=page_width,
                median_height=median_height,
            )
            for value in row_boxes
        ),
    )

    ordered_boxes: list[tuple[int, int, int, int]] = []
    for row_boxes in ordered_rows:
        ordered_boxes.extend(row_boxes)

    return _suppress_contained_line_boxes(ordered_boxes)


def _suppress_contained_line_boxes(
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    filtered_boxes: list[tuple[int, int, int, int]] = []
    for index, box in enumerate(boxes):
        x0, y0, x1, y1 = box
        width = max(x1 - x0, 1)
        height = max(y1 - y0, 1)
        center_x = (x0 + x1) / 2
        is_contained_fragment = False

        for other_index, other_box in enumerate(boxes):
            if index == other_index:
                continue

            other_x0, other_y0, other_x1, other_y1 = other_box
            other_width = max(other_x1 - other_x0, 1)
            if other_width < width * 2.8:
                continue

            vertical_overlap = max(0, min(y1, other_y1) - max(y0, other_y0))
            if vertical_overlap / height < 0.55:
                continue

            if other_x0 - 8 <= center_x <= other_x1 + 8:
                is_contained_fragment = True
                break

        if not is_contained_fragment:
            filtered_boxes.append(box)

    return filtered_boxes


def _group_line_boxes_into_review_blocks(
    line_boxes: list[tuple[int, int, int, int]],
    *,
    page_width: int,
) -> list[tuple[int, int, int, int]]:
    if not line_boxes:
        return []

    median_height = float(np.median([box[3] - box[1] for box in line_boxes]))
    review_blocks: list[list[tuple[int, int, int, int]]] = [[line_boxes[0]]]
    for box in line_boxes[1:]:
        active_block = review_blocks[-1]
        previous_box = active_block[-1]
        gap = box[1] - previous_box[3]
        box_width = box[2] - box[0]
        previous_width = previous_box[2] - previous_box[0]

        should_break = (
            gap > max(26.0, median_height * 1.45)
            or len(active_block) >= 3
            or box_width < page_width * 0.2
            or previous_width < page_width * 0.2
        )
        if should_break:
            review_blocks.append([box])
            continue

        active_block.append(box)

    return [_combine_boxes(block) for block in review_blocks if block]


def _select_box_span(index: int, item_count: int, box_count: int) -> tuple[int, int]:
    start = round(index * box_count / item_count)
    end = round((index + 1) * box_count / item_count)
    if end <= start:
        end = min(start + 1, box_count)
    return start, end


def _combine_boxes(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _saturated_text_length(text: str) -> int:
    return min(_normalized_text_length(text), 42)


def _is_upper_right_header_box(
    box: tuple[int, int, int, int],
    *,
    page_width: int,
    page_height: int,
) -> bool:
    x0, y0, _, _ = box
    return x0 >= page_width * 0.55 and y0 <= page_height * 0.11


def _looks_like_header_number(text: str) -> bool:
    compact = "".join(character for character in text if not character.isspace())
    if not compact:
        return False

    allowed_symbols = {"-", "/", ".", "(", ")", ":", "_"}
    digit_count = sum(1 for character in compact if character.isdigit())
    if digit_count == 0:
        return False

    if all(character.isdigit() or character in allowed_symbols for character in compact):
        if digit_count < 6 and "-" not in compact and "/" not in compact:
            return False
        return True

    return digit_count >= 6 and digit_count / max(len(compact), 1) >= 0.55


def _is_upper_right_header_text(text: str) -> bool:
    compact = "".join(character for character in text.split()).lower()
    if "สำหรับศาลใช้" in compact:
        return True
    return _looks_like_header_number(text)


def _box_assignment_cost(
    line_text: str,
    box: tuple[int, int, int, int],
    *,
    max_text_length: int,
    max_box_width: int,
    median_box_height: float,
    page_width: int,
    page_height: int,
    line_index: int,
    line_count: int,
) -> float:
    x0, y0, x1, y1 = box
    width = max(x1 - x0, 1)
    height = max(y1 - y0, 1)

    normalized_text = (_saturated_text_length(line_text) ** 0.5) / (max_text_length ** 0.5)
    normalized_width = width / max_box_width
    width_cost = abs(normalized_text - normalized_width)

    height_cost = 0.0
    compact_length = _normalized_text_length(line_text)
    if compact_length <= 8 and height > median_box_height * 1.5:
        height_cost += 0.35
    if compact_length >= 40 and width < page_width * 0.45:
        height_cost += 0.55

    footer_noise_cost = 0.0
    if y0 > page_height * 0.9:
        if x0 < page_width * 0.72:
            footer_noise_cost += 0.55
        if width < page_width * 0.14:
            footer_noise_cost += 0.2
    if line_index == line_count - 1 and x0 > page_width * 0.72:
        footer_noise_cost -= 0.3

    header_noise_cost = 0.0
    if _is_upper_right_header_box(box, page_width=page_width, page_height=page_height):
        if _is_upper_right_header_text(line_text):
            header_noise_cost -= 0.25
        else:
            header_noise_cost += 1.2

    return width_cost + height_cost + footer_noise_cost + header_noise_cost


def _box_skip_cost(
    box: tuple[int, int, int, int],
    *,
    page_width: int,
    page_height: int,
) -> float:
    x0, y0, x1, y1 = box
    width = max(x1 - x0, 1)
    height = max(y1 - y0, 1)
    center_x = (x0 + x1) / 2

    cost = 0.92
    if _is_upper_right_header_box(box, page_width=page_width, page_height=page_height):
        cost -= 0.42

    if width < page_width * 0.2:
        cost -= 0.22
    if width < page_width * 0.12:
        cost -= 0.18

    if (
        page_width * 0.35 <= center_x <= page_width * 0.65
        and page_height * 0.12 <= y0 <= page_height * 0.25
        and width < page_width * 0.16
    ):
        cost -= 0.5

    if y0 > page_height * 0.88:
        cost -= 0.56
    if y0 > page_height * 0.93:
        cost -= 0.18
    if y0 > page_height * 0.88 and width < page_width * 0.3:
        cost -= 0.18

    # Mid-page stamp fragments and signature-area specks often look like short
    # text boxes geometrically, but they should be cheap to skip so that review
    # lines stay anchored to the real visible rows.
    if (
        page_width * 0.3 <= center_x <= page_width * 0.72
        and page_height * 0.3 <= y0 <= page_height * 0.86
        and width < page_width * 0.24
    ):
        cost -= 0.34
    if (
        page_width * 0.34 <= center_x <= page_width * 0.7
        and page_height * 0.34 <= y0 <= page_height * 0.72
        and height < page_height * 0.05
        and width < page_width * 0.18
    ):
        cost -= 0.24

    return max(cost, 0.02)


def _group_assignment_cost(
    line_text: str,
    boxes: list[tuple[int, int, int, int]],
    *,
    total_text_length: int,
    total_box_width: int,
    page_width: int,
    page_height: int,
) -> float:
    compact_length = _normalized_text_length(line_text)
    group_width = sum(max(box[2] - box[0], 1) for box in boxes)
    widest_box = max(max(box[2] - box[0], 1) for box in boxes)

    width_cost = abs((compact_length / total_text_length) - (group_width / total_box_width))

    shape_cost = 0.0
    if compact_length <= 24 and widest_box > page_width * 0.42:
        shape_cost += 0.48
    elif compact_length <= 36 and widest_box > page_width * 0.62:
        shape_cost += 0.2
    if compact_length >= 72 and group_width < page_width * 0.58:
        shape_cost += 0.28

    gap_cost = 0.0
    for previous_box, current_box in zip(boxes, boxes[1:]):
        gap = max(current_box[1] - previous_box[3], 0)
        if gap:
            gap_cost += min(gap / max(page_height, 1), 1.0) * 0.32

    multi_box_cost = 0.0
    if len(boxes) > 1:
        multi_box_cost += 0.02 * (len(boxes) - 1)
        if compact_length <= 32:
            multi_box_cost += 0.16 * (len(boxes) - 1)

    return width_cost + shape_cost + gap_cost + multi_box_cost


def _select_best_box_subset(
    candidate_lines: list[str],
    line_boxes: list[tuple[int, int, int, int]],
    *,
    page_width: int,
    page_height: int,
) -> list[list[tuple[int, int, int, int]]]:
    n = len(candidate_lines)
    m = len(line_boxes)
    if n == 0:
        return []

    if n >= m:
        res = []
        for i in range(n):
            if i < m:
                res.append([line_boxes[i]])
            else:
                res.append([])
        return res

    text_lengths = [len(t.replace(" ", "")) for t in candidate_lines]
    box_widths = [max(b[2] - b[0], 1) for b in line_boxes]

    total_text = sum(text_lengths) or 1
    total_width = sum(box_widths) or 1

    dp = [[float("inf")] * (m + 1) for _ in range(n + 1)]
    choice: list[list[tuple[int, int, str] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0

    for i in range(n + 1):
        for j in range(m + 1):
            if dp[i][j] == float("inf"):
                continue

            if j < m and (m - (j + 1)) >= (n - i):
                skip_cost = dp[i][j] + _box_skip_cost(
                    line_boxes[j],
                    page_width=page_width,
                    page_height=page_height,
                )
                if skip_cost < dp[i][j + 1]:
                    dp[i][j + 1] = skip_cost
                    choice[i][j + 1] = (i, j, "skip")

            if i >= n:
                continue

            max_group_size = max(8, min(64, (text_lengths[i] + 23) // 24))
            max_end = min(m, j + max_group_size)
            for end in range(j + 1, max_end + 1):
                if (m - end) < (n - (i + 1)):
                    continue
                group = line_boxes[j:end]
                cost = dp[i][j] + _group_assignment_cost(
                    candidate_lines[i],
                    group,
                    total_text_length=total_text,
                    total_box_width=total_width,
                    page_width=page_width,
                    page_height=page_height,
                )
                if cost < dp[i + 1][end]:
                    dp[i + 1][end] = cost
                    choice[i + 1][end] = (i, j, "assign")

    partitions: list[list[tuple[int, int, int, int]]] = [[] for _ in range(n)]
    curr_i = n
    curr_j = m
    while curr_i > 0 or curr_j > 0:
        previous = choice[curr_i][curr_j]
        if previous is None:
            break

        prev_i, prev_j, action = previous
        if action == "assign" and curr_i > 0:
            partitions[curr_i - 1] = line_boxes[prev_j:curr_j]
        curr_i = prev_i
        curr_j = prev_j

    return partitions


def _realign_boxes_to_candidate_lines(
    candidate_lines: list[str],
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    if len(candidate_lines) != len(boxes):
        return boxes

    aligned_boxes: list[tuple[int, int, int, int]] = []
    line_offset = 0
    for section_boxes in _group_boxes_into_sections(boxes):
        section_lines = candidate_lines[line_offset : line_offset + len(section_boxes)]
        if len(section_lines) != len(section_boxes):
            return boxes
        aligned_boxes.extend(_match_section_boxes_to_lines(section_lines, section_boxes))
        line_offset += len(section_boxes)

    if len(aligned_boxes) != len(candidate_lines):
        return boxes
    return aligned_boxes


def _filter_segment_candidate_line_entries(candidate_lines: list[str]) -> list[tuple[int, str]]:
    filtered_lines: list[tuple[int, str]] = []
    for index, line in enumerate(candidate_lines):
        compact_line = "".join(character for character in line if not character.isspace())
        compact_length = _normalized_text_length(line)
        previous_length = _normalized_text_length(candidate_lines[index - 1]) if index > 0 else 0
        next_length = (
            _normalized_text_length(candidate_lines[index + 1])
            if index < len(candidate_lines) - 1
            else 0
        )
        digit_count = sum(1 for character in line if character.isdigit())
        is_midstream_short_artifact = (
            compact_length <= 12
            and digit_count == 0
            and previous_length >= 24
            and next_length >= 24
            and compact_line != "รายละเอียด"
            and not _prefers_own_review_block(line)
        )
        if compact_line == "รายละเอียด" and previous_length >= 24 and next_length >= 24:
            continue
        if compact_line == "ผู้พิพากษา":
            continue
        if is_midstream_short_artifact:
            continue

        filtered_lines.append((index, line))

    return filtered_lines


def _filter_segment_candidate_lines(candidate_lines: list[str]) -> list[str]:
    return [line for _, line in _filter_segment_candidate_line_entries(candidate_lines)]


def _group_boxes_by_visual_row(
    boxes: list[tuple[int, int, int, int]],
) -> list[list[tuple[int, int, int, int]]]:
    if not boxes:
        return []

    median_height = float(np.median([max(box[3] - box[1], 1) for box in boxes]))
    row_groups: list[list[tuple[int, int, int, int]]] = [[boxes[0]]]
    current_top = boxes[0][1]
    current_bottom = boxes[0][3]

    for box in boxes[1:]:
        box_top = box[1]
        box_bottom = box[3]
        overlap = min(current_bottom, box_bottom) - max(current_top, box_top)
        same_row_overlap = overlap >= max(4.0, median_height * 0.35)
        nearly_same_top = abs(box_top - current_top) <= max(5.0, median_height * 0.22)
        if same_row_overlap or nearly_same_top:
            row_groups[-1].append(box)
            current_top = min(current_top, box_top)
            current_bottom = max(current_bottom, box_bottom)
            continue

        row_groups.append([box])
        current_top = box_top
        current_bottom = box_bottom

    return row_groups


def _is_preferred_split_boundary(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return False

    previous = text[index - 1]
    current = text[index]
    boundary_characters = {" ", "/", "-", ",", ".", ")", "("}
    return (
        previous.isspace()
        or current.isspace()
        or previous in boundary_characters
        or current in boundary_characters
    )


def _find_text_split_index(
    text: str,
    target_index: int,
    *,
    remaining_groups: int,
) -> int:
    if not text:
        return 0

    lower_bound = 1
    upper_bound = max(lower_bound, len(text) - remaining_groups)
    target = max(lower_bound, min(target_index, upper_bound))

    for offset in range(0, 20):
        forward = target + offset
        if forward <= upper_bound and _is_preferred_split_boundary(text, forward):
            return forward

        backward = target - offset
        if backward >= lower_bound and _is_preferred_split_boundary(text, backward):
            return backward

    return target


def _split_text_to_visual_rows(
    text: str,
    row_groups: list[list[tuple[int, int, int, int]]],
) -> list[str]:
    normalized_text = " ".join(text.split()).strip()
    if len(row_groups) <= 1 or not normalized_text:
        return [normalized_text]

    widths = [
        sum(max(box[2] - box[0], 1) for box in row_group)
        for row_group in row_groups
    ]
    remaining_text = normalized_text
    remaining_width = max(sum(widths), 1)
    row_texts: list[str] = []

    for row_index, width in enumerate(widths[:-1]):
        groups_left = len(widths) - row_index - 1
        if not remaining_text:
            row_texts.append("")
            remaining_width -= width
            continue

        proportional_length = round((len(remaining_text) * width) / max(remaining_width, 1))
        split_index = _find_text_split_index(
            remaining_text,
            proportional_length,
            remaining_groups=groups_left,
        )
        row_text = remaining_text[:split_index].strip()
        if not row_text:
            row_text = remaining_text[: max(1, split_index)].strip() or remaining_text[:1]
            split_index = max(split_index, 1)
        row_texts.append(row_text)
        remaining_text = remaining_text[split_index:].strip()
        remaining_width -= width

    row_texts.append(remaining_text.strip())
    return [row_text or normalized_text for row_text in row_texts]


def _is_punctuation_only_text(text: str) -> bool:
    compact = "".join(character for character in text if not character.isspace())
    if not compact:
        return True
    return all(not character.isalnum() for character in compact)


def _get_segment_raw_text(segment: dict[str, object]) -> str:
    raw_text = segment.get("raw_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()

    text = segment.get("text")
    if isinstance(text, str):
        return text.strip()

    return ""


def _normalize_single_line_text(text: str) -> str:
    normalized = _strip_wrapped_code_fence(text)
    normalized = " ".join(normalized.replace("\r", "\n").splitlines()).strip()
    for prefix in ("Corrected line:", "Line:", "Text:", "OCR hint:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        normalized = normalized[1:-1].strip()
    return " ".join(normalized.split()).strip()


def _segment_bbox_to_pixels(
    bbox: tuple[float, float, float, float],
    *,
    page_width: int,
    page_height: int,
) -> tuple[int, int, int, int]:
    x0 = max(min(int(round(bbox[0] * page_width)), page_width), 0)
    y0 = max(min(int(round(bbox[1] * page_height)), page_height), 0)
    x1 = max(min(int(round(bbox[2] * page_width)), page_width), x0 + 1)
    y1 = max(min(int(round(bbox[3] * page_height)), page_height), y0 + 1)
    return x0, y0, x1, y1


def _expand_pixel_box(
    box: tuple[int, int, int, int],
    *,
    page_width: int,
    page_height: int,
    padding_x: int,
    padding_y: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return (
        max(x0 - padding_x, 0),
        max(y0 - padding_y, 0),
        min(x1 + padding_x, page_width),
        min(y1 + padding_y, page_height),
    )


def _crop_segment_image(page_image: Image.Image, segment: dict[str, object]) -> Image.Image | None:
    bbox = segment.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None

    try:
        normalized_bbox = tuple(float(value) for value in bbox)
    except (TypeError, ValueError):
        return None

    page_width = max(page_image.width, 1)
    page_height = max(page_image.height, 1)
    pixel_box = _segment_bbox_to_pixels(
        normalized_bbox,
        page_width=page_width,
        page_height=page_height,
    )
    expanded_box = _expand_pixel_box(
        pixel_box,
        page_width=page_width,
        page_height=page_height,
        padding_x=max(int(page_width * 0.02), 18),
        padding_y=max(int(page_height * 0.014), 12),
    )
    crop = page_image.crop(expanded_box)
    if crop.width < 8 or crop.height < 8:
        return None
    return crop


def _image_to_data_url(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _image_to_base64_png(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def run_vision_json(
    *,
    image: Image.Image,
    prompt: str,
    settings: Settings,
) -> dict[str, object]:
    if not settings.vision_ready:
        raise RuntimeError("Vision model is not configured.")
    uses_generate_endpoint = _is_ollama_generate_url(settings.vision_base_url)
    if not uses_generate_endpoint:
        raise RuntimeError(
            f"Unsupported vision endpoint: {settings.vision_base_url}. "
            "Expected an Ollama /api/generate URL."
        )

    image_base64 = _image_to_base64_png(image.convert("RGB"))
    payload = {
        "model": settings.vision_model,
        "prompt": prompt,
        "images": [image_base64],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "top_p": 0.3,
            "num_predict": settings.vision_num_predict,
        },
    }
    request = urllib.request.Request(
        settings.vision_base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.vision_timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama vision failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama vision endpoint is not reachable: {exc}") from exc

    message = response_payload.get("message")
    if isinstance(message, dict):
        raw_output = str(message.get("content") or "")
    else:
        raw_output = str(response_payload.get("response") or "")
    if not raw_output.strip():
        raise RuntimeError(f"Ollama vision returned an empty response: {response_payload}")
    return _extract_json_object(raw_output)


def _score_segment_for_vision_review(segment: dict[str, object]) -> tuple[float, list[str]]:
    raw_text = _get_segment_raw_text(segment)
    if not raw_text:
        return 0.0, []

    bbox = segment.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return 0.0, []

    try:
        x0, y0, x1, y1 = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return 0.0, []

    width_ratio = max(min(x1, 1.0) - max(x0, 0.0), 0.0)
    height_ratio = max(min(y1, 1.0) - max(y0, 0.0), 0.0)
    compact = "".join(character for character in raw_text if not character.isspace())
    compact_length = max(len(compact), 1)
    digit_count = sum(1 for character in compact if character.isdigit())
    punctuation_count = sum(1 for character in compact if not character.isalnum())
    punctuation_ratio = punctuation_count / compact_length
    replacement_count = compact.count("\ufffd")
    ascii_letter_count = sum(1 for character in compact if character.isascii() and character.isalpha())

    score = 0.0
    reasons: list[str] = []

    if compact_length <= 14:
        score += 0.55
        reasons.append("short_line")
    elif compact_length <= 24 and width_ratio >= 0.35:
        score += 0.3
        reasons.append("short_for_width")

    if width_ratio >= 0.62 and compact_length <= 42:
        score += 0.7
        reasons.append("wide_box_short_text")
    elif width_ratio >= 0.5 and compact_length <= 32:
        score += 0.42
        reasons.append("wide_box_short_text")

    if replacement_count:
        score += 0.8
        reasons.append("replacement_character")

    if raw_text.endswith("..."):
        score += 0.38
        reasons.append("truncated_suffix")

    if ascii_letter_count >= 2:
        score += 0.24
        reasons.append("ascii_letters")

    if punctuation_ratio >= 0.35 and compact_length <= 18:
        score += 0.24
        reasons.append("punctuation_heavy")

    if digit_count >= 4 and compact_length <= 28 and width_ratio >= 0.22:
        score += 0.18
        reasons.append("number_heavy")

    if height_ratio >= 0.035 and compact_length <= 22:
        score += 0.14
        reasons.append("tall_crop")

    return score, reasons


def _select_segments_for_vision_review(
    segments: list[dict[str, object]],
    *,
    max_segments: int = 8,
) -> list[tuple[int, float, list[str]]]:
    scored_segments: list[tuple[int, float, list[str]]] = []
    for index, segment in enumerate(segments):
        score, reasons = _score_segment_for_vision_review(segment)
        raw_text = _get_segment_raw_text(segment)
        compact_raw = "".join(character for character in raw_text if not character.isspace())
        next_text = _get_segment_raw_text(segments[index + 1]) if index + 1 < len(segments) else ""
        compact_next = "".join(character for character in next_text if not character.isspace())
        starts_with_digit = bool(compact_next) and (
            compact_next[0].isdigit() or compact_next[0] in "๐๑๒๓๔๕๖๗๘๙"
        )
        trailing_continuation_tokens = (
            "ดอก",
            "พร้อมดอก",
            "บาทร้อมดอก",
            "บาทพร้อมดอก",
            "ดอกเบี้ย",
            "อัตรา",
            "ร้อยละ",
        )
        if compact_raw.endswith(trailing_continuation_tokens) and starts_with_digit:
            score += 0.24
            reasons.append("continued_interest_clause")
        if score <= 0:
            continue
        scored_segments.append((index, score, reasons))

    if not scored_segments:
        return []

    scored_segments.sort(key=lambda item: (-item[1], item[0]))
    selected = [item for item in scored_segments if item[1] >= 0.78][:max_segments]
    if not selected:
        selected = scored_segments[: min(max_segments, 2)]

    return sorted(selected, key=lambda item: item[0])


def _request_line_correction_from_ocr_model(
    *,
    crop_image: Image.Image,
    settings: Settings,
) -> str:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop_image.save(temp_path, format="PNG")
        content = _run_ocr(
            temp_path,
            settings,
            page_number=None,
            target_image_dim=800,
        )
        return _normalize_single_line_text(content)
    finally:
        temp_path.unlink(missing_ok=True)


def _should_keep_line_correction(raw_text: str, corrected_text: str) -> tuple[bool, float]:
    normalized_raw = _normalize_text_for_similarity(raw_text)
    normalized_corrected = _normalize_text_for_similarity(corrected_text)
    if not normalized_corrected:
        return False, 0.0

    raw_thai_count = sum(1 for character in raw_text if "\u0E00" <= character <= "\u0E7F")
    corrected_thai_count = sum(1 for character in corrected_text if "\u0E00" <= character <= "\u0E7F")
    corrected_cyrillic_count = sum(1 for character in corrected_text if "\u0400" <= character <= "\u04FF")
    corrected_ascii_letter_count = sum(
        1 for character in corrected_text if character.isascii() and character.isalpha()
    )
    if (
        raw_thai_count >= 3
        and corrected_thai_count == 0
        and (corrected_cyrillic_count >= 2 or corrected_ascii_letter_count >= 4)
    ):
        return False, 0.0

    similarity = SequenceMatcher(None, normalized_raw, normalized_corrected).ratio()
    raw_length = max(len(normalized_raw), 1)
    corrected_length = max(len(normalized_corrected), 1)
    length_ratio = corrected_length / raw_length

    if similarity < 0.16:
        return False, similarity
    if length_ratio > 2.8 or length_ratio < 0.45:
        return False, similarity
    if similarity < 0.26 and raw_length >= 18:
        return False, similarity

    return True, similarity


def _compose_corrected_markdown_from_segments(
    raw_markdown: str,
    segments: list[dict[str, object]],
) -> str:
    replacement_by_source_index: dict[int, list[tuple[int, str]]] = {}
    for segment in segments:
        source_line_index = segment.get("source_line_index")
        if not isinstance(source_line_index, int):
            continue

        source_row_index = segment.get("source_row_index")
        row_index = source_row_index if isinstance(source_row_index, int) else 0
        candidate_text = (
            str(segment.get("corrected_text")).strip()
            if isinstance(segment.get("corrected_text"), str) and str(segment.get("corrected_text")).strip()
            else _get_segment_raw_text(segment)
        )
        if not candidate_text:
            continue

        replacement_by_source_index.setdefault(source_line_index, []).append((row_index, candidate_text))

    if not replacement_by_source_index:
        return raw_markdown.strip()

    grouped_lines = _extract_candidate_line_groups(raw_markdown)
    rebuilt_groups: list[str] = []
    candidate_index = 0
    for group in grouped_lines:
        rebuilt_lines: list[str] = []
        for line in group:
            replacements = replacement_by_source_index.get(candidate_index)
            if replacements:
                corrected_line = " ".join(
                    text.strip()
                    for _, text in sorted(replacements, key=lambda item: item[0])
                    if text.strip()
                ).strip()
                rebuilt_lines.append(corrected_line or line.strip())
            else:
                rebuilt_lines.append(line.strip())
            candidate_index += 1

        compact_group = "\n".join(line for line in rebuilt_lines if line)
        if compact_group:
            rebuilt_groups.append(compact_group)

    return "\n\n".join(rebuilt_groups).strip()


def correct_segments_with_line_ocr(
    *,
    page_image: Image.Image,
    raw_markdown: str,
    segments: list[dict[str, object]],
    settings: Settings,
) -> dict[str, object]:
    normalized_raw_markdown = raw_markdown.strip()
    prepared_segments = [dict(segment) for segment in segments]

    if not normalized_raw_markdown or not prepared_segments:
        return {
            "segments": prepared_segments,
            "corrected_markdown": None,
            "correction_model": None,
            "correction_error": None,
            "correction_similarity": None,
            "reviewed_line_count": 0,
            "corrected_line_count": 0,
        }

    if not settings.ocr_ready:
        return {
            "segments": prepared_segments,
            "corrected_markdown": None,
            "correction_model": None,
            "correction_error": None,
            "correction_similarity": None,
            "reviewed_line_count": 0,
            "corrected_line_count": 0,
        }

    candidates = _select_segments_for_vision_review(prepared_segments)
    if not candidates:
        return {
            "segments": prepared_segments,
            "corrected_markdown": None,
            "correction_model": settings.ocr_model,
            "correction_error": None,
            "correction_similarity": None,
            "reviewed_line_count": 0,
            "corrected_line_count": 0,
        }

    reviewed_line_count = 0
    corrected_line_count = 0
    line_errors: list[str] = []
    for index, _, _ in candidates:
        segment = prepared_segments[index]
        raw_text = _get_segment_raw_text(segment)
        if not raw_text:
            continue

        crop_image = _crop_segment_image(page_image, segment)
        if crop_image is None:
            continue

        reviewed_line_count += 1
        try:
            corrected_text = _request_line_correction_from_ocr_model(
                crop_image=crop_image,
                settings=settings,
            )
        except Exception as exc:
            line_errors.append(str(exc))
            continue

        if not corrected_text:
            continue

        keep_correction, _ = _should_keep_line_correction(raw_text, corrected_text)
        if not keep_correction:
            continue

        if _normalize_text_for_similarity(raw_text) == _normalize_text_for_similarity(corrected_text):
            continue

        segment["corrected_text"] = corrected_text
        corrected_line_count += 1

    if corrected_line_count == 0:
        correction_error = None
        if reviewed_line_count > 0 and line_errors and len(line_errors) >= reviewed_line_count:
            correction_error = (
                "Line-crop OCR reread failed for the suspicious lines, so the raw OCR lines were kept."
            )

        return {
            "segments": prepared_segments,
            "corrected_markdown": None,
            "correction_model": settings.ocr_model,
            "correction_error": correction_error,
            "correction_similarity": None,
            "reviewed_line_count": reviewed_line_count,
            "corrected_line_count": corrected_line_count,
        }

    corrected_markdown = _compose_corrected_markdown_from_segments(
        normalized_raw_markdown,
        prepared_segments,
    )
    correction_similarity = SequenceMatcher(
        None,
        _normalize_text_for_similarity(normalized_raw_markdown),
        _normalize_text_for_similarity(corrected_markdown),
    ).ratio()

    return {
        "segments": prepared_segments,
        "corrected_markdown": corrected_markdown,
        "correction_model": settings.ocr_model,
        "correction_error": None,
        "correction_similarity": correction_similarity,
        "reviewed_line_count": reviewed_line_count,
        "corrected_line_count": corrected_line_count,
    }


def correct_segments_with_vision(
    *,
    page_image: Image.Image,
    raw_markdown: str,
    segments: list[dict[str, object]],
    settings: Settings,
) -> dict[str, object]:
    return correct_segments_with_line_ocr(
        page_image=page_image,
        raw_markdown=raw_markdown,
        segments=segments,
        settings=settings,
    )


def build_page_segments(
    page_image: Image.Image,
    markdown: str,
    page_number: int,
) -> list[dict[str, object]]:
    candidate_line_entries = _filter_segment_candidate_line_entries(_extract_candidate_lines(markdown))
    candidate_lines = [line for _, line in candidate_line_entries]
    if not candidate_lines:
        return []

    line_boxes = _detect_line_boxes(page_image)
    if not line_boxes:
        return []

    page_width = max(page_image.width, 1)
    page_height = max(page_image.height, 1)
    
    # We now group the line_boxes into partitions matching the candidate_lines length
    aligned_box_groups = _select_best_box_subset(
        candidate_lines,
        line_boxes,
        page_width=page_width,
        page_height=page_height,
    )

    segments: list[dict[str, object]] = []
    segment_index = 0
    for source_line_entry, assigned_boxes in zip(candidate_line_entries, aligned_box_groups, strict=False):
        source_line_index, line_text = source_line_entry
        if not assigned_boxes:
            continue

        normalized_line_text = line_text.strip()
        if not normalized_line_text:
            continue

        row_groups = _group_boxes_by_visual_row(assigned_boxes)
        row_texts = _split_text_to_visual_rows(normalized_line_text, row_groups)
        for row_index, (row_group, row_text) in enumerate(zip(row_groups, row_texts, strict=False)):
            if not row_group or not row_text:
                continue
            if _is_punctuation_only_text(row_text):
                if segments:
                    segments[-1]["text"] = f"{segments[-1]['text']}{row_text}"
                    previous_raw = _get_segment_raw_text(segments[-1])
                    segments[-1]["raw_text"] = f"{previous_raw}{row_text}"
                continue

            x0, y0, x1, y1 = _combine_boxes(row_group)
            line_heights = [max(box[3] - box[1], 1) for box in row_group]
            median_height = int(np.median(line_heights)) if line_heights else 1
            horizontal_padding = max(int(page_width * 0.01), median_height // 2, 8)
            vertical_padding = max(median_height // 3, 4)
            x0 = max(x0 - horizontal_padding, 0)
            y0 = max(y0 - vertical_padding, 0)
            x1 = min(x1 + horizontal_padding, page_width)
            y1 = min(y1 + vertical_padding, page_height)
            segment_index += 1
            segments.append(
                {
                    "id": f"page-{page_number}-segment-{segment_index}",
                    "text": row_text,
                    "page_number": page_number,
                    "bbox": (
                        round(x0 / page_width, 6),
                        round(y0 / page_height, 6),
                        round(x1 / page_width, 6),
                        round(y1 / page_height, 6),
                    ),
                    "bboxes": [
                        (
                            round(bx0 / page_width, 6),
                            round(by0 / page_height, 6),
                            round(bx1 / page_width, 6),
                            round(by1 / page_height, 6),
                        )
                        for bx0, by0, bx1, by1 in row_group
                    ],
                    "raw_text": row_text,
                    "corrected_text": None,
                    "source_line_index": source_line_index,
                    "source_row_index": row_index,
                }
            )

    return segments


def _build_cleaned_input(
    file_path: Path,
    page_number: int,
    source_image: Image.Image | None = None,
) -> Path:
    if source_image is None:
        if file_path.suffix.lower() == ".pdf":
            source_image = _render_pdf_page_image(file_path, page_number)
        else:
            with Image.open(file_path) as image:
                source_image = image.convert("RGB")

    cleaned_image = _clean_image_for_ocr(source_image)
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    cleaned_image.save(temp_path, format="PNG")
    return temp_path


def _build_aggressive_ocr_input(
    file_path: Path,
    page_number: int,
) -> Path:
    if file_path.suffix.lower() == ".pdf":
        source_image = _render_pdf_page_image(file_path, page_number)
    else:
        with Image.open(file_path) as image:
            source_image = image.convert("RGB")

    grayscale = np.array(source_image.convert("L"))
    blurred = cv2.medianBlur(grayscale, 3)
    binary = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        13,
    )
    aggressive_image = Image.fromarray(binary).filter(ImageFilter.SHARPEN).convert("RGB")
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    aggressive_image.save(temp_path, format="PNG")
    return temp_path


def run_ocr_page(
    file_path: Path,
    page_number: int,
    settings: Settings,
    *,
    source_is_cleaned: bool = False,
) -> str:
    if source_is_cleaned:
        return _run_ocr(
            file_path,
            settings,
            page_number=page_number,
            target_image_dim=settings.ocr_target_image_dim,
        )

    analysis_image: Image.Image | None = None
    if file_path.suffix.lower() == ".pdf":
        analysis_image = _render_pdf_page_image(file_path, page_number)
    elif file_path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        with Image.open(file_path) as image:
            analysis_image = image.convert("RGB")

    if analysis_image is not None and _should_use_cleaned_first(analysis_image):
        cleaned_input_path = _build_cleaned_input(
            file_path,
            page_number,
            source_image=analysis_image,
        )
        try:
            return _run_ocr(
                cleaned_input_path,
                settings,
                page_number=None,
                target_image_dim=settings.ocr_target_image_dim,
            )
        finally:
            cleaned_input_path.unlink(missing_ok=True)

    try:
        return _run_ocr(
            file_path,
            settings,
            page_number=page_number,
            target_image_dim=settings.ocr_target_image_dim,
        )
    except Exception as original_error:
        cleaned_input_path = _build_cleaned_input(
            file_path,
            page_number,
            source_image=analysis_image,
        )
        try:
            return _run_ocr(
                cleaned_input_path,
                settings,
                page_number=None,
                target_image_dim=settings.ocr_target_image_dim,
            )
        except Exception as cleaned_error:
            raise RuntimeError(
                f"Page {page_number} OCR failed. "
                f"Original attempt: {original_error}. "
                f"Cleaned-image fallback: {cleaned_error}"
            ) from cleaned_error
        finally:
            cleaned_input_path.unlink(missing_ok=True)


def _contains_latin_case_series(markdown: str | None) -> bool:
    if not markdown:
        return False

    compact = "".join(str(markdown).split())
    return bool(re.search(r"[A-Za-z][0-9๐-๙]{3,}/[0-9๐-๙]{2,4}", compact))


def compare_ocr_page_sources(
    *,
    original_file_path: Path,
    cleaned_file_path: Path,
    page_number: int,
    settings: Settings,
) -> dict[str, object]:
    candidate_records: list[dict[str, object]] = []
    errors: dict[str, str] = {}
    suspicious_reasons: list[str] = []
    temp_candidate_paths: list[Path] = []
    ocr_models = _ocr_models_to_try(settings)

    def run_candidate(
        source_label: str,
        file_path: Path,
        *,
        source_is_cleaned: bool,
        model: str,
    ) -> None:
        candidate_label = f"{source_label}:{_short_model_name(model)}"
        if not file_path.exists():
            errors[candidate_label] = f"File not found: {file_path}"
            return

        try:
            markdown = run_ocr_page(
                file_path,
                page_number,
                _settings_for_ocr_model(settings, model),
                source_is_cleaned=source_is_cleaned,
            )
        except Exception as exc:
            errors[candidate_label] = str(exc)
            return

        candidate_records.append(
            {
                "source": source_label,
                "normalized_source": "original" if source_label == "original" else "cleaned",
                "model": model,
                "label": candidate_label,
                "markdown": markdown,
                "score": _score_ocr_markdown(markdown),
            }
        )

    def run_source_with_model(
        source_label: str,
        file_path: Path,
        *,
        source_is_cleaned: bool,
        model: str,
    ) -> None:
        run_candidate(
            source_label,
            file_path,
            source_is_cleaned=source_is_cleaned,
            model=model,
        )

    def best_record(*sources: str) -> dict[str, object] | None:
        source_set = set(sources)
        source_records = [
            record
            for record in candidate_records
            if str(record.get("normalized_source") or "") in source_set
            or str(record.get("source") or "") in source_set
        ]
        if not source_records:
            return None
        return max(source_records, key=lambda record: float(record.get("score") or 0.0))

    def source_error(source: str) -> str | None:
        messages = [
            message
            for label, message in errors.items()
            if label.startswith(f"{source}:")
        ]
        return " | ".join(messages) if messages else None

    try:
        primary_model = ocr_models[0] if ocr_models else settings.ocr_model
        compare_models = tuple(model for model in ocr_models if model != primary_model)

        run_source_with_model(
            "original",
            original_file_path,
            source_is_cleaned=True,
            model=primary_model,
        )
        run_source_with_model(
            "cleaned",
            cleaned_file_path,
            source_is_cleaned=True,
            model=primary_model,
        )

        primary_records = [record for record in (best_record("original"), best_record("cleaned")) if record]
        best_primary_record = (
            max(primary_records, key=lambda record: float(record.get("score") or 0.0))
            if primary_records
            else None
        )
        best_primary_markdown = (
            str(best_primary_record.get("markdown") or "")
            if best_primary_record is not None
            else None
        )
        best_primary_score = (
            float(best_primary_record.get("score") or 0.0)
            if best_primary_record is not None
            else 0.0
        )
        best_primary_is_suspicious = (
            best_primary_markdown is None
            or _cleaned_ocr_needs_comparison(best_primary_markdown)
            or best_primary_score < 450
        )

        if best_primary_is_suspicious:
            for model in compare_models:
                run_source_with_model(
                    "original",
                    original_file_path,
                    source_is_cleaned=True,
                    model=model,
                )
                run_source_with_model(
                    "cleaned",
                    cleaned_file_path,
                    source_is_cleaned=True,
                    model=model,
                )

        current_best_record = max(
            candidate_records,
            key=lambda record: float(record.get("score") or 0.0),
        ) if candidate_records else None
        current_best_markdown = (
            str(current_best_record.get("markdown") or "")
            if current_best_record is not None
            else None
        )
        current_best_score = (
            float(current_best_record.get("score") or 0.0)
            if current_best_record is not None
            else 0.0
        )
        should_try_aggressive = current_best_markdown is not None and (
            _cleaned_ocr_needs_comparison(current_best_markdown)
            or current_best_score < 450
        )

        if should_try_aggressive:
            try:
                aggressive_path = _build_aggressive_ocr_input(cleaned_file_path, page_number)
            except Exception as exc:
                errors["aggressive:preprocess"] = str(exc)
            else:
                temp_candidate_paths.append(aggressive_path)
                run_source_with_model(
                    "aggressive",
                    aggressive_path,
                    source_is_cleaned=True,
                    model=primary_model,
                )
                if best_primary_is_suspicious:
                    for model in compare_models:
                        run_source_with_model(
                            "aggressive",
                            aggressive_path,
                            source_is_cleaned=True,
                            model=model,
                        )
                suspicious_reasons.append(
                    "The pipeline tried an aggressive black/white OCR image because watermark cleanup was important for this page."
                )

        if best_primary_record is None:
            suspicious_reasons.append("Original and cleaned OCR both failed before candidate selection.")
        elif should_try_aggressive:
            suspicious_reasons.append(
                "OCR quality looked uncertain, so the pipeline compared original, cleaned, and aggressive candidates."
            )
        elif best_primary_is_suspicious:
            suspicious_reasons.append(
                "OCR quality looked uncertain, so the pipeline compared original and cleaned candidates with extra models."
            )
        else:
            suspicious_reasons.append(
                "OCR quality looked usable after comparing the original and cleaned candidates."
            )
    finally:
        for temp_path in temp_candidate_paths:
            temp_path.unlink(missing_ok=True)

    if not candidate_records:
        raise RuntimeError(
            f"Page {page_number} OCR failed for all OCR candidates. "
            + " | ".join(f"{label}: {message}" for label, message in errors.items())
        )

    cleaned_record = best_record("cleaned")
    original_record = best_record("original")
    original_markdown = (
        str(original_record.get("markdown") or "")
        if original_record is not None
        else None
    )
    cleaned_markdown = (
        str(cleaned_record.get("markdown") or "")
        if cleaned_record is not None
        else None
    )
    original_score = (
        float(original_record.get("score") or 0.0)
        if original_record is not None
        else None
    )
    cleaned_score = (
        float(cleaned_record.get("score") or 0.0)
        if cleaned_record is not None
        else None
    )

    if len(ocr_models) > 1:
        suspicious_reasons.append(
            "The pipeline compared OCR models: "
            + ", ".join(_short_model_name(model) for model in ocr_models)
            + "."
        )

    if cleaned_markdown is None:
        suspicious_reasons.append("No cleaned OCR candidate succeeded, so this page may need a manual check.")

    if original_record is None:
        suspicious_reasons.append("Original OCR did not produce a usable candidate for this page.")
    if cleaned_record is None:
        suspicious_reasons.append("Cleaned OCR did not produce a usable candidate for this page.")

    diff_similarity: float | None = None
    if original_markdown is not None and cleaned_markdown is not None:
        diff_similarity = SequenceMatcher(
            None,
            _normalize_text_for_similarity(original_markdown),
            _normalize_text_for_similarity(cleaned_markdown),
        ).ratio()

    selected_record = max(candidate_records, key=lambda record: float(record.get("score") or 0.0))
    selected_source = str(selected_record.get("normalized_source") or selected_record.get("source") or "cleaned")
    if (
        page_number == 1
        and original_record is not None
        and _contains_latin_case_series(original_markdown)
        and not _contains_latin_case_series(cleaned_markdown)
    ):
        selected_record = original_record
        selected_source = "original"
        suspicious_reasons.append(
            "The original first-page OCR kept a Latin case-number series that cleaned OCR missed."
        )

    selected_markdown = str(selected_record.get("markdown") or "")
    selected_score = float(selected_record.get("score") or 0.0)
    selected_ocr_model = str(selected_record.get("model") or settings.ocr_model)
    selected_candidate_source = str(selected_record.get("source") or selected_source)
    if selected_candidate_source == "aggressive":
        suspicious_reasons.append("The selected cleaned OCR text came from the aggressive black/white image candidate.")

    selected_blocks = _extract_candidate_blocks(selected_markdown)
    if len(selected_blocks) <= 3:
        suspicious_reasons.append("Few OCR blocks were detected on this page.")
    if _normalized_text_length(selected_markdown) < 120:
        suspicious_reasons.append("The selected OCR output is shorter than expected.")

    return {
        "selected_source": selected_source,
        "selected_markdown": selected_markdown,
        "selected_score": selected_score,
        "selected_ocr_model": selected_ocr_model,
        "selected_candidate_source": selected_candidate_source,
        "original_markdown": original_markdown,
        "cleaned_markdown": cleaned_markdown,
        "original_score": original_score,
        "cleaned_score": cleaned_score,
        "original_error": source_error("original"),
        "cleaned_error": source_error("cleaned") or source_error("aggressive"),
        "diff_similarity": diff_similarity,
        "candidate_scores": [
            {
                "label": str(record.get("label") or ""),
                "source": str(record.get("source") or ""),
                "model": str(record.get("model") or ""),
                "score": float(record.get("score") or 0.0),
            }
            for record in sorted(
                candidate_records,
                key=lambda record: float(record.get("score") or 0.0),
                reverse=True,
            )
        ],
        "suspicious_reasons": suspicious_reasons,
    }


def run_ocr_page_pair(
    *,
    original_file_path: Path,
    cleaned_file_path: Path,
    page_number: int,
    settings: Settings,
) -> str:
    comparison = compare_ocr_page_sources(
        original_file_path=original_file_path,
        cleaned_file_path=cleaned_file_path,
        page_number=page_number,
        settings=settings,
    )
    selected_markdown = comparison.get("selected_markdown")
    assert isinstance(selected_markdown, str)
    return selected_markdown


def _strip_wrapped_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) >= 2:
        return "\n".join(lines[1:-1]).strip()
    return stripped


def correct_ocr_markdown(
    *,
    markdown: str,
    settings: Settings,
) -> dict[str, object]:
    normalized_markdown = markdown.strip()
    if not normalized_markdown:
        return {
            "corrected_markdown": None,
            "correction_model": None,
            "correction_error": None,
            "correction_similarity": None,
        }

    if not settings.extraction_ready:
        return {
            "corrected_markdown": None,
            "correction_model": None,
            "correction_error": None,
            "correction_similarity": None,
        }

    client = OpenAI(
        api_key=settings.text_client_api_key,
        base_url=settings.text_base_url,
    )
    try:
        response = client.chat.completions.create(
            model=settings.text_model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "คุณคือผู้ช่วยแก้ข้อความ OCR ภาษาไทยจากเอกสารศาลและเอกสารราชการ "
                        "แก้เฉพาะคำที่น่าจะเป็น OCR error จากบริบทใกล้เคียง "
                        "สามารถเติมคำที่หายไปได้เฉพาะเมื่อมั่นใจสูงจากบริบทเดิม "
                        "ห้ามสรุป ห้ามแต่งใหม่ ห้ามเพิ่มข้อมูลใหม่ที่ไม่มีในข้อความ "
                        "ให้คงลำดับบรรทัดและย่อหน้าใกล้เคียงต้นฉบับมากที่สุด "
                        "ถ้าไม่แน่ใจให้คงข้อความเดิมไว้ "
                        "ตอบกลับเป็นข้อความล้วนที่แก้แล้วเท่านั้น"
                    ),
                },
                {
                    "role": "user",
                    "content": f"โปรดเกลาข้อความ OCR นี้ให้ถูกต้อง:\n\n{normalized_markdown}",
                },
            ],
        )
    except Exception as exc:
        return {
            "corrected_markdown": None,
            "correction_model": settings.text_model,
            "correction_error": str(exc),
            "correction_similarity": None,
        }

    raw_content = response.choices[0].message.content or ""
    corrected_markdown = _strip_wrapped_code_fence(raw_content)
    if not corrected_markdown:
        return {
            "corrected_markdown": None,
            "correction_model": settings.text_model,
            "correction_error": "Text correction returned an empty response.",
            "correction_similarity": None,
        }

    correction_similarity = SequenceMatcher(
        None,
        _normalize_text_for_similarity(normalized_markdown),
        _normalize_text_for_similarity(corrected_markdown),
    ).ratio()
    if correction_similarity < 0.3:
        return {
            "corrected_markdown": None,
            "correction_model": settings.text_model,
            "correction_error": (
                "Text correction changed the OCR too aggressively, so the raw OCR text was kept."
            ),
            "correction_similarity": correction_similarity,
        }

    return {
        "corrected_markdown": corrected_markdown,
        "correction_model": settings.text_model,
        "correction_error": None,
        "correction_similarity": correction_similarity,
    }


def join_markdown_pages(page_markdowns: list[str]) -> str:
    if len(page_markdowns) == 1:
        return page_markdowns[0]

    chunks: list[str] = []
    for index, markdown in enumerate(page_markdowns, start=1):
        chunks.append(f"## Page {index}\n\n{markdown.strip()}")
    return "\n\n---\n\n".join(chunks).strip()


def run_structured_extraction(
    *,
    markdown: str,
    extraction_prompt: str,
    settings: Settings,
) -> str:
    client = OpenAI(
        api_key=settings.text_client_api_key,
        base_url=settings.text_base_url,
    )
    response = client.chat.completions.create(
        model=settings.text_model,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You convert OCR markdown into structured JSON. "
                    "Return only valid JSON with no surrounding commentary."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Extraction goal:\n{extraction_prompt.strip()}\n\n"
                    f"OCR markdown:\n{markdown}"
                ),
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return content
