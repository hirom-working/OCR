#!/usr/bin/env python3
"""
Scansnap Folder Watcher - Automated PDF OCR Pipeline

Workflow:
1. Watch ~/Scansnap folder for new PDFs
2. OCR process on pgx02 (GPU)
3. Extract title/author using Ollama on pgx01
4. Rename file: "[Author] Title.pdf" or "Title.pdf"
5. Move to ~/電子図書/

Usage:
    python watch_scansnap.py          # Start daemon
    python watch_scansnap.py --once   # Process existing files and exit
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
from pathlib import Path

import fitz  # PyMuPDF
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Configuration
SCANSNAP_DIR = Path.home() / "Scansnap"
OUTPUT_DIR = Path.home() / "電子図書"
WORK_DIR = Path("/Users/hirom/Projects/OCR/work")

# Remote servers
OCR_HOST = "pgx02"
OLLAMA_HOST = "pgx01"
OLLAMA_MODEL = "gemma3:27b"

REMOTE_SURYA_DIR = "~/surya-ocr"
REMOTE_INPUT_DIR = "~/ocr_watch_input"
REMOTE_OUTPUT_DIR = "~/ocr_watch_output"


def log(msg: str):
    """Print timestamped log message."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def run_ssh(host: str, cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run command on remote server via SSH."""
    full_cmd = f'ssh {host} "{cmd}"'
    return subprocess.run(full_cmd, shell=True, capture_output=True, text=True, check=check)


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
    subprocess.run(cmd, shell=True, capture_output=True, check=True)

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

    run_ssh(OCR_HOST, ocr_cmd, check=False)
    return remote_output


def download_results(remote_output: str, batch_name: str) -> Path:
    """Download OCR results. Returns local results path."""
    local_dir = WORK_DIR / batch_name
    local_dir.mkdir(parents=True, exist_ok=True)

    # Get expanded remote path (escape spaces)
    escaped_output = remote_output.replace(" ", "\\ ")
    result = run_ssh(OCR_HOST, f"echo {escaped_output}")
    expanded_path = result.stdout.strip()

    cmd = f'scp -r "{OCR_HOST}:{expanded_path}/"* "{local_dir}/"'
    subprocess.run(cmd, shell=True, capture_output=True)

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
        subprocess.run(f'scp "{local_payload}" {OLLAMA_HOST}:{remote_payload}', shell=True, capture_output=True)
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


def process_pdf(pdf_path: Path) -> bool:
    """Process a single PDF through the full pipeline."""
    batch_name = pdf_path.stem
    log(f"Processing: {pdf_path.name}")

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


class ScanSnapHandler(FileSystemEventHandler):
    """Handler for new PDF files in Scansnap folder."""

    def __init__(self):
        self.processing = set()

    def on_created(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path)
        if path.suffix.lower() != ".pdf":
            return

        if path.name in self.processing:
            return

        # Wait for file to be fully written
        self.processing.add(path.name)
        time.sleep(2)

        # Check file is stable (not being written)
        prev_size = -1
        while True:
            try:
                curr_size = path.stat().st_size
                if curr_size == prev_size:
                    break
                prev_size = curr_size
                time.sleep(1)
            except FileNotFoundError:
                self.processing.discard(path.name)
                return

        process_pdf(path)
        self.processing.discard(path.name)


def process_existing():
    """Process all existing PDFs in Scansnap folder."""
    pdfs = list(SCANSNAP_DIR.glob("*.pdf"))
    if not pdfs:
        log("No PDFs found in Scansnap folder")
        return

    log(f"Found {len(pdfs)} PDFs to process")
    for pdf_path in pdfs:
        process_pdf(pdf_path)


def main():
    parser = argparse.ArgumentParser(description="Scansnap Folder Watcher")
    parser.add_argument("--once", action="store_true", help="Process existing files and exit")

    args = parser.parse_args()

    # Ensure directories exist
    SCANSNAP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    if args.once:
        process_existing()
        return

    # Process existing files first
    process_existing()

    # Start watching
    log(f"Watching: {SCANSNAP_DIR}")
    log(f"Output: {OUTPUT_DIR}")
    log("Press Ctrl+C to stop")

    event_handler = ScanSnapHandler()
    observer = Observer()
    observer.schedule(event_handler, str(SCANSNAP_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        log("Stopped")

    observer.join()


if __name__ == "__main__":
    main()
