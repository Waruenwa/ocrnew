from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class AnchorLine:
    text: str
    bbox: tuple[float, float, float, float]
    source: str
    score: float | None = None


def detect_anchor_lines(
    image_path: Path,
    *,
    provider: str = "auto",
    lang: str = "th",
) -> list[AnchorLine]:
    requested_provider = (provider or "auto").strip().lower()
    if requested_provider in {"none", "off", "disabled"}:
        return []

    providers = (
        ("surya", _detect_with_surya),
        ("paddle", _detect_with_paddle),
        ("opencv", _detect_with_opencv),
    )
    for provider_name, detector in providers:
        if requested_provider not in {"auto", provider_name}:
            continue
        try:
            lines = detector(image_path, lang=lang)
        except Exception as exc:  # pragma: no cover - optional providers vary by install
            if requested_provider == provider_name or not isinstance(exc, ModuleNotFoundError):
                print(
                    f"[imports] Anchor provider {provider_name} unavailable: {exc}",
                    flush=True,
                )
            lines = []
        if lines:
            return lines
        if requested_provider == provider_name:
            return []

    return []


def _detect_with_opencv(image_path: Path, *, lang: str) -> list[AnchorLine]:
    del lang
    with Image.open(image_path) as image:
        grayscale = np.array(image.convert("L"))

    height, width = grayscale.shape[:2]
    if width <= 0 or height <= 0:
        return []

    header_lines: list[AnchorLine] = []
    header_lines.extend(
        _find_ink_row_groups(
            grayscale,
            region=(0.60, 0.04, 0.95, 0.25),
            source="opencv_header_right",
            min_width=0.22,
            min_top=0.075,
            max_bottom=0.20,
            merge_gap=0.006,
        )
    )
    header_lines.extend(
        _find_ink_row_groups(
            grayscale,
            region=(0.25, 0.14, 0.75, 0.36),
            source="opencv_header_center",
            min_width=0.08,
            min_top=0.14,
            max_bottom=0.36,
            merge_gap=0.008,
        )
    )
    return header_lines


def _find_ink_row_groups(
    grayscale: np.ndarray,
    *,
    region: tuple[float, float, float, float],
    source: str,
    min_width: float,
    min_top: float,
    max_bottom: float,
    merge_gap: float,
) -> list[AnchorLine]:
    height, width = grayscale.shape[:2]
    left = max(0, int(round(width * region[0])))
    top = max(0, int(round(height * region[1])))
    right = min(width, int(round(width * region[2])))
    bottom = min(height, int(round(height * region[3])))
    if right <= left or bottom <= top:
        return []

    crop = grayscale[top:bottom, left:right]
    ink_mask = crop < 90
    row_counts = ink_mask.sum(axis=1)
    min_ink = max(8, int((right - left) * 0.008))
    raw_groups: list[tuple[int, int]] = []
    start: int | None = None
    for row_index, count in enumerate(row_counts):
        if count >= min_ink:
            if start is None:
                start = row_index
            continue
        if start is not None:
            if row_index - start >= 4:
                raw_groups.append((start, row_index - 1))
            start = None
    if start is not None and len(row_counts) - start >= 4:
        raw_groups.append((start, len(row_counts) - 1))

    groups: list[tuple[int, int, int, int, int]] = []
    for row_start, row_end in raw_groups:
        group_mask = ink_mask[row_start : row_end + 1]
        columns = np.where(group_mask.any(axis=0))[0]
        if columns.size == 0:
            continue
        x0 = int(left + columns.min())
        x1 = int(left + columns.max())
        y0 = int(top + row_start)
        y1 = int(top + row_end)
        groups.append((x0, y0, x1, y1, int(group_mask.sum())))

    merged_groups = _merge_opencv_groups(groups, width, height, merge_gap)
    lines: list[AnchorLine] = []
    for x0, y0, x1, y1, area in merged_groups:
        bbox = (
            round(x0 / width, 6),
            round(y0 / height, 6),
            round(x1 / width, 6),
            round(y1 / height, 6),
        )
        box_width = bbox[2] - bbox[0]
        if box_width < min_width or bbox[1] < min_top or bbox[3] > max_bottom:
            continue
        lines.append(AnchorLine(text="", bbox=bbox, source=source, score=float(area)))

    return sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))


def _merge_opencv_groups(
    groups: list[tuple[int, int, int, int, int]],
    width: int,
    height: int,
    merge_gap: float,
) -> list[tuple[int, int, int, int, int]]:
    if not groups:
        return []

    sorted_groups = sorted(groups, key=lambda group: group[1])
    merged: list[tuple[int, int, int, int, int]] = []
    max_pixel_gap = max(1, int(round(height * merge_gap)))
    for group in sorted_groups:
        if not merged:
            merged.append(group)
            continue

        previous = merged[-1]
        vertical_gap = group[1] - previous[3]
        horizontal_overlap = min(previous[2], group[2]) - max(previous[0], group[0])
        should_merge = vertical_gap <= max_pixel_gap and horizontal_overlap > -int(width * 0.04)
        if not should_merge:
            merged.append(group)
            continue

        merged[-1] = (
            min(previous[0], group[0]),
            min(previous[1], group[1]),
            max(previous[2], group[2]),
            max(previous[3], group[3]),
            previous[4] + group[4],
        )

    return merged


