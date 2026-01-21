#!/usr/bin/env python3
"""
OCR Pipeline - End-to-end PDF OCR processing

Workflow:
1. Upload PDFs to remote server (pgx02)
2. Run Surya OCR on remote GPU server
3. Download OCR results
4. Apply OCR text layer to original PDFs (overwrite)
5. Cleanup temporary files (local & remote)

Usage:
    python pipeline.py <pdf_path_or_directory>

Example:
    python pipeline.py "/path/to/books/"
    python pipeline.py "/path/to/book.pdf"
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF

from config import config

# Configuration (loaded from config.toml)
REMOTE_HOST = config.ocr_server.host
REMOTE_SURYA_DIR = config.ocr_server.surya_dir
REMOTE_INPUT_DIR = config.pipeline.input_dir
REMOTE_OUTPUT_DIR = config.pipeline.output_dir


def run_ssh(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run command on remote server via SSH."""
    full_cmd = f'ssh {REMOTE_HOST} "{cmd}"'
    return subprocess.run(full_cmd, shell=True, capture_output=True, text=True, check=check)


def cleanup_remote(batch_name: str):
    """Clean up remote input/output directories."""
    print(f"\nCleaning up remote files...")
    run_ssh(f"rm -rf {REMOTE_INPUT_DIR}/{batch_name}", check=False)
    run_ssh(f"rm -rf {REMOTE_OUTPUT_DIR}/{batch_name}", check=False)
    print("  Remote cleanup done")


def cleanup_local(output_dir: Path):
    """Clean up local temporary files."""
    print(f"Cleaning up local files...")
    if output_dir.exists():
        shutil.rmtree(output_dir)
        print(f"  Removed: {output_dir}")
    print("  Local cleanup done")


