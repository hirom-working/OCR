#!/usr/bin/env python3
"""PDF OCR Pipeline - Process single PDF file.

Usage:
    python process_pdf.py <pdf_path>
    python process_pdf.py <pdf_path> --dry-run
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from functools import wraps
from pathlib import Path

import fitz  # PyMuPDF

from config import config

# Configuration (loaded from config.toml)
OUTPUT_DIR = config.local.output_dir
WORK_DIR = config.local.work_dir

# Remote servers
OCR_HOST = config.ocr_server.host
OLLAMA_HOST = config.ollama.host
OLLAMA_MODEL = config.ollama.model

REMOTE_SURYA_DIR = config.ocr_server.surya_dir
REMOTE_INPUT_DIR = config.ocr_server.input_dir
REMOTE_OUTPUT_DIR = config.ocr_server.output_dir


def log(msg: str):
    """Print timestamped log message."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def retry(max_attempts: int = 3, delay: int = 5):
    """Decorator for retry logic."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        log(f"  Retry {attempt + 1}/{max_attempts}: {e}")
                        time.sleep(delay)
            raise last_error
        return wrapper
    return decorator


def run_ssh(host: str, cmd: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run command on remote server via SSH."""
    full_cmd = f'ssh {host} "{cmd}"'
    return subprocess.run(full_cmd, shell=True, capture_output=True, text=True, check=check, timeout=timeout)


@retry(max_attempts=3, delay=5)
def upload_pdf(pdf_path: Path) -> str:
    """Upload PDF to OCR server. Returns remote path."""
    batch_name = pdf_path.stem
    remote_input = f"{REMOTE_INPUT_DIR}/{batch_name}"

    # Create remote directory (escape spaces for shell)
    escaped_path = remote_input.replace(" ", "\\ ")
    run_ssh(OCR_HOST, f"mkdir -p {escaped_path}")

    # Get expanded remote path and upload
    result = run_ssh(OCR_HOST, f"echo {escaped_path}")
    expanded_path = result.stdout.strip()

    cmd = f'scp "{pdf_path}" "{OCR_HOST}:{expanded_path}/"'
    subprocess.run(cmd, shell=True, capture_output=True, check=True, timeout=60)

    return remote_input


def run_ocr(remote_input: str, batch_name: str) -> str:
    """Run Surya OCR on remote server. Returns remote output path."""
    remote_output = f"{REMOTE_OUTPUT_DIR}/{batch_name}"

    # Escape spaces for shell
    escaped_input = remote_input.replace(" ", "\\ ")
    escaped_output = remote_output.replace(" ", "\\ ")

    ocr_cmd = (
        f"cd {REMOTE_SURYA_DIR} && "
        f"source venv/bin/activate && "
        f"surya_ocr {escaped_input} --output_dir {escaped_output}"
    )

    run_ssh(OCR_HOST, ocr_cmd, check=False, timeout=300)
    return remote_output


@retry(max_attempts=3, delay=5)
def download_results(remote_output: str, batch_name: str) -> Path:
    """Download OCR results. Returns local results path."""
    local_dir = WORK_DIR / batch_name
    local_dir.mkdir(parents=True, exist_ok=True)

    # Get expanded remote path (escape spaces)
    escaped_output = remote_output.replace(" ", "\\ ")
    result = run_ssh(OCR_HOST, f"echo {escaped_output}")
    expanded_path = result.stdout.strip()

    cmd = f'scp -r "{OCR_HOST}:{expanded_path}/"* "{local_dir}/"'
    subprocess.run(cmd, shell=True, capture_output=True, timeout=60)

    return local_dir


def find_results_json(results_dir: Path, pdf_name: str) -> Path | None:
    """Find results.json in results directory."""
    pdf_name_normalized = unicodedata.normalize("NFC", pdf_name)

    # Check root
    if (results_dir / "results.json").exists():
        return results_dir / "results.json"

    # Check nested folders
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


