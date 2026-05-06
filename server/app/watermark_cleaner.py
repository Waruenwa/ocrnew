from __future__ import annotations

import argparse
import io
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image


# DEFAULT_INPUT = Path("01.pdf")
# DEFAULT_OUTPUT = Path("01_no_watermark.pdf")
WATERMARK_ANGLE = -32
DEFAULT_RENDER_DPI = 200
DETECTION_THRESHOLD = 250
LIGHT_COMPONENT_MIN_MEAN = 235
WHITE_CLIP_THRESHOLD = 232
DENSE_PAGE_PIXEL_RATIO = 0.035
DENSE_PAGE_BINARY_THRESHOLD = 200
WATERMARK_HEAVY_PAGE_RATIO = 0.04


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


def clean_image_array(image_array: np.ndarray) -> np.ndarray:
    watermark_mask = build_watermark_mask(image_array)
    cleaned = image_array.copy()
    cleaned[watermark_mask > 0] = 255
    cleaned[cleaned > WHITE_CLIP_THRESHOLD] = 255

    # Dense text pages look cleaner as binary output, while sparse stamp/signature pages
    # preserve detail better if we keep them in grayscale after whitening the watermark.
    # Some sparse pages still carry a lot of watermark coverage; those pages need the
    # binary fallback as well or the remaining gray watermark edges stay visible.
    dark_pixel_ratio = float((cleaned < 220).mean())
    watermark_pixel_ratio = float((watermark_mask > 0).mean())
    if dark_pixel_ratio >= DENSE_PAGE_PIXEL_RATIO or watermark_pixel_ratio >= WATERMARK_HEAVY_PAGE_RATIO:
        blurred = cv2.medianBlur(cleaned, 3)
        output_array = np.where(blurred < DENSE_PAGE_BINARY_THRESHOLD, 0, 255).astype("uint8")
    else:
        output_array = cleaned

    return output_array.astype("uint8")


def clean_image_array_soft(image_array: np.ndarray) -> np.ndarray:
    watermark_mask = build_watermark_mask(image_array)
    cleaned = image_array.copy()
    cleaned[watermark_mask > 0] = 255
    cleaned[cleaned > WHITE_CLIP_THRESHOLD + 10] = 255
    return cleaned.astype("uint8")


def clean_pil_image(image: Image.Image) -> Image.Image:
    image_array = np.array(image.convert("L"))
    return Image.fromarray(clean_image_array(image_array))


def clean_pil_image_soft(image: Image.Image) -> Image.Image:
    image_array = np.array(image.convert("L"))
    return Image.fromarray(clean_image_array_soft(image_array))


def clean_page_to_image(page: fitz.Page, dpi: int = DEFAULT_RENDER_DPI) -> Image.Image:
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("L")
    return clean_pil_image(image)


def clean_page_image(page: fitz.Page, dpi: int) -> bytes:
    output_image = clean_page_to_image(page, dpi=dpi)
    output_buffer = io.BytesIO()
    output_image.save(output_buffer, format="PNG", optimize=True)
    return output_buffer.getvalue()


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
    binary = np.where(rotated < DETECTION_THRESHOLD, 255, 0).astype("uint8")

    # After rotation, watermark words become roughly horizontal. A horizontal dilation
    # turns each watermark phrase into a compact component we can filter by shape.
    connected = cv2.dilate(
        binary,
        cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3)),
        iterations=1,
    )
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(connected, connectivity=8)
    rotated_mask = np.zeros_like(binary)

    for label in range(1, num_labels):
        _, _, component_width, component_height, area = stats[label]
        if area < 400 or area > 12000 or component_height < 16 or component_height > 60:
            continue

        aspect_ratio = component_width / max(component_height, 1)
        if aspect_ratio < 4.0:
            continue

        component_pixels = labels == label
        mean_value = float(rotated[component_pixels].mean())
        if mean_value < LIGHT_COMPONENT_MIN_MEAN:
            continue

        rotated_mask[component_pixels] = 255

    rotated_mask = cv2.erode(
        rotated_mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)),
        iterations=1,
    )
    rotated_mask = cv2.dilate(
        rotated_mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (11, 3)),
        iterations=1,
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
    return np.where(restored_mask > 32, 255, 0).astype("uint8")


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
