# Yomitoku OCR ベンチマーク: CPU vs GPU on DGX Spark

## 実施日
2026-03-14

## テスト対象PDF
- ファイル名: コンテナ環境の構築・運用・活（…）.pdf
- ファイルサイズ: 537MB (562,789,072 bytes)
- ページ数: 499
- ページサイズ: 513 x 655 pts
- OCRパラメータ: `--reading_order auto --dpi 200 -f json`

## 環境

### 共通ハードウェア: NVIDIA DGX Spark
- SoC: NVIDIA GB10 (Grace Blackwell)
- CPU: Cortex-X925 × 10 + Cortex-A725 × 10 (20コア, 1T/core)
- メモリ: 128GB 統合メモリ (CPU/GPU共有)
- GPU: NVIDIA GB10 (CUDA capability 12.1)
- OS: Ubuntu 24.04 LTS (aarch64)
- NVIDIA Driver: 580.126.09
- CUDA Toolkit: 13.0

### pgx02 (CPU実行)
- Yomitoku: 0.10.3
- PyTorch: 2.10.0+cpu
- CUDA: 使用せず（GPT-OSS-120B BF16がGPUメモリ66GB占有中）
- GPU占有プロセス: llama-server (GPT-OSS-120B BF16, 128K ctx)
- 空きメモリ: ~1.9GB (RAM), GPU空きなし
- 開始時刻: 2026-03-14 20:19:25 (JST)

### pgx03 (GPU実行)
- Yomitoku: 0.12.0
- PyTorch: 2.10.0+cu128
- CUDA: 有効 (capability 12.1, 警告あり: サポート上限12.0)
- GPU: 専有（他プロセスなし）
- GPUメモリ使用: ~2,809MB
- GPU使用率: 95%
- GPU温度: 74℃
- 開始時刻: 2026-03-14 12:41:26 (UTC) = 21:41:26 (JST)

## 結果

### pgx02 (CPU) — 計測中
- 開始: 20:19:25 JST
- 81分経過時点: 171/499ページ完了
- ペース: ~28.5秒/page (約2.1 pages/min)
- 推定総時間: ~237分 (約4時間)
- CPU使用率: 786% (20コア中約8コア)

### pgx03 (GPU) — 完了
- 開始: 21:41:26 JST
- 終了: 21:58:59 JST
- **総処理時間: 17分33秒** (wall clock)
- 出力ファイル数: 499/499 (全ページ完了)
- ペース: **2.11秒/page** (28.4 pages/min)
- 最大メモリ使用: 15,792MB (15.4GB)
- GPU使用率: 95%
- モデル初期化時間: ~17秒 (TextDetector + TextRecognizer + LayoutParser)

### ページあたり処理時間内訳 (pgx03 GPU, 代表値)

| 処理段階 | 平均時間 (秒) | 備考 |
|---------|-------------|------|
| TextDetector | ~0.39 | テキスト領域検出 |
| TextRecognizer | ~1.1 | 文字認識（最もばらつき大: 0.22〜3.70秒） |
| LayoutParser | ~0.27 | レイアウト解析 |
| TableStructureRecognizer | ~0.01 | 表構造認識 |
| **合計** | **~1.5** | |

## 速度比較

| 指標 | pgx02 (CPU) | pgx03 (GPU) | 倍率 |
|------|------------|------------|------|
| 秒/page | ~93.7 (実測: 645分/412p, 失速込み) | **2.11** | **~44x** |
| pages/min | ~0.64 (実測平均) | **28.4** | **~44x** |
| 総処理時間 | 645分で未完(412/499) | **17分33秒** (全499p完了) | **~37x以上** |

## 注意事項
- pgx02はYomitoku 0.10.3、pgx03は0.12.0であり、バージョン差による性能差の可能性あり
- pgx02はGPT-OSS-120Bが同時稼働中（メモリ圧迫の影響あり）
- PyTorch cu128はGB10 (capability 12.1) を公式サポート外として警告を出すが動作に問題なし
- pgx02のCPU実行は並列（飛び飛びページ）で処理、pgx03はシーケンシャル

## 最終結果

### pgx03 (GPU) ✅ 完了
- 開始: 21:41:26 JST → 終了: 21:58:59 JST
- **総処理時間: 17分33秒**
- 出力ファイル数: 499/499
- 最大メモリ: 15.4GB RSS
- 平均ペース: 2.11秒/page

### pgx02 (CPU) ❌ 中断 (kill)
- 開始: 20:19:25 JST → 中断: 翌07:04頃 JST
- **経過時間: 10時間45分** (未完了)
- 出力ファイル数: 412/499 (82.6%)
- 後半で激しく失速（CPU使用率 786% → 116%、メモリ圧迫の兆候）
- 完走推定: 12〜14時間