def run_local(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run local command."""
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def upload_pdfs(pdf_paths: list[Path], batch_name: str) -> str:
    """Upload PDFs to remote server. Returns remote input path."""
    remote_input = f"{REMOTE_INPUT_DIR}/{batch_name}"

    print(f"Uploading {len(pdf_paths)} PDFs to {REMOTE_HOST}:{remote_input}...")

    # Create remote directory
    run_ssh(f"mkdir -p {remote_input}")

    # Upload files
    for pdf_path in pdf_paths:
        cmd = f'scp "{pdf_path}" {REMOTE_HOST}:"{remote_input}/"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ERROR uploading {pdf_path.name}: {result.stderr}")
        else:
            print(f"  Uploaded: {pdf_path.name}")

    return remote_input


def run_ocr_remote(remote_input: str, batch_name: str) -> str:
    """Run Surya OCR on remote server. Returns remote output path."""
    remote_output = f"{REMOTE_OUTPUT_DIR}/{batch_name}"

    print(f"\nRunning Surya OCR on {REMOTE_HOST}...")
    print(f"  Input:  {remote_input}")
    print(f"  Output: {remote_output}")

    # Build OCR command
    ocr_cmd = (
        f"cd {REMOTE_SURYA_DIR} && "
        f"source venv/bin/activate && "
        f"surya_ocr {remote_input} --output_dir {remote_output}"
    )

    # Run OCR (this may take a long time)
    print("  Starting OCR (this may take a while)...")
    start_time = time.time()

    result = run_ssh(ocr_cmd, check=False)

    elapsed = time.time() - start_time
    print(f"  OCR completed in {elapsed:.1f} seconds")

    if result.returncode != 0:
        print(f"  OCR stderr: {result.stderr}")

    return remote_output


def download_results(remote_output: str, local_output: Path, batch_name: str) -> Path:
    """Download OCR results from remote server. Returns local results path."""
    local_results_dir = local_output / "ocr_results" / batch_name
    local_results_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nDownloading OCR results to {local_results_dir}...")

    # Download results
    cmd = f'scp -r {REMOTE_HOST}:"{remote_output}/*" "{local_results_dir}/"'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
    else:
        print("  Download complete")

    return local_results_dir


def find_results_json(pdf_name: str, results_dir: Path) -> Path | None:
    """Find results.json for given PDF name."""
    pdf_name_normalized = unicodedata.normalize("NFC", pdf_name)

    # Check for batch results.json (all PDFs in one file)
    batch_results = results_dir / "results.json"
    if batch_results.exists():
        return batch_results

    # Check for individual folder structure
    for folder in results_dir.iterdir():
        if not folder.is_dir():
            continue
        normalized = unicodedata.normalize("NFC", folder.name)
        if normalized == pdf_name_normalized:
            # Try nested structure
            nested = folder / folder.name / "results.json"
            if nested.exists():
                return nested
            # Try direct
            direct = folder / "results.json"
            if direct.exists():
                return direct

    return None


def load_ocr_data(results_json: Path, pdf_name: str) -> list | None:
    """Load OCR data for specific PDF from results.json."""
    with open(results_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Direct key match
    if pdf_name in data:
        return data[pdf_name]

    # Normalized match
    pdf_name_normalized = unicodedata.normalize("NFC", pdf_name)
    for key, value in data.items():
        if unicodedata.normalize("NFC", key) == pdf_name_normalized:
            return value

    # Partial match (for batch processing where key might be truncated)
    for key, value in data.items():
        key_normalized = unicodedata.normalize("NFC", key)
        if pdf_name_normalized.startswith(key_normalized) or key_normalized.startswith(pdf_name_normalized):
            return value

    # If only one key, use it
    if len(data) == 1:
        return list(data.values())[0]

    return None


def apply_ocr_to_pdf(input_pdf: Path, ocr_data: list, output_pdf: Path):
    """Apply OCR text layer to PDF."""
    doc = fitz.open(input_pdf)

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

        # Add invisible textbox covering the page
        page.insert_textbox(
            page_rect,
            combined,
            fontname="japan",
            fontsize=1,
            color=(1, 1, 1),
            render_mode=3,  # Invisible
        )

    # Save to output
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()


def process_batch(
    pdf_paths: list[Path],
    output_dir: Path,
    batch_name: str,
    skip_upload: bool = False,
    skip_ocr: bool = False,
    keep_temp: bool = False,
):
    """Process a batch of PDFs through the full pipeline."""

    print(f"\n{'='*60}")
    print(f"Processing batch: {batch_name}")
    print(f"PDFs: {len(pdf_paths)}")
    print(f"Mode: overwrite originals")
    print(f"{'='*60}\n")

    # Step 1: Upload
    if not skip_upload:
        remote_input = upload_pdfs(pdf_paths, batch_name)
    else:
        remote_input = f"{REMOTE_INPUT_DIR}/{batch_name}"
        print(f"Skipping upload, using existing: {remote_input}")

    # Step 2: Run OCR
    if not skip_ocr:
        remote_output = run_ocr_remote(remote_input, batch_name)
    else:
        remote_output = f"{REMOTE_OUTPUT_DIR}/{batch_name}"
        print(f"Skipping OCR, using existing: {remote_output}")

    # Step 3: Download results
    results_dir = download_results(remote_output, output_dir, batch_name)

    # Step 4: Apply OCR to PDFs
    print(f"\nApplying OCR to PDFs...")

    success = 0
    failed = 0

    for pdf_path in pdf_paths:
        pdf_name = pdf_path.stem
        results_json = find_results_json(pdf_name, results_dir)

        if not results_json:
            print(f"  SKIP (no results): {pdf_path.name}")
            failed += 1
            continue

        ocr_data = load_ocr_data(results_json, pdf_name)
        if not ocr_data:
            print(f"  SKIP (no OCR data): {pdf_path.name}")
            failed += 1
            continue

        try:
            # Write to temp, then replace original
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            apply_ocr_to_pdf(pdf_path, ocr_data, tmp_path)
            tmp_path.replace(pdf_path)
            print(f"  OK: {pdf_path.name}")
            success += 1
        except Exception as e:
            print(f"  ERROR: {pdf_path.name} - {e}")
            failed += 1

    # Step 5: Cleanup
    if not keep_temp:
        cleanup_remote(batch_name)
        cleanup_local(output_dir)

    # Summary
    print(f"\n{'='*60}")
    print(f"Complete: {success} success, {failed} failed")
    print(f"{'='*60}\n")

    return success, failed


def main():
    parser = argparse.ArgumentParser(description="OCR Pipeline")
    parser.add_argument("input", help="PDF file or directory")
    parser.add_argument("--output-dir", "-o", default="./output", help="Working directory for OCR results")
    parser.add_argument("--batch-name", "-b", help="Batch name (default: auto)")
    parser.add_argument("--skip-upload", action="store_true", help="Skip upload step")
    parser.add_argument("--skip-ocr", action="store_true", help="Skip OCR step")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary files after processing")

    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}")
        sys.exit(1)

    # Collect PDFs
    if input_path.is_file():
        pdf_paths = [input_path]
        batch_name = args.batch_name or input_path.stem
    else:
        pdf_paths = sorted(input_path.glob("**/*.pdf"))
        if not pdf_paths:
            print(f"No PDF files found in: {input_path}")
            sys.exit(1)
        batch_name = args.batch_name or input_path.name

    # Process
    success, failed = process_batch(
        pdf_paths,
        output_dir,
        batch_name,
        skip_upload=args.skip_upload,
        skip_ocr=args.skip_ocr,
        keep_temp=args.keep_temp,
    )

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