def load_ocr_data(results_json: Path, pdf_name: str) -> list | None:
    """Load OCR page data from results.json."""
    with open(results_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data

    if len(data) == 1:
        return list(data.values())[0]

    pdf_name_normalized = unicodedata.normalize("NFC", pdf_name)
    for key, value in data.items():
        key_normalized = unicodedata.normalize("NFC", key)
        if key_normalized == pdf_name_normalized or pdf_name_normalized.startswith(key_normalized):
            return value

    return None


def apply_ocr_to_pdf(pdf_path: Path, ocr_data: list) -> int:
    """Apply OCR text layer to PDF. Returns pages with text."""
    doc = fitz.open(pdf_path)
    pages_with_text = 0

    for page_num, page_data in enumerate(ocr_data):
        if page_num >= len(doc):
            break

        page = doc[page_num]
        all_text = []

        for line in page_data.get("text_lines", []):
            text = line.get("text", "").strip()
            if text:
                all_text.append(text)

        if not all_text:
            continue

        combined = " ".join(all_text)
        pages_with_text += 1

        page.insert_textbox(
            page.rect,
            combined,
            fontname="japan",
            fontsize=1,
            color=(1, 1, 1),
            render_mode=3,
        )

    # Save to temp then replace
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    doc.save(tmp_path, garbage=4, deflate=True)
    doc.close()
    tmp_path.replace(pdf_path)

    return pages_with_text


def extract_text_sample(pdf_path: Path, max_pages: int = 5, max_chars: int = 3000) -> str:
    """Extract text sample from PDF for title/author extraction."""
    doc = fitz.open(pdf_path)
    text_parts = []
    total_chars = 0

    for page_num in range(min(max_pages, len(doc))):
        page = doc[page_num]
        text = page.get_text().strip()
        if text:
            text_parts.append(f"--- Page {page_num + 1} ---\n{text}")
            total_chars += len(text)
            if total_chars >= max_chars:
                break

    doc.close()
    return "\n\n".join(text_parts)[:max_chars]


def extract_title_author(text_sample: str, original_filename: str) -> tuple[str | None, str | None]:
    """Use Ollama API to extract title and author from text."""
    prompt = f"""あなたは書籍・雑誌のタイトルを抽出する専門家です。
スキャンされたPDFの最初の数ページから、正確なタイトルを特定してください。

元のファイル名: {original_filename}

抽出ルール:
1. 雑誌の場合:
   - 雑誌名と発行年月を抽出（例: "ナイフマガジン 1997年8月号"）
   - 広告や記事タイトルではなく、雑誌自体の名前を探す
   - 「〇〇マガジン」「月刊〇〇」「週刊〇〇」などのパターンを優先

2. 書籍の場合:
   - 表紙や奥付から書名を抽出
   - 著者名は「著」「著者」「written by」の前後から抽出

3. 著者について:
   - 雑誌の場合は null
   - 書籍で著者が特定できない場合も null

JSONのみで回答（説明不要）:
{{"title": "正確なタイトル", "author": "著者名 または null"}}

テキスト:
{text_sample}"""

    # Use Ollama API via curl (write payload to temp file to avoid escaping issues)
    api_payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False
    })

    try:
        # Write payload to temp file locally, transfer to remote, execute, cleanup
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(api_payload)
            local_payload = Path(f.name)

        # Transfer and execute
        remote_payload = f"/tmp/ollama_payload_{int(time.time())}.json"
        subprocess.run(f'scp "{local_payload}" {OLLAMA_HOST}:{remote_payload}', shell=True, capture_output=True, timeout=30)
        local_payload.unlink()

        cmd = f'ssh {OLLAMA_HOST} "curl -s http://localhost:11434/api/generate -d @{remote_payload} && rm {remote_payload}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)
        response_data = json.loads(result.stdout)
        response_text = response_data.get("response", "")

        # Extract JSON from response
        json_match = re.search(r'\{[^}]+\}', response_text)
        if json_match:
            data = json.loads(json_match.group())
            title = data.get("title")
            author = data.get("author")
            if author in [None, "null", "NULL", "", "不明", "unknown", "N/A"]:
                author = None
            return title, author
    except Exception as e:
        log(f"  Ollama error: {e}")

    return None, None


def sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    # Remove/replace invalid characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:100]  # Limit length


def generate_filename(title: str | None, author: str | None, original_name: str) -> str:
    """Generate new filename from title and author."""
    if not title:
        return original_name

    title = sanitize_filename(title)

    if author:
        author = sanitize_filename(author)
        return f"[{author}] {title}.pdf"
    else:
        return f"{title}.pdf"


def cleanup(batch_name: str, local_dir: Path):
    """Clean up temporary files."""
    # Remote cleanup (escape spaces)
    escaped_name = batch_name.replace(" ", "\\ ")
    run_ssh(OCR_HOST, f"rm -rf {REMOTE_INPUT_DIR}/{escaped_name}", check=False)
    run_ssh(OCR_HOST, f"rm -rf {REMOTE_OUTPUT_DIR}/{escaped_name}", check=False)

    # Local cleanup
    if local_dir.exists():
        shutil.rmtree(local_dir)


def process_pdf(pdf_path: Path, dry_run: bool = False) -> bool:
    """Process a single PDF through the full pipeline."""
    batch_name = pdf_path.stem
    log(f"Processing: {pdf_path.name}")

    if dry_run:
        log("  [DRY RUN] Would upload to OCR server")
        log("  [DRY RUN] Would run OCR")
        log("  [DRY RUN] Would download results")
        log("  [DRY RUN] Would apply OCR text layer")
        log("  [DRY RUN] Would extract title/author")
        log("  [DRY RUN] Would move to output directory")
        return True

    try:
        # Step 1: Upload to OCR server
        log("  Uploading to OCR server...")
        remote_input = upload_pdf(pdf_path)

        # Step 2: Run OCR
        log("  Running OCR...")
        remote_output = run_ocr(remote_input, batch_name)

        # Step 3: Download results
        log("  Downloading results...")
        local_results = download_results(remote_output, batch_name)

        # Step 4: Apply OCR to PDF
        results_json = find_results_json(local_results, batch_name)
        if results_json:
            ocr_data = load_ocr_data(results_json, batch_name)
            if ocr_data:
                pages = apply_ocr_to_pdf(pdf_path, ocr_data)
                log(f"  OCR applied: {pages} pages")
            else:
                log("  Warning: No OCR data found")
        else:
            log("  Warning: No results.json found")

        # Step 5: Extract title/author using Ollama
        log("  Extracting title/author...")
        text_sample = extract_text_sample(pdf_path)
        title, author = extract_title_author(text_sample, pdf_path.name)
        log(f"  Title: {title}")
        log(f"  Author: {author}")

        # Step 6: Rename and move
        new_filename = generate_filename(title, author, pdf_path.name)
        dest_path = OUTPUT_DIR / new_filename

        # Handle duplicate filenames
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = OUTPUT_DIR / f"{stem} ({counter}){suffix}"
                counter += 1

        shutil.move(pdf_path, dest_path)
        log(f"  Moved to: {dest_path.name}")

        # Step 7: Cleanup
        cleanup(batch_name, local_results)
        log("  Done!")

        return True

    except Exception as e:
        log(f"  ERROR: {e}")
        cleanup(batch_name, WORK_DIR / batch_name)
        return False


def main():
    parser = argparse.ArgumentParser(description="Process PDF with OCR Pipeline")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without processing")

    args = parser.parse_args()

    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.exists():
        log(f"Error: File not found: {pdf_path}")
        sys.exit(1)

    if not pdf_path.suffix.lower() == ".pdf":
        log(f"Error: Not a PDF file: {pdf_path}")
        sys.exit(1)

    # Ensure directories exist
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    success = process_pdf(pdf_path, args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
