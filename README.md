# PDF OCR Pipeline

Scansnapフォルダを監視し、PDFを自動でOCR処理・リネーム・移動するパイプライン。

## ワークフロー

```
1. ~/Scansnap に新しいPDFが追加される
2. pgx02でSurya OCRを実行
3. OCRテキストを元PDFに埋め込み
4. pgx01のOllamaでタイトル・著者を抽出
5. ファイル名を変更: "[著者] タイトル.pdf" or "タイトル.pdf"
6. ~/電子図書/ に移動
7. 一時ファイルをクリーンアップ
```

## セットアップ

```bash
cd /Users/hirom/Projects/OCR
uv sync
```

## 使い方

### 常駐監視（推奨）

```bash
# バックグラウンドで起動
uv run python watch_scansnap.py &

# または nohup で
nohup uv run python watch_scansnap.py > watch.log 2>&1 &
```

### 一回だけ実行

```bash
# 既存ファイルを処理して終了
uv run python watch_scansnap.py --once
```

## フォルダ構成

| フォルダ | 用途 |
|----------|------|
| `~/Scansnap/` | スキャン入力（監視対象） |
| `~/電子図書/` | 処理後の出力先 |

## サーバー構成

| サーバー | 用途 |
|----------|------|
| pgx02 | Surya OCR（GPU） |
| pgx01 | Ollama（タイトル抽出） |

## 手動パイプライン

```bash
# 特定フォルダのPDFをOCR処理（上書き）
uv run python pipeline.py "/path/to/pdfs/"

# バッチOCR結果を適用
uv run python process_batch.py "/path/to/pdfs/" \
    --remote-results "pgx02:~/ocr_output/batch/results.json"
```
