#!/usr/bin/env python3
"""PDF OCR Pipeline - Process single PDF file.

Workflow:
1. Run Yomitoku OCR on remote GPU server (pgx02)
2. Create searchable PDF with invisible text layer
3. Use LLM (Gemma3 on pgx01) to extract title/author and classify category
4. Rename and move to appropriate category folder

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
from functools import wraps
from pathlib import Path

import fitz  # PyMuPDF

from config import config

# Configuration
OUTPUT_DIR = config.local.output_dir
WORK_DIR = config.local.work_dir
OCR_HOST = config.ocr_server.host
OCR_VENV = config.ocr_server.venv_path
LLM_CONFIG = config.llm
CATEGORIES = config.categories.folders


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


def normalize_japanese_text(text: str) -> str:
    """Remove unnecessary spaces between Japanese characters."""
    jp_char = r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBF]'
    pattern = f'({jp_char})\\s+({jp_char})'
    prev = None
    while prev != text:
        prev = text
        text = re.sub(pattern, r'\1\2', text)
    return text


@retry(max_attempts=3, delay=5)
def run_yomitoku_ocr(pdf_path: Path) -> list[dict]:
    """Run Yomitoku OCR on remote server and return JSON results per page."""
    remote_input = f"/tmp/yomitoku_in_{pdf_path.stem}.pdf"
    remote_output = f"/tmp/yomitoku_out_{pdf_path.stem}"

    # Upload PDF
    subprocess.run(
        ["scp", str(pdf_path), f"{OCR_HOST}:{remote_input}"],
        check=True, capture_output=True, timeout=600,
    )

    # Run Yomitoku
    cmd = (
        f"source {OCR_VENV}/bin/activate && "
        f"rm -rf {remote_output} && "
        f"yomitoku {remote_input} -f json -o {remote_output} "
        f"--reading_order auto --dpi 200"
    )
    subprocess.run(
        ["ssh", OCR_HOST, cmd],
        capture_output=True, timeout=3600,
    )

    # Download all JSON files (Yomitoku outputs one per page)
    with tempfile.TemporaryDirectory() as tmpdir:
        local_dir = Path(tmpdir) / "results"
        local_dir.mkdir()

        subprocess.run(
            ["scp", "-r", f"{OCR_HOST}:{remote_output}/", str(local_dir)],
            check=True, capture_output=True, timeout=600,
        )

        # Find and sort JSON files by page number
        json_files = list(local_dir.rglob("*.json"))

        def extract_page_num(f: Path) -> int:
            """Extract page number from filename like xxx_p123.json"""
            match = re.search(r'_p(\d+)\.json$', f.name)
            return int(match.group(1)) if match else 0

        json_files.sort(key=extract_page_num)

        # Load and merge results
        results = []
        for jf in json_files:
            with open(jf, encoding="utf-8") as f:
                results.append(json.load(f))

    # Cleanup remote
    subprocess.run(
        ["ssh", OCR_HOST, f"rm -rf {remote_input} {remote_output}"],
        capture_output=True,
    )

    return results


def create_searchable_pdf(input_pdf: Path, ocr_result: dict, output_pdf: Path):
    """Create searchable PDF by adding invisible OCR text layer."""
    doc = fitz.open(input_pdf)

    # Render pages as images (removes existing text layer)
    new_doc = fitz.open()
    for page in doc:
        pix = page.get_pixmap(dpi=300)
        new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, pixmap=pix)
    doc.close()

    # Add OCR text layer
    jp_font = fitz.Font("japan")

    for page_num, page in enumerate(new_doc):
        if isinstance(ocr_result, list):
            if page_num >= len(ocr_result):
                continue
            ocr_data = ocr_result[page_num]
        else:
            ocr_data = ocr_result

        paragraphs = ocr_data.get("paragraphs", [])
        if not paragraphs:
            continue

        # Collect and normalize text
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

        # Add text as invisible layer
        page_width = page.rect.width
        tw = fitz.TextWriter(page.rect)
        y_offset = 5

        for para_text in all_text:
            fontsize = (page_width - 10) / (len(para_text) + 1)
            fontsize = min(fontsize, 3)
            fontsize = max(fontsize, 0.1)

            y_offset += fontsize
            try:
                tw.append(fitz.Point(5, y_offset), para_text, font=jp_font, fontsize=fontsize)
            except Exception:
                pass
            y_offset += 2

        tw.write_text(page, color=(0, 0, 0), opacity=0)

    new_doc.save(output_pdf, garbage=4, deflate=True)
    new_doc.close()


def extract_text_sample(pdf_path: Path, max_pages: int = 5, max_chars: int = 3000) -> str:
    """Extract text sample from PDF for classification."""
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


def _extract_json_from_text(text: str) -> dict | None:
    """Extract JSON object from LLM response text, handling multiline."""
    # Try 1: Find first { and last } and parse
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Try 2: Single-line regex fallback
    json_match = re.search(r'\{[^}]+\}', text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    return None


def classify_document(text_sample: str, original_filename: str) -> tuple[str | None, str | None, str]:
    """Use LLM to extract title, author, and classify category."""
    categories_list = "\n".join(f"- {cat}" for cat in CATEGORIES)

    prompt = f"""あなたは書籍・雑誌の分類専門家です。
