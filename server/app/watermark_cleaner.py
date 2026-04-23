from __future__ import annotations

import argparse
import io
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image


DEFAULT_INPUT = Path("01.pdf")
DEFAULT_OUTPUT = Path("01_no_watermark.pdf")
WATERMARK_ANGLE = -32
DEFAULT_RENDER_DPI = 260
DETECTION_THRESHOLD = 248
LIGHT_STROKE_MIN_VALUE = 155
LIGHT_COMPONENT_MIN_MEAN = 188
WHITE_CLIP_THRESHOLD = 235
BACKGROUND_CLIP_THRESHOLD = 248
OCR_TEXT_KEEP_THRESHOLD = 230
OCR_TEXT_DARKEN_SCALE = 0.80
OCR_TEXT_DARKEN_OFFSET = 6.0
OCR_SPECK_MIN_AREA = 2
TEXT_PROTECT_THRESHOLD = 190
TEXT_STRONG_THRESHOLD = 165
MASK_SOFTEN_SIGMA = 2.2
MASK_EXPAND_SIGMA = 7.0
GENERAL_WHITEN_STRENGTH = 1.0
PROTECTED_WHITEN_STRENGTH = 0.34
EXTRA_BACKGROUND_WHITEN_STRENGTH = 0.62
STRONG_TEXT_MAX_LIFT = 6.0
RESTORED_MASK_THRESHOLD = 8
HARD_WATERMARK_MIN_VALUE = 168


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Remove light gray scan watermarks from a PDF by rebuilding each page as a cleaned image."
    )
    parser.add_argument(
        "input_pdf",
        nargs="?",
        default=str(DEFAULT_INPUT),
        help=f"Input PDF path. Default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output PDF path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_RENDER_DPI,
        help=(
            "Render DPI before cleaning. Higher values keep more detail but create larger files. "
            f"Default: {DEFAULT_RENDER_DPI}"
        ),
    )
    return parser


def clean_page_image(page: fitz.Page, dpi: int) -> bytes:
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("L")
    image_array = np.array(image)

    watermark_mask = build_watermark_mask(image_array)
    output_array = apply_soft_watermark_cleanup(image_array, watermark_mask)

    output_buffer = io.BytesIO()
    Image.fromarray(output_array).save(output_buffer, format="PNG", optimize=True)
    return output_buffer.getvalue()


def apply_soft_watermark_cleanup(
    image_array: np.ndarray,
    watermark_mask: np.ndarray,
) -> np.ndarray:
    base = image_array.astype("float32")
    softened_mask = cv2.GaussianBlur(watermark_mask, (0, 0), MASK_SOFTEN_SIGMA).astype("float32") / 255.0
    expanded_mask = cv2.dilate(
        watermark_mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 7)),
        iterations=2,
    )
    expanded_mask = cv2.GaussianBlur(expanded_mask, (0, 0), MASK_EXPAND_SIGMA).astype("float32") / 255.0

    protected_text = np.where(image_array < TEXT_PROTECT_THRESHOLD, 255, 0).astype("uint8")
    strong_text = np.where(image_array < TEXT_STRONG_THRESHOLD, 255, 0).astype("uint8")
    protected_text = cv2.dilate(
        protected_text,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=1,
    )
    strong_text = cv2.dilate(
        strong_text,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        iterations=1,
    )

    whitening_strength = np.full(base.shape, GENERAL_WHITEN_STRENGTH, dtype="float32")
    whitening_strength = np.where(protected_text > 0, PROTECTED_WHITEN_STRENGTH, whitening_strength)

    lifted = base + (softened_mask * whitening_strength * (255.0 - base))
    extra_background_lift = expanded_mask * EXTRA_BACKGROUND_WHITEN_STRENGTH * (255.0 - base)
    lifted = np.where(protected_text > 0, lifted, lifted + extra_background_lift)

    hard_watermark_pixels = (
        ((watermark_mask > 0) | (expanded_mask > 0.28))
        & (image_array >= HARD_WATERMARK_MIN_VALUE)
        & (strong_text == 0)
    )
    lifted = np.where(hard_watermark_pixels, 255.0, lifted)
    lifted = np.where(strong_text > 0, np.minimum(lifted, base + STRONG_TEXT_MAX_LIFT), lifted)
    lifted = np.clip(lifted, 0, 255)

    watermark_clip_pixels = (
        ((watermark_mask > 0) | (expanded_mask > 0.18))
        & (lifted > WHITE_CLIP_THRESHOLD)
        & (strong_text == 0)
    )
    lifted = np.where(watermark_clip_pixels, 255.0, lifted)
    lifted = np.where(lifted > BACKGROUND_CLIP_THRESHOLD, 255.0, lifted)

    return apply_ocr_contrast_cleanup(lifted.astype("uint8"))


