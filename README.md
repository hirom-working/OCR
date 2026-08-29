# PDF OCR Pipeline

Scansnapフォルダを監視し、PDFを自動でOCR処理・リネーム・移動するパイプライン。

## ワークフロー

```
1. ~/Scansnap に新しいPDFが追加される（電子書籍等テキスト層ありのPDFは ~/Scansnap-OCRed へ）
2. Google Cloud Vision API で OCR（DOCUMENT_TEXT_DETECTION, 8ページ/バッチ）
3. OCRテキストを元PDFに埋め込み（PyMuPDF）
4. Gemini API（responseSchema構造化出力）でタイトル・著者・カテゴリを抽出
5. ファイル名を変更: "[著者] タイトル.pdf"
6. ~/電子図書/{カテゴリ}/{著者}/ に移動
```

OCR・分類ともクラウドAPI完結。ローカルGPU/リモートサーバーは不使用（2026-07-26、pgx01/pgx02売却に伴い移行済み）。

## セットアップ

```bash
cd /Users/hirom/Projects/OCR
uv sync
cp config.example.toml config.toml  # 環境に合わせて編集
```

APIキーは1Passwordから実行時に取得（`op://Dev/Google-Cloud-Vision/VISION_API_KEY`, `op://Dev/Google-AI-Studio/GEMINI_API_KEY`）。ディスクには平文保存しない。

## 使い方

### 常駐監視（推奨）

launchd（`com.hirom.scansnap-watcher.plist`、テンプレートは`com.example.scansnap-watcher.plist`）で自動起動。
ログは `~/Library/Logs/scansnap_watcher.log`。

### 手動実行

```bash
# OCRあり
uv run python process_pdf.py /path/to/file.pdf

# OCRスキップ（既にテキスト層がある場合）
uv run python process_pdf.py /path/to/file.pdf --skip-ocr

# ドライラン
uv run python process_pdf.py /path/to/file.pdf --dry-run
```

## フォルダ構成

| フォルダ | 用途 |
|----------|------|
| `~/Scansnap/` | スキャン入力（OCR必要、監視対象） |
| `~/Scansnap-OCRed/` | 電子書籍等、テキスト層ありの入力 |
| `~/電子図書/{カテゴリ}/{著者}/` | 処理後の出力先 |

詳細なアーキテクチャ・依存関係・変遷history は `README-for-AI.md` を参照。
