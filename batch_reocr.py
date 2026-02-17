#!/usr/bin/env python3
"""Batch Re-OCR Script - Re-process all PDFs using dual OCR servers.

Usage:
    python batch_reocr.py                    # List files only (dry run)
    python batch_reocr.py --run              # Process with both servers
    python batch_reocr.py --run --server pgx01  # Use specific server only
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue
from threading import Lock

import fitz  # PyMuPDF

# Configuration
SOURCE_DIR = Path.home() / "電子図書"
SCRIPT_DIR = Path(__file__).parent
LOG_FILE = SCRIPT_DIR / "batch_reocr.log"

# OCR Servers
OCR_SERVERS = {
    "pgx01": {
        "host": "pgx01",
        "surya_dir": "~/surya-ocr",
        "input_dir": "~/ocr_watch_input",
        "output_dir": "~/ocr_watch_output",
    },
    "pgx02": {
        "host": "pgx02",
        "surya_dir": "~/surya-ocr",
        "input_dir": "~/ocr_watch_input",
        "output_dir": "~/ocr_watch_output",
    },
}

# Thread-safe logging
log_lock = Lock()


def log(msg: str):
    """Print and log message (thread-safe)."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    with log_lock:
        print(line, flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def run_ssh(host: str, cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run command on remote server via SSH."""
    full_cmd = f'ssh {host} "{cmd}"'
    return subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def find_all_pdfs(source_dir: Path) -> list[Path]:
    """Find all PDF files recursively."""
    pdfs = list(source_dir.rglob("*.pdf")) + list(source_dir.rglob("*.PDF"))
    # Sort by size (smallest first for faster initial progress)
    pdfs.sort(key=lambda p: p.stat().st_size)
    return pdfs


def upload_pdf(pdf_path: Path, server: dict) -> str:
    """Upload PDF to OCR server. Returns remote path."""
    host = server["host"]
    batch_name = pdf_path.stem
    remote_input = f"{server['input_dir']}/{batch_name}"

    # Create remote directory
    escaped_path = remote_input.replace(" ", "\\ ")
    run_ssh(host, f"mkdir -p {escaped_path}")

    # Upload
    result = run_ssh(host, f"echo {escaped_path}")
    expanded_path = result.stdout.strip()
    cmd = f'scp "{pdf_path}" "{host}:{expanded_path}/"'
    subprocess.run(cmd, shell=True, capture_output=True, check=True, timeout=600)

    return remote_input


def run_ocr(remote_input: str, batch_name: str, server: dict) -> str:
    """Run Surya OCR on remote server. Returns remote output path."""
    host = server["host"]
    remote_output = f"{server['output_dir']}/{batch_name}"

    escaped_input = remote_input.replace(" ", "\\ ")
    escaped_output = remote_output.replace(" ", "\\ ")

    ocr_cmd = (
        f"cd {server['surya_dir']} && "
        f"source venv/bin/activate && "
        f"surya_ocr {escaped_input} --output_dir {escaped_output}"
    )

    run_ssh(host, ocr_cmd, timeout=3600)
    return remote_output


def download_results(remote_output: str, local_dir: Path, server: dict):
    """Download OCR results."""
    host = server["host"]
    escaped_output = remote_output.replace(" ", "\\ ")
    result = run_ssh(host, f"echo {escaped_output}")
    expanded_path = result.stdout.strip()

    cmd = f'scp -r "{host}:{expanded_path}/"* "{local_dir}/"'
    subprocess.run(cmd, shell=True, capture_output=True, timeout=300)


def find_results_json(results_dir: Path, pdf_name: str) -> Path | None:
    """Find results.json in results directory."""
    import unicodedata
    pdf_name_normalized = unicodedata.normalize("NFC", pdf_name)

    if (results_dir / "results.json").exists():
        return results_dir / "results.json"

    for folder in results_dir.iterdir():
        if not folder.is_dir():
            continue
        folder_normalized = unicodedata.normalize("NFC", folder.name)
        if folder_normalized == pdf_name_normalized:
            nested = folder / folder.name / "results.json"
            if nested.exists():
                return nested
            direct = folder / "results.json"
            if direct.exists():
                return direct

    return None


def apply_ocr_to_pdf(pdf_path: Path, ocr_data: list) -> int:
    """Apply OCR text layer to PDF using bbox coordinates."""
    doc = fitz.open(pdf_path)
    pages_with_text = 0

    for page_num, page_data in enumerate(ocr_data):
        if page_num >= len(doc):
            break

        page = doc[page_num]
        text_lines = page_data.get("text_lines", [])

        if not text_lines:
            continue

        # Remove existing text layer
        page.add_redact_annot(page.rect)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        # Get page dimensions for coordinate scaling
        page_rect = page.rect
        image_bbox = page_data.get("image_bbox", [0, 0, page_rect.width, page_rect.height])

        scale_x = page_rect.width / (image_bbox[2] - image_bbox[0]) if image_bbox[2] != image_bbox[0] else 1
        scale_y = page_rect.height / (image_bbox[3] - image_bbox[1]) if image_bbox[3] != image_bbox[1] else 1

        for line in text_lines:
            text = line.get("text", "").strip()
            if not text:
                continue

            bbox = line.get("bbox")
            if not bbox or len(bbox) < 4:
                continue

            x0 = bbox[0] * scale_x
            y0 = bbox[1] * scale_y
            x1 = bbox[2] * scale_x
            y1 = bbox[3] * scale_y

            text_rect = fitz.Rect(x0, y0, x1, y1)
            bbox_height = y1 - y0
            fontsize = max(1, min(bbox_height * 0.8, 12))

            try:
                page.insert_textbox(
                    text_rect, text,
                    fontname="japan", fontsize=fontsize,
                    color=(1, 1, 1), render_mode=3,
                    align=fitz.TEXT_ALIGN_LEFT,
                )
            except Exception:
                try:
                    page.insert_text(
                        (x0, y1), text,
                        fontname="japan", fontsize=fontsize,
                        color=(1, 1, 1), render_mode=3,
                    )
                except Exception:
                    pass

        pages_with_text += 1

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    doc.save(tmp_path, garbage=4, deflate=True)
    doc.close()
    tmp_path.replace(pdf_path)

    return pages_with_text


def cleanup_remote(batch_name: str, server: dict):
    """Clean up remote files."""
    host = server["host"]
    escaped_name = batch_name.replace(" ", "\\ ")
    run_ssh(host, f"rm -rf {server['input_dir']}/{escaped_name}", timeout=30)
    run_ssh(host, f"rm -rf {server['output_dir']}/{escaped_name}", timeout=30)


def process_single_pdf(pdf_path: Path, server_name: str, index: int, total: int) -> tuple[Path, bool, str]:
    """Process a single PDF using specified server."""
    server = OCR_SERVERS[server_name]
    batch_name = pdf_path.stem

    try:
        log(f"[{index}/{total}] [{server_name}] Processing: {pdf_path.name}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_pdf = Path(tmpdir) / pdf_path.name
            local_results = Path(tmpdir) / "results"
            local_results.mkdir()

            shutil.copy2(pdf_path, tmp_pdf)

            # Upload
            remote_input = upload_pdf(tmp_pdf, server)

            # OCR
            remote_output = run_ocr(remote_input, batch_name, server)

            # Download results
            download_results(remote_output, local_results, server)

            # Apply OCR
            results_json = find_results_json(local_results, batch_name)
            if results_json:
                with open(results_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ocr_data = data if isinstance(data, list) else list(data.values())[0] if len(data) == 1 else None

                if ocr_data:
                    pages = apply_ocr_to_pdf(tmp_pdf, ocr_data)
                    log(f"[{index}/{total}] [{server_name}] OCR applied: {pages} pages")

            # Copy back to original location
            shutil.copy2(tmp_pdf, pdf_path)

            # Cleanup remote
            cleanup_remote(batch_name, server)

            log(f"[{index}/{total}] [{server_name}] ✓ Success: {pdf_path.name}")
            return (pdf_path, True, "OK")

    except Exception as e:
        log(f"[{index}/{total}] [{server_name}] ✗ Failed: {pdf_path.name} - {e}")
        try:
            cleanup_remote(batch_name, server)
        except Exception:
            pass
        return (pdf_path, False, str(e))


def worker(task_queue: Queue, server_name: str, results: list, results_lock: Lock, total: int):
    """Worker thread that processes PDFs from the queue."""
    while True:
        try:
            index, pdf_path = task_queue.get_nowait()
        except Exception:
            break

        result = process_single_pdf(pdf_path, server_name, index, total)
        with results_lock:
            results.append(result)
        task_queue.task_done()


def main():
    parser = argparse.ArgumentParser(description="Batch Re-OCR all PDFs (dual server)")
    parser.add_argument("--run", action="store_true", help="Actually process")
    parser.add_argument("--server", choices=["pgx01", "pgx02", "both"], default="both", help="Which server(s) to use")
    parser.add_argument("--start", type=int, default=0, help="Start from this index")
    parser.add_argument("--limit", type=int, default=0, help="Process only this many files (0 = all)")

    args = parser.parse_args()

    # Find all PDFs
    pdfs = find_all_pdfs(SOURCE_DIR)
    total = len(pdfs)

    log(f"Found {total} PDF files in {SOURCE_DIR}")
    log(f"Total size: {sum(p.stat().st_size for p in pdfs) / 1024 / 1024 / 1024:.1f} GB")

    if not args.run:
        print("\n=== DRY RUN - Files that would be processed ===")
        for i, pdf in enumerate(pdfs):
            size_mb = pdf.stat().st_size / 1024 / 1024
            print(f"  {i+1:3d}. [{size_mb:6.1f} MB] {pdf.relative_to(SOURCE_DIR)}")
        print(f"\nTotal: {total} files")
        print(f"\nTo process, run with --run flag")
        print(f"  --server pgx01   : Use pgx01 only")
        print(f"  --server pgx02   : Use pgx02 only")
        print(f"  --server both    : Use both servers (default, 2x speed)")
        return

    # Apply start/limit
    if args.start > 0:
        pdfs = pdfs[args.start:]
        log(f"Starting from index {args.start}")
    if args.limit > 0:
        pdfs = pdfs[:args.limit]
        log(f"Limited to {args.limit} files")

    process_count = len(pdfs)

    # Determine servers to use
    if args.server == "both":
        servers = ["pgx01", "pgx02"]
    else:
        servers = [args.server]

    log(f"Processing {process_count} files using server(s): {', '.join(servers)}")

    # Process
    start_time = time.time()
    results = []
    results_lock = Lock()

    # Create task queue
    task_queue = Queue()
    for i, pdf in enumerate(pdfs, 1):
        task_queue.put((i, pdf))

    # Start worker threads (one per server)
    threads = []
    with ThreadPoolExecutor(max_workers=len(servers)) as executor:
        for server_name in servers:
            future = executor.submit(worker, task_queue, server_name, results, results_lock, process_count)
            threads.append(future)

        # Wait for all tasks to complete
        task_queue.join()

    # Summary
    success_count = sum(1 for _, success, _ in results if success)
    fail_count = sum(1 for _, success, _ in results if not success)
    failed_files = [(p, m) for p, success, m in results if not success]

    elapsed = time.time() - start_time
    log("=" * 50)
    log(f"COMPLETED in {elapsed/60:.1f} minutes")
    log(f"  Success: {success_count}/{process_count}")
    log(f"  Failed:  {fail_count}/{process_count}")

    if failed_files:
        log("\nFailed files:")
        for pdf, msg in failed_files:
            log(f"  - {pdf.name}: {msg}")


if __name__ == "__main__":
    main()