def apply_ocr_contrast_cleanup(image_array: np.ndarray) -> np.ndarray:
    text_pixels = image_array < OCR_TEXT_KEEP_THRESHOLD
    output = np.full(image_array.shape, 255, dtype="uint8")
    darkened_text = (
        (image_array.astype("float32") * OCR_TEXT_DARKEN_SCALE)
        - OCR_TEXT_DARKEN_OFFSET
    )
    output[text_pixels] = np.clip(darkened_text, 0, 255).astype("uint8")[text_pixels]

    ink = np.where(output < 245, 255, 0).astype("uint8")
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    cleaned_ink = np.zeros_like(ink)
    for label in range(1, num_labels):
        area = stats[label, cv2.CC_STAT_AREA]
        if area < OCR_SPECK_MIN_AREA:
            continue
        cleaned_ink[labels == label] = 255

    return np.where(cleaned_ink > 0, output, 255).astype("uint8")


def build_watermark_mask(image_array: np.ndarray) -> np.ndarray:
    height, width = image_array.shape
    center = (width // 2, height // 2)
    rotate_matrix = cv2.getRotationMatrix2D(center, WATERMARK_ANGLE, 1.0)

    rotated = cv2.warpAffine(
        image_array,
        rotate_matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    light_strokes = np.where(
        (rotated < DETECTION_THRESHOLD)
        & (rotated >= LIGHT_STROKE_MIN_VALUE),
        255,
        0,
    ).astype("uint8")
    dark_strokes = np.where(rotated < TEXT_STRONG_THRESHOLD, 255, 0).astype("uint8")

    # After rotation, watermark words become roughly horizontal. A horizontal dilation
    # turns each watermark phrase into a compact component we can filter by shape.
    connected = cv2.dilate(
        light_strokes,
        cv2.getStructuringElement(cv2.MORPH_RECT, (47, 7)),
        iterations=1,
    )
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(connected, connectivity=8)
    rotated_mask = np.zeros_like(light_strokes)

    for label in range(1, num_labels):
        _, _, component_width, component_height, area = stats[label]
        if area < 140 or area > 42000 or component_height < 8 or component_height > 96:
            continue

        aspect_ratio = component_width / max(component_height, 1)
        if aspect_ratio < 2.2:
            continue

        component_pixels = labels == label
        stroke_pixels = (light_strokes > 0) & component_pixels
        stroke_count = int(stroke_pixels.sum())
        if stroke_count < 20:
            continue

        stroke_mean = float(rotated[stroke_pixels].mean())
        if stroke_mean < LIGHT_COMPONENT_MIN_MEAN:
            continue

        dark_ratio = float(((dark_strokes > 0) & component_pixels).sum()) / max(area, 1)
        if dark_ratio > 0.035:
            continue

        rotated_mask[component_pixels] = 255

    rotated_mask = cv2.erode(
        rotated_mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)),
        iterations=1,
    )
    rotated_mask = cv2.dilate(
        rotated_mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (27, 7)),
        iterations=2,
    )

    reverse_rotate_matrix = cv2.getRotationMatrix2D(center, -WATERMARK_ANGLE, 1.0)
    restored_mask = cv2.warpAffine(
        rotated_mask,
        reverse_rotate_matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return np.where(restored_mask > RESTORED_MASK_THRESHOLD, 255, 0).astype("uint8")


def remove_watermark(input_pdf: Path, output_pdf: Path, dpi: int) -> None:
    if not input_pdf.exists():
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")

    source_pdf = fitz.open(input_pdf)
    output_pdf_doc = fitz.open()

    try:
        for index, page in enumerate(source_pdf, start=1):
            cleaned_image = clean_page_image(page, dpi=dpi)
            new_page = output_pdf_doc.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(page.rect, stream=cleaned_image)
            print(f"Processed page {index}/{source_pdf.page_count}")

        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        output_pdf_doc.save(output_pdf, deflate=True, garbage=4)
    finally:
        output_pdf_doc.close()
        source_pdf.close()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    input_pdf = Path(args.input_pdf).expanduser().resolve()
    output_pdf = Path(args.output).expanduser().resolve()

    remove_watermark(input_pdf=input_pdf, output_pdf=output_pdf, dpi=args.dpi)
    print(f"Saved cleaned PDF to: {output_pdf}")


if __name__ == "__main__":
    main()
