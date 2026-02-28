# OCR（PDF OCR パイプライン）

## System
- purpose: ScanSnap PDF 自動 OCR + メタデータ抽出 + リネーム・整理
- type: cli
- lang: Python 3.11 (uv)
- deploy: ローカル実行（ウォッチャーデーモン）
- repo: git@github.com:hirom-working/OCR.git

## Architecture
```
~/Scansnap/ (新規 PDF 検出)
  → pgx02: Surya OCR (GPU) でテキスト抽出
  → 元 PDF にテキスト埋め込み (PyMuPDF)
  → pgx01: LLM でタイトル・著者抽出
  → リネーム: "[著者] タイトル.pdf"
  → ~/電子図書/ に移動
```

## Dependencies
| 依存先 | 接続情報 | 用途 |
|--------|----------|------|
| pgx02 | SSH | Surya OCR（GPU 処理） |
| pgx01 | 8000 or 8080 | LLM メタデータ抽出（vLLM/Ollama/llama-cpp） |

## Key Paths
| パス | 内容 |
|------|------|
| watch_scansnap.py | ファイルウォッチャーデーモン |
| apply_ocr.py | メイン OCR 処理 |
| pipeline.py | 手動パイプライン実行 |
| process_batch.py | バッチ処理 |
| config.py | データクラスベース設定（155行） |
| config.toml | 実行環境設定 |

## Operations
| 操作 | コマンド |
|------|---------|
| 常駐監視 | `uv run python watch_scansnap.py &` |
| 単発実行 | `uv run python watch_scansnap.py --once` |
| 手動パイプライン | `uv run python pipeline.py "/path/to/pdfs/"` |
| バッチ結果適用 | `uv run python process_batch.py "/path/" --remote-results "pgx02:~/ocr_output/batch/results.json"` |

## Configuration（config.toml）
- local: output_dir=~/電子図書、scansnap_dir=~/Scansnap
- ocr_server: host=pgx02、venv_path=~/Projects/.venv
- llm: backend=vllm/ollama/llama-cpp、host=pgx01、model=gemma3-27b
- categories: フォルダ分類（技術、文学、その他 等）
