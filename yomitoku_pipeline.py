#!/usr/bin/env python3
"""Yomitoku OCR Pipeline - Create searchable PDFs.

Replaces existing text layer with Yomitoku OCR results for better Japanese text search.

Usage:
    python yomitoku_pipeline.py <input_pdf> [-o <output_pdf>] [--dpi 200]
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz  # PyMuPDF


def normalize_japanese_text(text: str) -> str:
    """Remove unnecessary spaces between Japanese characters."""
    # Remove spaces between Japanese characters (hiragana, katakana, kanji)
    jp_char = r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBF]'
    pattern = f'({jp_char})\\s+({jp_char})'

    prev = None
    while prev != text:
        prev = text
        text = re.sub(pattern, r'\1\2', text)

    return text


def run_yomitoku(
    pdf_path: Path,
    remote_host: str = "pgx02",
    venv_path: str = "~/Projects/.venv",
    dpi: int = 200,
) -> dict:
    """Run Yomitoku OCR on remote GPU server."""
    remote_input = f"/tmp/yomitoku_in_{pdf_path.stem}.pdf"
    remote_output = f"/tmp/yomitoku_out_{pdf_path.stem}"

    # Upload
    subprocess.run(
        ["scp", str(pdf_path), f"{remote_host}:{remote_input}"],
        check=True, capture_output=True,
    )

    # Run OCR
    cmd = (
        f"source {venv_path}/bin/activate && "
        f"rm -rf {remote_output} && "
        f"yomitoku {remote_input} -f json -o {remote_output} "
        f"--reading_order auto --dpi {dpi}"
    )
    subprocess.run(["ssh", remote_host, cmd], capture_output=True, timeout=3600)

    # Download result
    with tempfile.TemporaryDirectory() as tmpdir:
        local_json = Path(tmpdir) / "result.json"
        subprocess.run(
            ["scp", f"{remote_host}:{remote_output}/*.json", str(local_json)],
            check=True, capture_output=True,
        )
        with open(local_json, encoding="utf-8") as f:
            result = json.load(f)

    # Cleanup
    subprocess.run(
        ["ssh", remote_host, f"rm -rf {remote_input} {remote_output}"],
        capture_output=True,
    )

    return result


def create_searchable_pdf(
    input_pdf: Path,
    ocr_result: dict,
    output_pdf: Path,
    render_dpi: int = 300,
):
    """Create searchable PDF by replacing text layer with OCR results."""
    doc = fitz.open(input_pdf)

    # Step 1: Render pages as images (removes existing text layer)
    new_doc = fitz.open()
    for page in doc:
        pix = page.get_pixmap(dpi=render_dpi)
        new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, pixmap=pix)
    doc.close()

    # Step 2: Add OCR text layer (invisible, for search only)
    jp_font = fitz.Font("japan")

    for page_num, page in enumerate(new_doc):
        # Get OCR data for this page
        if isinstance(ocr_result, list):
            if page_num >= len(ocr_result):
                continue
            ocr_data = ocr_result[page_num]
        else:
            ocr_data = ocr_result

        paragraphs = ocr_data.get("paragraphs", [])
        if not paragraphs:
            continue

        # Collect all text from paragraphs
        all_text = []
        for para in paragraphs:
            text = para.get("contents", "")
            if text:
                clean = text.replace("\n", "").strip()
                clean = normalize_japanese_text(clean)
                if clean:
                    all_text.append(clean)

        if not all_text:
            continue

        # Add each paragraph as a separate line to avoid clipping
        page_width = page.rect.width
        tw = fitz.TextWriter(page.rect)
        y_offset = 5

        for para_text in all_text:
            # Calculate fontsize to fit this paragraph in page width
            # Japanese chars are roughly square, so width ≈ fontsize
            fontsize = (page_width - 10) / (len(para_text) + 1)
            fontsize = min(fontsize, 3)
            fontsize = max(fontsize, 0.1)

            y_offset += fontsize
            try:
                tw.append(fitz.Point(5, y_offset), para_text, font=jp_font, fontsize=fontsize)
            except Exception:
                pass
            y_offset += 2  # Line spacing

        tw.write_text(page, color=(0, 0, 0), opacity=0)

    # Save
    new_doc.save(output_pdf, garbage=4, deflate=True)
    new_doc.close()


def process_pdf(
    input_pdf: Path,
    output_pdf: Path = None,
    dpi: int = 200,
    remote_host: str = "pgx02",
) -> Path:
    """Process PDF: OCR with Yomitoku and create searchable PDF."""
    input_pdf = Path(input_pdf)
    if output_pdf is None:
        output_pdf = input_pdf.parent / f"{input_pdf.stem}_ocr.pdf"
    else:
        output_pdf = Path(output_pdf)

    print(f"Processing: {input_pdf.name}")

    print("  Running Yomitoku OCR...")
    ocr_result = run_yomitoku(input_pdf, remote_host, dpi=dpi)

    print("  Creating searchable PDF...")
    create_searchable_pdf(input_pdf, ocr_result, output_pdf)

    print(f"  Done: {output_pdf}")
    return output_pdf


def main():
    parser = argparse.ArgumentParser(description="Create searchable PDF with Yomitoku OCR")
    parser.add_argument("input", type=Path, help="Input PDF file")
    parser.add_argument("-o", "--output", type=Path, help="Output PDF file")
    parser.add_argument("--dpi", type=int, default=200, help="DPI for OCR (default: 200)")
    parser.add_argument("--host", default="pgx02", help="Remote host for Yomitoku")

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    try:
        output = process_pdf(args.input, args.output, dpi=args.dpi, remote_host=args.host)
        print(f"Output: {output}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
