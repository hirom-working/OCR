#!/usr/bin/env python3
"""Extract Rocky's dialogue chapter by chapter using GPT-OSS-120B."""
import re
import time

import httpx

LLM_URL = "http://192.168.150.51:8080/v1/chat/completions"
MODEL = "GPT-OSS-120B"
OUTPUT_FILE = "rocky_dialogue_by_chapter.txt"

SYSTEM_PROMPT = """あなたは小説「プロジェクト・ヘイル・メアリー」（アンディ・ウィアー著）の分析者です。

以下のテキスト（1章分）からエイリアンのキャラクター「ロッキー」の台詞だけを抽出してください。

ロッキーの特徴:
- エリディアン（異星人）で、音楽的な和音で会話する（♪♫等で表現される）
- 片言で話す（翻訳された形で）
- 短い文で話すことが多い
- 主人公「ぼく」（グレース）との会話相手
- 「彼がいう」「彼がいった」「ロッキーがいう」「ロッキーがいった」等の地の文で発話者が示される

ルール:
- ロッキーの台詞のみを出力。地の文や主人公グレースの台詞は含めない
- 主人公グレースも一人称「ぼく」を使うので混同しないこと
- 台詞は「」で囲まれた形でそのまま出力
- ロッキーの台詞がない場合は「（なし）」とだけ返す"""


def split_into_chapters(text: str) -> list[tuple[str, str]]:
    """テキストを章ごとに分割。(章タイトル, 本文)のリスト。"""
    pattern = re.compile(r'^(第.{1,3}章)', re.MULTILINE)
    matches = list(pattern.finditer(text))

    chapters = []
    for i, m in enumerate(matches):
        title = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        chapters.append((title, body))

    return chapters


def extract_rocky_lines(chapter_text: str) -> str:
    for attempt in range(3):
        try:
            response = httpx.post(
                LLM_URL,
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": chapter_text},
                    ],
                    "max_tokens": 4000,
                },
                timeout=300.0,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"    Retry {attempt + 1}/3: {e}", flush=True)
            time.sleep(5)
    return "(error)"


def main():
    with open("hail_mary_full.txt", encoding="utf-8") as f:
        full_text = f.read()

    chapters = split_into_chapters(full_text)
    print(f"Total chapters: {len(chapters)}", flush=True)

    # ロッキー登場章（第10章以降）だけ処理
    output_lines = []
    for title, body in chapters:
        ch_num = re.search(r'\d+', title)
        num = int(ch_num.group()) if ch_num else 0

        if num < 10:
            continue

        size_kb = len(body.encode("utf-8")) / 1024
        print(f"Processing: {title} ({size_kb:.0f} KB)...", flush=True)

        result = extract_rocky_lines(body)

        output_lines.append(f"### {title}")
        output_lines.append(result)
        output_lines.append("")

        has_dialogue = result != "（なし）" and result != "(error)"
        if has_dialogue:
            print(f"  → Rocky dialogue found", flush=True)
        else:
            print(f"  → {result}", flush=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
    print(f"\nSaved to: {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
