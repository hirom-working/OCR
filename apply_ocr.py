#!/usr/bin/env python3
"""
Apply OCR results to PDF files.

Supports multiple result formats:
1. Individual: output/<pdf_name>/<pdf_name>/results.json
2. Batch: output/results.json (multiple PDFs in one file)

Usage:
    python apply_ocr.py <pdf_path_or_directory> [--results-dir <dir>] [--output-dir <dir>]

Example:
    python apply_ocr.py "/path/to/book.pdf"
    python apply_ocr.py "/path/to/books/" --results-dir ./ocr_results
    python apply_ocr.py "/path/to/books/" --output-dir ./searchable
"""

import argparse
import json
import sys
import tempfile
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF


def find_results_json(pdf_name: str, results_dir: Path) -> tuple[Path | None, str | None]:
    """
    Find results.json for given PDF.
    Returns (path_to_json, key_in_json) or (None, None) if not found.
    """
    pdf_name_normalized = unicodedata.normalize("NFC", pdf_name)

    # 1. Check for batch results.json in results_dir root
    batch_results = results_dir / "results.json"
    if batch_results.exists():
        return batch_results, pdf_name

    # 2. Check for individual folder structure: <pdf_name>/<pdf_name>/results.json
    for folder in results_dir.iterdir():
        if not folder.is_dir():
            continue
        folder_normalized = unicodedata.normalize("NFC", folder.name)
        if folder_normalized == pdf_name_normalized:
            # Try nested structure first
            nested = folder / folder.name / "results.json"
            if nested.exists():
                return nested, None
            # Try direct
            direct = folder / "results.json"
            if direct.exists():
                return direct, None

    # 3. Check subdirectories for batch results.json
    for subdir in results_dir.iterdir():
        if subdir.is_dir():
            batch_results = subdir / "results.json"
            if batch_results.exists():
                return batch_results, pdf_name

    return None, None


def load_ocr_data(results_json: Path, pdf_name: str | None) -> list | None:
    """Load OCR page data from results.json."""
    with open(results_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    # If data is a list, it's already page data
    if isinstance(data, list):
        return data

    # If no pdf_name specified and only one key, use it
    if pdf_name is None:
        if len(data) == 1:
            return list(data.values())[0]
        return None

    # Direct key match
    if pdf_name in data:
        return data[pdf_name]

    # Normalized match
    pdf_name_normalized = unicodedata.normalize("NFC", pdf_name)
    for key, value in data.items():
        key_normalized = unicodedata.normalize("NFC", key)
        if key_normalized == pdf_name_normalized:
            return value

    # Partial match (batch processing sometimes truncates names)
    for key, value in data.items():
        key_normalized = unicodedata.normalize("NFC", key)
        # Match if PDF name starts with key or vice versa
        if pdf_name_normalized.startswith(key_normalized):
            return value
        if key_normalized.startswith(pdf_name_normalized):
            return value
        # Match base name without extension
        key_base = key_normalized.replace(".pdf", "").strip()
        if pdf_name_normalized == key_base or pdf_name_normalized.startswith(key_base):
            return value

    return None


def apply_ocr_to_pdf(input_pdf: Path, ocr_data: list, output_pdf: Path | None = None, overwrite: bool = False):
    """Apply OCR text layer to PDF."""
    doc = fitz.open(input_pdf)
    print(f"  Processing {len(doc)} pages...")

    pages_with_text = 0
    for page_num, page_data in enumerate(ocr_data):
        if page_num >= len(doc):
            break

        page = doc[page_num]
        page_rect = page.rect

        # Collect all text from OCR result
        all_text = []
        for line in page_data.get("text_lines", []):
            text = line.get("text", "").strip()
            if text:
                all_text.append(text)

        if not all_text:
            continue

        combined = " ".join(all_text)
        pages_with_text += 1

        # Add invisible textbox covering the page
        page.insert_textbox(
            page_rect,
            combined,
            fontname="japan",
            fontsize=1,
            color=(1, 1, 1),
            render_mode=3,  # Invisible
        )

    # Determine output path
    if output_pdf:
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_pdf, garbage=4, deflate=True)
        doc.close()
        print(f"  Saved: {output_pdf} ({pages_with_text} pages with text)")
    elif overwrite:
        # Save to temp file first, then overwrite original
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        doc.save(tmp_path, garbage=4, deflate=True)
        doc.close()
        tmp_path.replace(input_pdf)
        print(f"  Overwritten: {input_pdf} ({pages_with_text} pages with text)")
    else:
        doc.close()
        print(f"  Dry run: would process {pages_with_text} pages with text")


def process_pdf(
    pdf_path: Path,
    results_dir: Path,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> bool:
    """Process a single PDF. Returns True if successful."""
    pdf_name = pdf_path.stem

    results_json, key = find_results_json(pdf_name, results_dir)
    if not results_json:
        print(f"SKIP (no results.json): {pdf_path.name}")
        return False

    ocr_data = load_ocr_data(results_json, key)
    if not ocr_data:
        print(f"SKIP (no OCR data for '{pdf_name}'): {pdf_path.name}")
        return False

    try:
        output_pdf = (output_dir / pdf_path.name) if output_dir else None
        apply_ocr_to_pdf(pdf_path, ocr_data, output_pdf, overwrite)
        return True
    except Exception as e:
        print(f"ERROR: {pdf_path.name} - {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Apply OCR results to PDFs")
    parser.add_argument("input", help="PDF file or directory")
    parser.add_argument(
        "--results-dir", "-r",
        default="./output",
        help="Directory containing OCR results (default: ./output)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        help="Output directory for searchable PDFs (default: overwrite original)"
    )
    parser.add_argument(
        "--overwrite", "-w",
        action="store_true",
        help="Overwrite original PDFs (only when --output-dir not specified)"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be done without making changes"
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    results_dir = Path(args.results_dir)
    output_dir = Path(args.output_dir) if args.output_dir else None

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}")
        sys.exit(1)

    if not results_dir.exists():
        print(f"Error: Results directory not found: {results_dir}")
        sys.exit(1)

    # Collect PDFs
    if input_path.is_file():
        pdf_paths = [input_path]
    else:
        pdf_paths = sorted(input_path.glob("**/*.pdf"))
        if not pdf_paths:
            print(f"No PDF files found in: {input_path}")
            sys.exit(1)

    print(f"Processing {len(pdf_paths)} PDFs...")
    print(f"Results dir: {results_dir}")
    if output_dir:
        print(f"Output dir: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
    elif args.overwrite:
        print("Mode: overwrite original")
    else:
        print("Mode: dry run (use --output-dir or --overwrite)")

    success = 0
    failed = 0

    for i, pdf_path in enumerate(pdf_paths, 1):
        print(f"\n[{i}/{len(pdf_paths)}] {pdf_path.name}")

        if args.dry_run:
            # Just check if OCR data exists
            results_json, key = find_results_json(pdf_path.stem, results_dir)
            if results_json:
                ocr_data = load_ocr_data(results_json, key)
                if ocr_data:
                    print(f"  Would process: {len(ocr_data)} pages")
                    success += 1
                else:
                    print(f"  SKIP: no OCR data")
                    failed += 1
            else:
                print(f"  SKIP: no results.json")
                failed += 1
        else:
            if process_pdf(pdf_path, results_dir, output_dir, args.overwrite):
                success += 1
            else:
                failed += 1

    print(f"\n{'='*40}")
    print(f"Completed: {success} success, {failed} skipped/failed")
    if output_dir:
        print(f"Output: {output_dir}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
