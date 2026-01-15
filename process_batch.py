#!/usr/bin/env python3
"""
Process batch OCR results where multiple PDFs are merged into one results.json.

Workflow:
1. Download batch results.json from remote server
2. Split OCR data by PDF based on page counts
3. Apply OCR text layer to original PDFs (overwrite)
4. Cleanup temporary files

Usage:
    python process_batch.py <pdf_directory> --remote-results <remote_path>

Example:
    python process_batch.py ~/装苑PDFs --remote-results pgx02:~/ocr_output/装苑/results.json
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz  # PyMuPDF


def download_file(remote_path: str, local_path: Path) -> bool:
    """Download file from remote server."""
    print(f"Downloading {remote_path}...")
    cmd = f'scp "{remote_path}" "{local_path}"'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
        return False
    print(f"  Downloaded to {local_path}")
    return True


def get_pdf_page_counts(pdf_dir: Path) -> dict[str, int]:
    """Get page count for each PDF in directory."""
    result = {}
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        doc = fitz.open(pdf)
        result[pdf.name] = len(doc)
        doc.close()
    return result


def split_batch_results(
    results_json: Path,
    pdf_page_counts: dict[str, int],
) -> dict[str, list]:
    """Split batch results.json by PDF based on page counts."""
    with open(results_json, "r", encoding="utf-8") as f:
        batch_data = json.load(f)

    # Group PDFs by year key
    pdf_groups: dict[str, list[str]] = {}
    for pdf_name in pdf_page_counts.keys():
        # Extract year key (e.g., "SO-EN 2010" from "SO-EN 2010.09.pdf")
        parts = pdf_name.replace(".pdf", "").rsplit(".", 1)
        if len(parts) == 2:
            year_key = parts[0]  # "SO-EN 2010"
        else:
            year_key = pdf_name.replace(".pdf", "")

        if year_key not in pdf_groups:
            pdf_groups[year_key] = []
        pdf_groups[year_key].append(pdf_name)

    # Sort PDFs within each group
    for key in pdf_groups:
        pdf_groups[key].sort()

    # Split pages for each PDF
    split_results: dict[str, list] = {}

    for year_key, pdfs in pdf_groups.items():
        if year_key not in batch_data:
            print(f"  WARNING: No OCR data for year key '{year_key}'")
            continue

        year_pages = batch_data[year_key]
        page_offset = 0

        for pdf_name in pdfs:
            page_count = pdf_page_counts[pdf_name]
            end_offset = page_offset + page_count

            if end_offset > len(year_pages):
                print(f"  WARNING: Not enough pages for {pdf_name} (need {page_count}, have {len(year_pages) - page_offset})")
                split_results[pdf_name] = year_pages[page_offset:]
            else:
                split_results[pdf_name] = year_pages[page_offset:end_offset]

            page_offset = end_offset

    return split_results


def apply_ocr_to_pdf(input_pdf: Path, ocr_data: list, output_pdf: Path):
    """Apply OCR text layer to PDF."""
    doc = fitz.open(input_pdf)

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

        # Add invisible textbox
        page.insert_textbox(
            page_rect,
            combined,
            fontname="japan",
            fontsize=1,
            color=(1, 1, 1),
            render_mode=3,
        )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()

    return pages_with_text


def cleanup_remote(remote_results: str):
    """Clean up remote results file."""
    if ":" in remote_results:
        host, path = remote_results.split(":", 1)
        # Delete the results directory (parent of results.json)
        result_dir = str(Path(path).parent)
        print(f"\nCleaning up remote files...")
        cmd = f'ssh {host} "rm -rf {result_dir}"'
        subprocess.run(cmd, shell=True, capture_output=True)
        print(f"  Removed: {host}:{result_dir}")


def main():
    parser = argparse.ArgumentParser(description="Process batch OCR results")
    parser.add_argument("pdf_dir", help="Directory containing original PDFs")
    parser.add_argument(
        "--remote-results", "-r",
        required=True,
        help="Remote path to results.json (e.g., pgx02:~/ocr_output/batch/results.json)"
    )
    parser.add_argument(
        "--local-results", "-l",
        help="Use local results.json instead of downloading"
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary/remote files after processing"
    )

    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)

    if not pdf_dir.exists():
        print(f"Error: PDF directory not found: {pdf_dir}")
        sys.exit(1)

    # Get PDF page counts
    print(f"Scanning PDFs in {pdf_dir}...")
    pdf_page_counts = get_pdf_page_counts(pdf_dir)
    print(f"  Found {len(pdf_page_counts)} PDFs, {sum(pdf_page_counts.values())} total pages")

    # Download or use local results
    if args.local_results:
        results_json = Path(args.local_results)
    else:
        results_json = Path(tempfile.gettempdir()) / "batch_results.json"
        if not download_file(args.remote_results, results_json):
            sys.exit(1)

    # Split batch results
    print(f"\nSplitting batch results...")
    split_results = split_batch_results(results_json, pdf_page_counts)
    print(f"  Split into {len(split_results)} PDF entries")

    # Apply OCR to each PDF
    print(f"\nApplying OCR to PDFs...")
    print(f"  Mode: overwrite originals")

    success = 0
    failed = 0

    for i, (pdf_name, ocr_data) in enumerate(sorted(split_results.items()), 1):
        pdf_path = pdf_dir / pdf_name
        if not pdf_path.exists():
            print(f"[{i}/{len(split_results)}] SKIP (not found): {pdf_name}")
            failed += 1
            continue

        try:
            # Write to temp file, then replace original
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            pages_with_text = apply_ocr_to_pdf(pdf_path, ocr_data, tmp_path)
            tmp_path.replace(pdf_path)
            print(f"[{i}/{len(split_results)}] OK: {pdf_name} ({pages_with_text} pages)")
            success += 1
        except Exception as e:
            print(f"[{i}/{len(split_results)}] ERROR: {pdf_name} - {e}")
            failed += 1

    # Cleanup
    if not args.keep_temp:
        cleanup_remote(args.remote_results)
        # Clean up downloaded results.json
        if not args.local_results and results_json.exists():
            results_json.unlink()
            print(f"  Removed: {results_json}")

    print(f"\n{'='*50}")
    print(f"Complete: {success} success, {failed} failed")


if __name__ == "__main__":
    main()
