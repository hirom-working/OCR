#!/usr/bin/env python3
"""Extract Rocky's dialogue from Project Hail Mary using GPT-OSS-120B."""
import os
import sys
import time

import fitz  # PyMuPDF
import httpx

LLM_URL = "http://192.168.150.51:8080/v1/chat/completions"
MODEL = "GPT-OSS-120B"
OUTPUT_FILE = "extracted_rocky_dialogue.txt"

SYSTEM_PROMPT = """あなたは小説「プロジェクト・ヘイル・メアリー」（アンディ・ウィアー著）の分析者です。

テキストからエイリアンのキャラクター「ロッキー」の台詞だけを抽出してください。

ロッキーの特徴:
- エリディアン（異星人）で、音楽的な和音で会話する（♪♫等で表現される）
- 片言で話す（翻訳された形で）
- 短い文で話すことが多い
- 主人公「ぼく」（グレース）との会話相手
- 「彼がいう」「彼がいった」「ロッキーがいう」等の地の文で発話者が示されることが多い

注意:
- 主人公グレースも一人称「ぼく」を使うので混同しないこと
- ロッキーの台詞でない部分は一切含めないこと
- ロッキーの台詞がない場合は「（なし）」とだけ返すこと
- 台詞は「」で囲まれた形でそのまま出力すること"""


def extract_text_from_pdf(pdf_path: str) -> list[tuple[int, str]]:
    """PDFから各ページのテキストを抽出。(ページ番号, テキスト)のリスト。"""
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc, 1):
        text = page.get_text().strip()
        if text and len(text) > 20:
            pages.append((i, text))
    doc.close()
    return pages


def extract_rocky_lines(text: str) -> str:
    """GPT-OSSにテキストを送ってロッキーの台詞を抽出。"""
    for attempt in range(3):
        try:
            response = httpx.post(
                LLM_URL,
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "max_tokens": 2000,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"    Retry {attempt + 1}/3: {e}", flush=True)
            time.sleep(3)
    return "(error)"


def main():
    pdf_paths = sys.argv[1:] or [
        os.path.expanduser("~/電子図書/小説/Andy Weir/[Andy Weir] プロジェクト・ヘイル・メアリー 上.pdf"),
        os.path.expanduser("~/電子図書/小説/Andy Weir/[Andy Weir] プロジェクト・ヘイル・メアリー 下.pdf"),
    ]

    output_lines = []

    for pdf_path in pdf_paths:
        if not os.path.exists(pdf_path):
            print(f"Not found: {pdf_path}", file=sys.stderr, flush=True)
            continue

        basename = os.path.basename(pdf_path)
        pages = extract_text_from_pdf(pdf_path)
        print(f"Processing: {basename} ({len(pages)} pages with text)", flush=True)

        for i, (page_num, text) in enumerate(pages, 1):
            result = extract_rocky_lines(text)

            if result and result != "（なし）":
                output_lines.append(f"[{basename} p.{page_num}]")
                output_lines.append(result)
                output_lines.append("")
                status = "  ROCKY FOUND"
            else:
                status = ""

            print(f"  {i}/{len(pages)} (p.{page_num}){status}", flush=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
    print(f"\nSaved to: {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
