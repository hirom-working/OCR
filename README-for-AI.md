# OCR（PDF OCR パイプライン）

## System
- purpose: ScanSnap PDF 自動 OCR + メタデータ抽出 + リネーム・整理
- type: cli
- lang: Python 3.14 (uv)
- deploy: Mac側ウォッチャー + Google Cloud Vision API（OCR）+ Gemini API（分類）
- repo: git@github.com:hirom-working/OCR.git

## Architecture
```
[ルート1] ~/Scansnap/ (スキャンPDF、OCR必要)
  → pypdfium2でページ→画像変換 (Mac側)
  → Cloud Vision images:annotate (DOCUMENT_TEXT_DETECTION, 8ページ/バッチ) でテキスト抽出
  → 元 PDF にテキスト埋め込み (PyMuPDF, Mac側)
  → ↓

[ルート2] ~/Scansnap-OCRed/ (電子書籍等、テキスト層あり)
  → OCR スキップ（--skip-ocr）
  → ↓

[共通] Gemini API (responseSchema構造化出力) でタイトル・著者・カテゴリ抽出
  → リネーム: "[著者] タイトル.pdf"
  → ~/電子図書/{カテゴリ}/{著者}/ に移動
```

## Dependencies
| 依存先 | 接続情報 | 用途 |
|--------|----------|------|
| Cloud Vision API | https://vision.googleapis.com | OCR (DOCUMENT_TEXT_DETECTION)。APIキーは1Password `op://Dev/Google-Cloud-Vision/VISION_API_KEY`。GCPプロジェクト `ocr-pipeline-486010`（課金・API有効化済み） |
| Gemini API | https://generativelanguage.googleapis.com | title/author/category抽出（responseSchema使用）。APIキーは1Password `op://Dev/Google-AI-Studio/GEMINI_API_KEY` |

## Key Paths
| パス | 内容 |
|------|------|
| process_pdf.py | メインパイプライン（OCR→埋め込み→分類→移動） |
| watch_scansnap.sh | fswatch ベースのフォルダ監視スクリプト |
| config.py | データクラスベース設定 |
| config.toml | 実行環境設定（.gitignore対象） |
| config.example.toml | 設定テンプレート |
| com.hirom.scansnap-watcher.plist | launchd 常駐監視設定 |
| docker/ | 旧pgx01/pgx02向けDocker環境（2026-07-26運用終了、本リポジトリ非管理） |

## Operations
| 操作 | コマンド |
|------|---------|
| 常駐監視 | launchd `com.hirom.scansnap-watcher`（自動起動） |
| 手動単発（OCRあり） | `uv run python process_pdf.py /path/to/file.pdf` |
| 手動単発（OCRスキップ） | `uv run python process_pdf.py /path/to/file.pdf --skip-ocr` |
| ドライラン | `uv run python process_pdf.py /path/to/file.pdf --dry-run` |
| ウォッチャーログ | `tail -f ~/Library/Logs/scansnap_watcher.log` |

## Configuration（config.toml）
- local: output_dir=~/電子図書、scansnap_dir=~/Scansnap
- llm: model=gemini-3.6-flash（Gemini API、APIキーはop readで実行時取得、ディスクに平文保存しない）
- categories: IT, ナイフマガジン, 小説, 語学, 心理学, ビジネス・経営 等13種
- OCR設定（ocr_server等）は無し。Cloud Vision APIキーは`process_pdf.py`内で`op read`により実行時取得、コード内に`VISION_BATCH_SIZE=8`定数あり

## Current State
- 最終更新: 2026-07-26
- OCR方式: Surya → Yomitoku HTTP API（pgx03→pgx01） → Macローカルyomitoku(CPU) → **Google Cloud Vision API（2026-07-26、最終版）**
- 分類方式: ローカル/リモートLLM（pgx01等） → **Gemini API + responseSchema構造化出力（2026-07-26）**
- **Macローカルyomitoku(CPU)からCloud Visionへ切り替えた理由**: CPU推論は208ページの本で10分タイムアウトし実用不可（本来31分程度かかる計算）。さらにクライアント切断してもサーバー側`ThreadPoolExecutor`が処理を継続する不具合でCPU張り付き・ファン唸りが発生。Cloud Visionは同じ本を約1分半で完走。縦書き単体(小説)・縦横混在(雑誌)とも実データで読み順含め良好と確認済み
- yomitoku_local(オフラインフォールバックとして温存していたローカルuv venv構成)は2026-07-26付けで完全削除済み（launchd plist, ログ, venv一式2.3GB）。以後OCRはCloud Vision API必須、オフライン代替手段は無し
- 稼働: Mac側ウォッチャー常駐 + Cloud Vision API（OCR）+ Gemini API（分類）、いずれもクラウド。ローカル常駐プロセスは無し
- pgx01は全機能とも移設対象外で売却予定（OCR/分類ともクラウドAPI完結に切替済み、pgx01依存は解消済み）
