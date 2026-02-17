#!/usr/bin/env python3
"""Apply OCR text layer to original PDFs with proper position mapping.

Usage:
    python apply_ocr_to_original.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import fitz  # PyMuPDF

# Configuration
OCR_OUTPUT_DIR = Path.home() / "Projects/OCR/gcv_output"
SOURCE_DIR = Path.home() / "電子図書/小説/田中芳樹"


def log(msg: str):
    """Print timestamped log message."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def get_bbox_rect(bbox: dict, page_width: float, page_height: float,
                  ocr_width: float, ocr_height: float) -> fitz.Rect:
    """Convert OCR bounding box to PDF rect with scaling."""
    vertices = bbox.get("vertices", [])
    if len(vertices) < 4:
        return None

    # Get coordinates (handle missing x/y)
    x0 = vertices[0].get("x", 0)
    y0 = vertices[0].get("y", 0)
    x1 = vertices[2].get("x", x0)
    y1 = vertices[2].get("y", y0)

    # Scale to PDF dimensions
    scale_x = page_width / ocr_width if ocr_width > 0 else 1
    scale_y = page_height / ocr_height if ocr_height > 0 else 1

    return fitz.Rect(x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y)


def apply_ocr_to_pdf(original_pdf: Path, ocr_json: Path) -> tuple[bool, int]:
    """Apply OCR text layer to original PDF with position mapping."""
    try:
        # Load OCR data
        with open(ocr_json, "r", encoding="utf-8") as f:
            ocr_results = json.load(f)

        doc = fitz.open(original_pdf)
        pages_with_text = 0

        for page_num, ocr_data in enumerate(ocr_results):
            if page_num >= len(doc):
                break

            if not ocr_data:
                continue

            page = doc[page_num]
            page_rect = page.rect

            # Get OCR image dimensions
            pages_data = ocr_data.get("pages", [])
            if not pages_data:
                # Fallback: just use full text at top
                text = ocr_data.get("text", "")
                if text:
                    try:
                        page.insert_text(
                            fitz.Point(10, 20), text[:500],
                            fontname="japan", fontsize=8,
                            color=(1, 1, 1), render_mode=3
                        )
                        pages_with_text += 1
                    except:
                        pass
                continue

            ocr_page = pages_data[0]
            ocr_width = ocr_page.get("width", page_rect.width)
            ocr_height = ocr_page.get("height", page_rect.height)

            text_inserted = False

            # Process each block -> paragraph -> word
            for block in ocr_page.get("blocks", []):
                if block.get("blockType") != "TEXT":
                    continue

                for para in block.get("paragraphs", []):
                    for word in para.get("words", []):
                        # Get word bounding box
                        word_bbox = word.get("boundingBox")
                        if not word_bbox:
                            continue

                        rect = get_bbox_rect(
                            word_bbox, page_rect.width, page_rect.height,
                            ocr_width, ocr_height
                        )
                        if not rect:
                            continue

                        # Build word text from symbols
                        word_text = ""
                        for symbol in word.get("symbols", []):
                            word_text += symbol.get("text", "")

                        if not word_text:
                            continue

                        # Calculate font size based on bbox height
                        bbox_height = rect.height
                        fontsize = max(4, min(bbox_height * 0.8, 14))

                        try:
                            # Insert text at word position (invisible)
                            page.insert_text(
                                fitz.Point(rect.x0, rect.y1),  # Bottom-left of bbox
                                word_text,
                                fontname="japan",
                                fontsize=fontsize,
                                color=(1, 1, 1),  # White
                                render_mode=3,  # Invisible
                            )
                            text_inserted = True
                        except Exception:
                            # Try textbox if insert_text fails
                            try:
                                page.insert_textbox(
                                    rect, word_text,
                                    fontname="japan", fontsize=fontsize,
                                    color=(1, 1, 1), render_mode=3,
                                    align=fitz.TEXT_ALIGN_LEFT
                                )
                                text_inserted = True
                            except:
                                pass

            if text_inserted:
                pages_with_text += 1

        # Save to temp file first, then replace original
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        doc.save(tmp_path, garbage=4, deflate=True)
        doc.close()

        # Replace original
        tmp_path.replace(original_pdf)

        return True, pages_with_text

    except Exception as e:
        log(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False, 0


def main():
    # Find all OCR JSON files
    ocr_files = sorted(OCR_OUTPUT_DIR.glob("*銀河英雄伝説*_ocr.json"))
    log(f"Found {len(ocr_files)} OCR data files")

    if not ocr_files:
        log("No OCR files found. Run gcv_ocr.py first.")
        return

    results = []

    for i, ocr_json in enumerate(ocr_files, 1):
        # Derive original PDF path
        stem = ocr_json.stem.replace("_ocr", "")
        original_pdf = SOURCE_DIR / f"{stem}.pdf"

        if not original_pdf.exists():
            log(f"[{i}/{len(ocr_files)}] SKIP: Original not found: {original_pdf.name}")
            continue

        log(f"[{i}/{len(ocr_files)}] Applying OCR to: {original_pdf.name}")

        original_size = original_pdf.stat().st_size / 1024 / 1024
        success, pages = apply_ocr_to_pdf(original_pdf, ocr_json)
        new_size = original_pdf.stat().st_size / 1024 / 1024

        if success:
            log(f"  Done! {pages} pages with text, {original_size:.1f}MB -> {new_size:.1f}MB")
            results.append((original_pdf, True, pages))
        else:
            results.append((original_pdf, False, 0))

    # Summary
    log("\n" + "=" * 50)
    success_count = sum(1 for _, s, _ in results if s)
    log(f"Completed: {success_count}/{len(results)}")

    failed = [(p, 0) for p, s, _ in results if not s]
    if failed:
        log("\nFailed files:")
        for pdf, _ in failed:
            log(f"  - {pdf.name}")


if __name__ == "__main__":
    main()