def _detect_with_paddle(image_path: Path, *, lang: str) -> list[AnchorLine]:
    paddle_ocr = _get_paddle_ocr(lang)
    raw_result = _call_paddle_ocr(paddle_ocr, image_path)
    return _parse_paddle_result(raw_result, image_path=image_path)


@lru_cache(maxsize=4)
def _get_paddle_ocr(lang: str) -> Any:
    from paddleocr import PaddleOCR  # type: ignore

    try:
        return PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    except TypeError:
        return PaddleOCR(lang=lang)


def _call_paddle_ocr(paddle_ocr: Any, image_path: Path) -> Any:
    if hasattr(paddle_ocr, "ocr"):
        try:
            return paddle_ocr.ocr(str(image_path), cls=True)
        except TypeError:
            return paddle_ocr.ocr(str(image_path))
    if hasattr(paddle_ocr, "predict"):
        return paddle_ocr.predict(str(image_path))
    raise RuntimeError("Unsupported PaddleOCR API.")


def _parse_paddle_result(raw_result: Any, *, image_path: Path) -> list[AnchorLine]:
    with Image.open(image_path) as image:
        width, height = image.size

    lines: list[AnchorLine] = []
    for item in _walk_paddle_items(raw_result):
        box = item.get("box")
        text = str(item.get("text") or "").strip()
        score = item.get("score")
        bbox = _polygon_to_bbox(box, width=width, height=height)
        if bbox is None:
            continue
        lines.append(AnchorLine(text=text, bbox=bbox, source="paddle", score=score))
    return lines


def _walk_paddle_items(raw_result: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(raw_result, dict):
        rec_texts = raw_result.get("rec_texts")
        rec_scores = raw_result.get("rec_scores") or []
        rec_polys = raw_result.get("rec_polys") or raw_result.get("dt_polys") or raw_result.get("rec_boxes")
        if isinstance(rec_texts, list) and isinstance(rec_polys, list):
            for index, text in enumerate(rec_texts):
                items.append(
                    {
                        "box": rec_polys[index] if index < len(rec_polys) else None,
                        "text": text,
                        "score": rec_scores[index] if index < len(rec_scores) else None,
                    }
                )
        return items

    if isinstance(raw_result, (list, tuple)):
        if len(raw_result) >= 2 and isinstance(raw_result[1], (list, tuple)) and isinstance(raw_result[1][0] if raw_result[1] else "", str):
            items.append(
                {
                    "box": raw_result[0],
                    "text": raw_result[1][0],
                    "score": raw_result[1][1] if len(raw_result[1]) > 1 else None,
                }
            )
            return items
        for entry in raw_result:
            items.extend(_walk_paddle_items(entry))
    return items


def _detect_with_surya(image_path: Path, *, lang: str) -> list[AnchorLine]:
    with Image.open(image_path) as image:
        rgb_image = image.convert("RGB")
        width, height = rgb_image.size
        recognition_predictor, detection_predictor = _get_surya_predictors()
        predictions = recognition_predictor([rgb_image], [[lang]], detection_predictor)

    if not predictions:
        return []

    result = predictions[0]
    raw_lines = getattr(result, "text_lines", None)
    if raw_lines is None and isinstance(result, dict):
        raw_lines = result.get("text_lines")
    if not raw_lines:
        return []

    lines: list[AnchorLine] = []
    for raw_line in raw_lines:
        text = _get_object_value(raw_line, "text")
        score = _get_object_value(raw_line, "confidence")
        bbox_raw = _get_object_value(raw_line, "bbox") or _get_object_value(raw_line, "polygon")
        bbox = _polygon_to_bbox(bbox_raw, width=width, height=height)
        if bbox is None:
            continue
        lines.append(AnchorLine(text=str(text or "").strip(), bbox=bbox, source="surya", score=score))
    return lines


@lru_cache(maxsize=1)
def _get_surya_predictors() -> tuple[Any, Any]:
    from surya.detection import DetectionPredictor  # type: ignore
    from surya.recognition import RecognitionPredictor  # type: ignore

    return RecognitionPredictor(), DetectionPredictor()


def _get_object_value(raw_object: Any, key: str) -> Any:
    if isinstance(raw_object, dict):
        return raw_object.get(key)
    return getattr(raw_object, key, None)


def _polygon_to_bbox(
    raw_box: Any,
    *,
    width: int,
    height: int,
) -> tuple[float, float, float, float] | None:
    if raw_box is None or width <= 0 or height <= 0:
        return None

    try:
        array = np.array(raw_box, dtype=float)
    except (TypeError, ValueError):
        return None

    if array.size == 4 and array.ndim == 1:
        left, top, right, bottom = array.tolist()
    elif array.ndim >= 2 and array.shape[-1] >= 2:
        points = array.reshape(-1, array.shape[-1])
        left = float(points[:, 0].min())
        top = float(points[:, 1].min())
        right = float(points[:, 0].max())
        bottom = float(points[:, 1].max())
    else:
        return None

    if max(abs(left), abs(top), abs(right), abs(bottom)) > 1.5:
        left /= width
        right /= width
        top /= height
        bottom /= height

    left = max(0.0, min(float(left), 1.0))
    top = max(0.0, min(float(top), 1.0))
    right = max(0.0, min(float(right), 1.0))
    bottom = max(0.0, min(float(bottom), 1.0))
    if right <= left or bottom <= top:
        return None
    return (round(left, 6), round(top, 6), round(right, 6), round(bottom, 6))
