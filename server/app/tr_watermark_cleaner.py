from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image


TR_DOCUMENT_CATEGORY = "tr"
TR_WATERMARK_SCORE_THRESHOLD = 0.012
DOTTED_WATERMARK_SCORE_THRESHOLD = 0.025
DOTTED_TEXT_KEEP_THRESHOLD = 122
LIGHT_PIXEL_LOW = 138
LIGHT_PIXEL_HIGH = 246
DARK_TEXT_THRESHOLD = 115


@dataclass(frozen=True)
class TrWatermarkAnalysis:
    detected: bool
    score: float
    light_pixel_ratio: float
    dotted_pixel_ratio: float = 0.0
    watermark_kind: str = "none"


@dataclass(frozen=True)
class TrCleanResult:
    image: Image.Image
    analysis: TrWatermarkAnalysis
    cleaning_mode: str


def is_tr_document_category(raw_value: str | None) -> bool:
    normalized = (raw_value or "").strip().lower().replace(" ", "_")
    return normalized in {TR_DOCUMENT_CATEGORY, "tor_ror"}


def detect_tr_watermark(image: Image.Image) -> TrWatermarkAnalysis:
    gray = np.array(image.convert("L"))
    candidate_mask = _build_tr_light_texture_mask(gray)
    focus_mask = _focus_scan_area(candidate_mask)
    total_pixels = max(focus_mask.size, 1)
    light_pixel_ratio = float(np.count_nonzero(focus_mask) / total_pixels)
    dotted_pixel_ratio = _calculate_dotted_watermark_ratio(gray)
    score = max(light_pixel_ratio, dotted_pixel_ratio)
    watermark_kind = (
        "dotted"
        if dotted_pixel_ratio >= DOTTED_WATERMARK_SCORE_THRESHOLD
        else "light"
        if light_pixel_ratio >= TR_WATERMARK_SCORE_THRESHOLD
        else "none"
    )

    return TrWatermarkAnalysis(
        detected=watermark_kind != "none",
        score=score,
        light_pixel_ratio=light_pixel_ratio,
        dotted_pixel_ratio=dotted_pixel_ratio,
        watermark_kind=watermark_kind,
    )


def build_tr_cleaned_image(image: Image.Image) -> TrCleanResult:
    analysis = detect_tr_watermark(image)
    if not analysis.detected:
        return TrCleanResult(
            image=image.convert("L"),
            analysis=analysis,
            cleaning_mode="tr_original_no_watermark",
        )

    if analysis.watermark_kind == "dotted":
        return TrCleanResult(
            image=clean_tr_dotted_watermark_image(image),
            analysis=analysis,
            cleaning_mode="tr_dotted_watermark_cleaned",
        )

    return TrCleanResult(
        image=clean_tr_light_watermark_image(image),
        analysis=analysis,
        cleaning_mode="tr_watermark_cleaned",
    )


def clean_tr_light_watermark_image(image: Image.Image) -> Image.Image:
    gray = np.array(image.convert("L"))
    texture_mask = _build_tr_light_texture_mask(gray)
    cleaned = gray.copy()
    cleaned[texture_mask > 0] = 255
    cleaned[cleaned > 250] = 255
    return Image.fromarray(cleaned.astype("uint8"))


def clean_tr_dotted_watermark_image(image: Image.Image) -> Image.Image:
    gray = np.array(image.convert("L"))

    # The dotted TR watermark is made from medium-dark halftone dots. Keeping only
    # very dark ink removes most dots while preserving the printed text and rules.
    binary = np.where(gray < DOTTED_TEXT_KEEP_THRESHOLD, 0, 255).astype("uint8")

    # Dots that remain are usually isolated specks. Remove tiny components unless
    # they are connected to stronger ink such as Thai tone marks or line strokes.
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(255 - binary, connectivity=8)
    ink = np.zeros_like(binary)
    for label in range(1, num_labels):
        _, _, width, height, area = stats[label]
        if area >= 3 or width >= 3 or height >= 3:
            ink[labels == label] = 255

    cleaned = np.where(ink > 0, 0, 255).astype("uint8")
    return Image.fromarray(cleaned)


def _build_tr_light_texture_mask(gray: np.ndarray) -> np.ndarray:
    light_mask = cv2.inRange(gray, LIGHT_PIXEL_LOW, LIGHT_PIXEL_HIGH)

    # Keep anti-aliased text edges intact by protecting a small area around dark text.
    dark_text_mask = cv2.inRange(gray, 0, DARK_TEXT_THRESHOLD)
    protected_text_mask = cv2.dilate(
        dark_text_mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=1,
    )
    light_mask[protected_text_mask > 0] = 0

    # The TR watermark appears as broad, repeated pale texture. A small opening
    # removes isolated scan specks while keeping the repeated watermark pattern.
    opened = cv2.morphologyEx(
        light_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        iterations=1,
    )
    return opened.astype("uint8")


def _calculate_dotted_watermark_ratio(gray: np.ndarray) -> float:
    focus_area = _focus_scan_area(gray)
    if focus_area.size == 0:
        return 0.0

    medium_dark_mask = cv2.inRange(focus_area, 90, 210)
    return float(np.count_nonzero(medium_dark_mask) / max(medium_dark_mask.size, 1))


def _focus_scan_area(mask: np.ndarray) -> np.ndarray:
    height = mask.shape[0]
    top = int(height * 0.02)
    bottom = int(height * 0.72)
    if bottom <= top:
        return mask
    return mask[top:bottom, :]
