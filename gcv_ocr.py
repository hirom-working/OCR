#!/usr/bin/env python3
"""Google Cloud Vision OCR Pipeline for Japanese vertical text.

Usage:
    python gcv_ocr.py <pdf_path>           # Process single PDF
    python gcv_ocr.py --batch <directory>  # Process all PDFs in directory
    python gcv_ocr.py --batch <directory> --pattern "*銀河英雄伝説*"  # Filter by pattern
"""
import argparse
import base64
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import fitz  # PyMuPDF

# Configuration
GCLOUD_PATH = "/opt/homebrew/share/google-cloud-sdk/bin/gcloud"
OUTPUT_DIR = Path.home() / "Projects/OCR/gcv_output"
MAX_WORKERS = 4  # Parallel API calls per PDF


def log(msg: str):
    """Print timestamped log message."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def ocr_image(image_path: Path) -> dict:
    """Call Google Cloud Vision API for a single image."""
    cmd = [
        GCLOUD_PATH, "ml", "vision", "detect-text",
        str(image_path),
        "--language-hints=ja",
        "--format=json"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise Exception(f"Vision API error: {result.stderr}")

    data = json.loads(result.stdout)
    if "responses" in data and data["responses"]:
        resp = data["responses"][0]
        if "fullTextAnnotation" in resp:
            return resp["fullTextAnnotation"]
        elif "textAnnotations" in resp and resp["textAnnotations"]:
            return {"text": resp["textAnnotations"][0].get("description", "")}
    return {"text": ""}


def process_page(args: tuple) -> tuple[int, dict]:
    """Process a single page (for parallel execution)."""
    page_num, image_path = args
    try:
        result = ocr_image(image_path)
        return (page_num, result)
    except Exception as e:
        return (page_num, {"text": "", "error": str(e)})


def apply_ocr_to_pdf(pdf_path: Path, ocr_results: list[dict], output_path: Path):
    """Apply OCR text layer to PDF."""
    doc = fitz.open(pdf_path)

    for page_num, ocr_data in enumerate(ocr_results):
        if page_num >= len(doc):
            break

        page = doc[page_num]
        text = ocr_data.get("text", "")

        if not text:
            continue

        # Get page dimensions
        page_rect = page.rect

        # Add invisible text layer at the top of the page
        # This makes the PDF searchable while preserving the original image
        try:
            # Insert text as invisible (render_mode=3)
            fontsize = 10
            text_point = fitz.Point(page_rect.x0 + 10, page_rect.y0 + 20)

            # Split text into lines and insert
            lines = text.split('\n')
            y_offset = 20
            for line in lines:
                if line.strip():
                    try:
                        page.insert_text(
                            fitz.Point(page_rect.x0 + 10, page_rect.y0 + y_offset),
                            line,
                            fontname="japan",
                            fontsize=fontsize,
                            color=(1, 1, 1),  # White (invisible on white background)
                            render_mode=3,  # Invisible
                        )
                    except Exception:
                        pass
                y_offset += fontsize + 2
                if y_offset > page_rect.height - 20:
                    break
        except Exception as e:
            log(f"  Warning: Could not add text to page {page_num + 1}: {e}")

    doc.save(output_path, garbage=4, deflate=True)
    doc.close()


def process_pdf(pdf_path: Path, output_dir: Path = None) -> tuple[bool, str]:
    """Process a single PDF through Google Cloud Vision OCR."""
    if output_dir is None:
        output_dir = OUTPUT_DIR

    output_dir.mkdir(parents=True, exist_ok=True)

    batch_name = pdf_path.stem
    log(f"Processing: {pdf_path.name}")

    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        log(f"  Pages: {total_pages}")

        # Create temp directory for images
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Extract all pages as images
            log("  Extracting pages...")
            image_paths = []
            for page_num in range(total_pages):
                page = doc[page_num]
                pix = page.get_pixmap(dpi=200)  # 200 DPI is good balance
                image_path = tmpdir / f"page_{page_num:04d}.png"
                pix.save(str(image_path))
                image_paths.append((page_num, image_path))

            doc.close()

            # Process pages with Vision API (parallel)
            log("  Running OCR...")
            ocr_results = [None] * total_pages

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(process_page, args): args[0] for args in image_paths}
                completed = 0
                for future in as_completed(futures):
                    page_num, result = future.result()
                    ocr_results[page_num] = result
                    completed += 1
                    if completed % 50 == 0:
                        log(f"    Progress: {completed}/{total_pages}")

            # Save OCR results as JSON
            json_path = output_dir / f"{batch_name}_ocr.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(ocr_results, f, ensure_ascii=False, indent=2)

            # Save plain text
            text_path = output_dir / f"{batch_name}.txt"
            with open(text_path, "w", encoding="utf-8") as f:
                for i, result in enumerate(ocr_results):
                    text = result.get("text", "") if result else ""
                    f.write(f"=== Page {i + 1} ===\n")
                    f.write(text)
                    f.write("\n\n")

            # Apply OCR to PDF
            log("  Creating searchable PDF...")
            output_pdf = output_dir / f"{batch_name}.pdf"
            apply_ocr_to_pdf(pdf_path, ocr_results, output_pdf)

            # Count successful pages
            success_pages = sum(1 for r in ocr_results if r and r.get("text"))
            log(f"  Done! {success_pages}/{total_pages} pages with text")

            return (True, f"OK: {success_pages}/{total_pages} pages")

    except Exception as e:
        log(f"  ERROR: {e}")
        return (False, str(e))


def main():
    parser = argparse.ArgumentParser(description="Google Cloud Vision OCR Pipeline")
    parser.add_argument("path", nargs="?", help="PDF file or directory path")
    parser.add_argument("--batch", action="store_true", help="Process all PDFs in directory")
    parser.add_argument("--pattern", default="*.pdf", help="File pattern for batch mode")
    parser.add_argument("--output", type=Path, help="Output directory")

    args = parser.parse_args()

    if not args.path:
        parser.print_help()
        sys.exit(1)

    path = Path(args.path).resolve()
    output_dir = args.output or OUTPUT_DIR

    if args.batch:
        if not path.is_dir():
            log(f"Error: {path} is not a directory")
            sys.exit(1)

        pdfs = sorted(path.glob(args.pattern))
        log(f"Found {len(pdfs)} PDF files")

        results = []
        for i, pdf in enumerate(pdfs, 1):
            log(f"\n[{i}/{len(pdfs)}] {pdf.name}")
            success, msg = process_pdf(pdf, output_dir)
            results.append((pdf, success, msg))

        # Summary
        log("\n" + "=" * 50)
        success_count = sum(1 for _, s, _ in results if s)
        log(f"Completed: {success_count}/{len(pdfs)}")

        failed = [(p, m) for p, s, m in results if not s]
        if failed:
            log("\nFailed files:")
            for pdf, msg in failed:
                log(f"  - {pdf.name}: {msg}")
    else:
        if not path.is_file():
            log(f"Error: {path} is not a file")
            sys.exit(1)

        success, msg = process_pdf(path, output_dir)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