スキャンされたPDFから、タイトル、著者、カテゴリを特定してください。

元のファイル名: {original_filename}

## タイトル抽出ルール
1. 雑誌: 雑誌名と発行年月（例: "ナイフマガジン 1997年8月号"）
2. 書籍: 表紙や奥付から書名を抽出
3. 著者: 書籍の場合のみ。雑誌や不明な場合はnull

## カテゴリ選択（以下から1つ選択）
{categories_list}

必ず以下の形式の1行JSONのみで回答してください。他のテキストは含めないでください。
著者がない場合はnull（ダブルクォートなし）を使用してください。

回答例:
{{"title": "銀河英雄伝説 第01巻", "author": "田中芳樹", "category": "小説"}}
{{"title": "ナイフマガジン 1997年8月号", "author": null, "category": "趣味"}}

テキスト:
{text_sample}"""

    try:
        # Call vLLM API on remote server (OpenAI-compatible)
        api_payload = json.dumps({
            "model": LLM_CONFIG.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
        })

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(api_payload)
            local_payload = Path(f.name)

        remote_payload = f"/tmp/llm_payload_{int(time.time())}.json"
        subprocess.run(
            f'scp "{local_payload}" {LLM_CONFIG.host}:{remote_payload}',
            shell=True, capture_output=True, timeout=30
        )
        local_payload.unlink()

        endpoint = f"http://localhost:{LLM_CONFIG.port}/v1/chat/completions"
        cmd = f'ssh {LLM_CONFIG.host} "curl -s {endpoint} -H \'Content-Type: application/json\' -d @{remote_payload} && rm {remote_payload}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)

        response_data = json.loads(result.stdout)
        response_text = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        log(f"  LLM response: {response_text}")

        # Extract JSON from response
        data = _extract_json_from_text(response_text)
        if data:
            title = data.get("title")
            author = data.get("author")
            category = data.get("category", "その他")

            if author in [None, "null", "NULL", "", "不明", "unknown", "N/A"]:
                author = None

            # Validate category
            if category not in CATEGORIES:
                category = "その他"

            return title, author, category

        log(f"  JSON parse failed. Raw response: {response_text}")

    except Exception as e:
        log(f"  LLM error: {e}")

    return None, None, "その他"


def sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:100]


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


def process_pdf(pdf_path: Path, dry_run: bool = False) -> bool:
    """Process a single PDF through the full pipeline."""
    log(f"Processing: {pdf_path.name}")

    if dry_run:
        log("  [DRY RUN] Would run Yomitoku OCR")
        log("  [DRY RUN] Would create searchable PDF")
        log("  [DRY RUN] Would classify and rename")
        return True

    try:
        # Step 1: Run Yomitoku OCR
        log("  Running Yomitoku OCR...")
        ocr_result = run_yomitoku_ocr(pdf_path)

        # Step 2: Create searchable PDF
        log("  Creating searchable PDF...")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_pdf = Path(tmp.name)
        create_searchable_pdf(pdf_path, ocr_result, tmp_pdf)

        # Replace original with searchable version
        shutil.copy2(tmp_pdf, pdf_path)
        tmp_pdf.unlink()
        log("  OCR text layer applied")

        # Step 3: Classify document
        log("  Classifying document...")
        text_sample = extract_text_sample(pdf_path)
        title, author, category = classify_document(text_sample, pdf_path.name)
        log(f"  Title: {title}")
        log(f"  Author: {author}")
        log(f"  Category: {category}")

        # Step 4: Rename and move
        new_filename = generate_filename(title, author, pdf_path.name)

        # Build destination path: カテゴリ / 著者 / ファイル名
        if author:
            dest_dir = OUTPUT_DIR / category / sanitize_filename(author)
        else:
            dest_dir = OUTPUT_DIR / category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / new_filename

        # Handle duplicates
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = dest_dir / f"{stem} ({counter}){suffix}"
                counter += 1

        shutil.move(pdf_path, dest_path)
        log(f"  Moved to: {category}/{dest_path.name}")
        log("  Done!")

        return True

    except Exception as e:
        log(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="Process PDF with OCR Pipeline")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")

    args = parser.parse_args()

    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.exists():
        log(f"Error: File not found: {pdf_path}")
        sys.exit(1)

    if not pdf_path.suffix.lower() == ".pdf":
        log(f"Error: Not a PDF file: {pdf_path}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    success = process_pdf(pdf_path, args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
