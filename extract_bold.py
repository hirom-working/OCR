#!/usr/bin/env python3
"""Extract bold text from scanned PDF pages using Gemini 2.0 Flash."""
import logging
import os
import sys
import time

import fitz  # PyMuPDF
from google import genai
from google.genai import types

DPI = 150
MODEL = "gemini-2.0-flash"
PROMPT = (
    "このページに太字（ボールド体）のテキストがあれば、その部分だけを抽出してください。"
    "太字でないテキストは一切含めないでください。太字テキストがない場合は空行を返してください。"
)
MAX_RETRIES = 3
SLEEP_BETWEEN_REQUESTS = 0.5
OUTPUT_FILE = "extracted_bold_text.txt"

logging.basicConfig(
    filename="extract_bold.log",
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)


def extract_bold_from_page(client: genai.Client, img_bytes: bytes) -> str:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                    PROMPT,
                ],
            )
            return response.text.strip() if response.text else ""
        except Exception as e:
            logging.error(f"Attempt {attempt}/{MAX_RETRIES}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2)
    return ""


def main():
    pdf_paths = sys.argv[1:] or [
        os.path.expanduser("~/電子図書/小説/Andy Weir/[Andy Weir] プロジェクト・ヘイル・メアリー 上.pdf"),
        os.path.expanduser("~/電子図書/小説/Andy Weir/[Andy Weir] プロジェクト・ヘイル・メアリー 下.pdf"),
    ]

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    output_lines = []

    for pdf_path in pdf_paths:
        if not os.path.exists(pdf_path):
            print(f"Not found: {pdf_path}", file=sys.stderr)
            continue

        doc = fitz.open(pdf_path)
        total = len(doc)
        basename = os.path.basename(pdf_path)
        print(f"Processing: {basename} ({total} pages)")

        mat = fitz.Matrix(DPI / 72, DPI / 72)
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes("png")

            bold_text = extract_bold_from_page(client, img_bytes)
            if bold_text:
                output_lines.append(f"[{basename} p.{i}]")
                output_lines.append(bold_text)
                output_lines.append("")

            status = "  BOLD FOUND" if bold_text else ""
            print(f"  {i}/{total}{status}")
            time.sleep(SLEEP_BETWEEN_REQUESTS)

        doc.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
    print(f"\nSaved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
