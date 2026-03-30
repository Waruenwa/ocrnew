from __future__ import annotations

import base64
import json
import tempfile
from itertools import permutations
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from openai import OpenAI
from PIL import Image, ImageFilter
from pypdf import PdfReader
from typhoon_ocr import ocr_document
from typhoon_ocr.ocr_utils import render_pdf_to_base64png

from app.config import Settings


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


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


def _run_ocr(
    file_path: Path,
    settings: Settings,
    *,
    page_number: int | None,
    target_image_dim: int,
) -> str:
    kwargs = {
        "pdf_or_image_path": str(file_path),
        "base_url": settings.typhoon_base_url,
        "api_key": settings.ocr_api_key,
        "target_image_dim": target_image_dim,
    }
    if page_number is not None and file_path.suffix.lower() == ".pdf":
        kwargs["page_num"] = page_number
    return ocr_document(**kwargs)


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
    cleaned = grayscale.point(lambda pixel: 255 if pixel > 200 else pixel)
    cleaned = cleaned.filter(ImageFilter.SHARPEN)
    return cleaned.convert("RGB")


def _extract_candidate_lines(markdown: str) -> list[str]:
    candidate_lines: list[str] = []
    inside_figure = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("## Page"):
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

        candidate_lines.append(line)

    return candidate_lines


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
    if x0 < page_width * 0.28 and height > max(48.0, median_height * 2.8):
        adjusted_y -= min(height * 0.45, median_height * 3.0)
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

    return ordered_boxes


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

    return width_cost + height_cost + footer_noise_cost


def _select_best_box_subset(
    candidate_lines: list[str],
    line_boxes: list[tuple[int, int, int, int]],
    *,
    page_width: int,
    page_height: int,
) -> list[tuple[int, int, int, int]]:
    line_count = len(candidate_lines)
    box_count = len(line_boxes)
    if box_count < line_count:
        return line_boxes

    max_text_length = max(_saturated_text_length(line) for line in candidate_lines)
    max_box_width = max(box[2] - box[0] for box in line_boxes)
    median_box_height = float(np.median([box[3] - box[1] for box in line_boxes]))

    dp: list[list[float]] = [[float("inf")] * (box_count + 1) for _ in range(line_count + 1)]
    choice: list[list[int | None]] = [[None] * (box_count + 1) for _ in range(line_count)]
    for box_index in range(box_count + 1):
        dp[line_count][box_index] = 0.0

    for line_index in range(line_count - 1, -1, -1):
        remaining_lines = line_count - line_index
        for box_index in range(box_count - 1, -1, -1):
            remaining_boxes = box_count - box_index
            if remaining_boxes < remaining_lines:
                continue

            max_skip = remaining_boxes - remaining_lines
            for skip_count in range(max_skip + 1):
                selected_box_index = box_index + skip_count
                cost = _box_assignment_cost(
                    candidate_lines[line_index],
                    line_boxes[selected_box_index],
                    max_text_length=max_text_length,
                    max_box_width=max_box_width,
                    median_box_height=median_box_height,
                    page_width=page_width,
                    page_height=page_height,
                    line_index=line_index,
                    line_count=line_count,
                )
                total_cost = cost + dp[line_index + 1][selected_box_index + 1]
                if total_cost < dp[line_index][box_index]:
                    dp[line_index][box_index] = total_cost
                    choice[line_index][box_index] = selected_box_index

    selected_boxes: list[tuple[int, int, int, int]] = []
    cursor = 0
    for line_index in range(line_count):
        selected_box_index = choice[line_index][cursor]
        if selected_box_index is None:
            return line_boxes
        selected_boxes.append(line_boxes[selected_box_index])
        cursor = selected_box_index + 1

    return selected_boxes


def build_page_segments(
    page_image: Image.Image,
    markdown: str,
    page_number: int,
) -> list[dict[str, object]]:
    candidate_lines = _extract_candidate_lines(markdown)
    if not candidate_lines:
        return []

    line_boxes = _detect_line_boxes(page_image)
    if not line_boxes:
        return []

    page_width = max(page_image.width, 1)
    page_height = max(page_image.height, 1)

    segments: list[dict[str, object]] = []
    if len(line_boxes) >= len(candidate_lines):
        ordered_boxes = line_boxes
        if len(line_boxes) == len(candidate_lines):
            aligned_boxes: list[tuple[int, int, int, int]] = []
            line_offset = 0
            for section_boxes in _group_boxes_into_sections(line_boxes):
                section_lines = candidate_lines[line_offset : line_offset + len(section_boxes)]
                if len(section_lines) != len(section_boxes):
                    aligned_boxes = line_boxes
                    break
                aligned_boxes.extend(_match_section_boxes_to_lines(section_lines, section_boxes))
                line_offset += len(section_boxes)

            if len(aligned_boxes) == len(candidate_lines):
                ordered_boxes = aligned_boxes
        else:
            ordered_boxes = _select_best_box_subset(
                candidate_lines,
                line_boxes,
                page_width=page_width,
                page_height=page_height,
            )

        for line_index, line_text in enumerate(candidate_lines):
            box_start, box_end = _select_box_span(line_index, len(candidate_lines), len(ordered_boxes))
            assigned_boxes = ordered_boxes[box_start:box_end]
            if not assigned_boxes:
                continue

            x0, y0, x1, y1 = _combine_boxes(assigned_boxes)
            segments.append(
                {
                    "id": f"page-{page_number}-segment-{line_index + 1}",
                    "text": line_text,
                    "page_number": page_number,
                    "bbox": (
                        round(x0 / page_width, 6),
                        round(y0 / page_height, 6),
                        round(x1 / page_width, 6),
                        round(y1 / page_height, 6),
                    ),
                }
            )
        return segments

    for box_index, line_box in enumerate(line_boxes):
        line_start = round(box_index * len(candidate_lines) / len(line_boxes))
        line_end = round((box_index + 1) * len(candidate_lines) / len(line_boxes))
        if line_end <= line_start:
            line_end = min(line_start + 1, len(candidate_lines))

        text = " ".join(candidate_lines[line_start:line_end]).strip()
        if not text:
            continue

        x0, y0, x1, y1 = line_box
        segments.append(
            {
                "id": f"page-{page_number}-segment-{box_index + 1}",
                "text": text,
                "page_number": page_number,
                "bbox": (
                    round(x0 / page_width, 6),
                    round(y0 / page_height, 6),
                    round(x1 / page_width, 6),
                    round(y1 / page_height, 6),
                ),
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


def run_ocr_page(file_path: Path, page_number: int, settings: Settings) -> str:
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
                target_image_dim=1200,
            )
        finally:
            cleaned_input_path.unlink(missing_ok=True)

    try:
        return _run_ocr(
            file_path,
            settings,
            page_number=page_number,
            target_image_dim=1800,
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
                target_image_dim=1200,
            )
        except Exception as cleaned_error:
            raise RuntimeError(
                f"Page {page_number} OCR failed. "
                f"Original attempt: {original_error}. "
                f"Cleaned-image fallback: {cleaned_error}"
            ) from cleaned_error
        finally:
            cleaned_input_path.unlink(missing_ok=True)


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
        api_key=settings.text_api_key,
        base_url=settings.typhoon_base_url,
    )
    response = client.chat.completions.create(
        model=settings.typhoon_text_model,
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
