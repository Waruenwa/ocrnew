from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from app.watermark_cleaner import remove_watermark

DEFAULT_SOURCE_DIR = Path(r"C:\Users\waruen.w\Desktop\คำพิพากษา")
DEFAULT_PARK_DIR = Path(__file__).resolve().parent / "Park"
SUPPORTED_SUFFIXES = {".pdf"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy PDFs from a source folder into Park/origin and generate watermark-removed "
            "versions into Park/doit."
        )
    )
    parser.add_argument(
        "--source-dir",
        default=str(DEFAULT_SOURCE_DIR),
        help=f"Folder to scan for source PDFs. Default: {DEFAULT_SOURCE_DIR}",
    )
    parser.add_argument(
        "--park-dir",
        default=str(DEFAULT_PARK_DIR),
        help=f"Destination Park folder. Default: {DEFAULT_PARK_DIR}",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Render DPI passed to the watermark-removal step. Default: 200",
    )
    return parser


def iter_source_pdfs(source_dir: Path) -> list[Path]:
    return sorted(
        file_path
        for file_path in source_dir.iterdir()
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def process_folder(*, source_dir: Path, park_dir: Path, dpi: int) -> list[dict[str, str]]:
    if not source_dir.exists():
        raise FileNotFoundError(f"Source folder not found: {source_dir}")

    origin_dir = park_dir / "origin"
    doit_dir = park_dir / "doit"
    origin_dir.mkdir(parents=True, exist_ok=True)
    doit_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, str]] = []
    for source_file in iter_source_pdfs(source_dir):
        origin_file = origin_dir / source_file.name
        doit_file = doit_dir / source_file.name

        shutil.copy2(source_file, origin_file)
        remove_watermark(source_file, doit_file, dpi=dpi)
        source_file.unlink(missing_ok=True)

        results.append(
            {
                "source": str(source_file),
                "origin": str(origin_file),
                "doit": str(doit_file),
                "source_deleted": "true",
            }
        )

    return results


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    source_dir = Path(args.source_dir).expanduser().resolve()
    park_dir = Path(args.park_dir).expanduser().resolve()
    results = process_folder(source_dir=source_dir, park_dir=park_dir, dpi=args.dpi)

    print(f"source_dir={source_dir}")
    print(f"park_dir={park_dir}")
    print(f"processed_files={len(results)}")
    for result in results:
        print(f"- source: {result['source']}")
        print(f"  origin: {result['origin']}")
        print(f"  doit:   {result['doit']}")


if __name__ == "__main__":
    main()
