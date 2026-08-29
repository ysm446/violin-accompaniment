# Changelog

作成日時: 2026-08-30 02:52
更新日時: 2026-08-30 12:00

## 未リリース

### 2026-08-30 12:00 — Phase 3: オンライン追従

- 演奏をリアルタイムに楽譜と照合する追従器を追加(`violin_core.follower`)。
- ツールバーに「◎ 追従」ボタン。ON にすると青いカーソルが演奏位置を示し、伴奏が演奏に同期する(誤差が大きいときはシーク、小さいときはテンポを緩やかに合わせる)。「最初から」で追従器もリセット。
- 追従器の評価スクリプト `core/tools/eval_follower.py`。

### 2026-08-30 09:30 — Phase 2: 演奏後フィードバック

- 記録したセッションを楽譜と DTW で整列し、音符ごとの音程偏差(セント)とタイミング偏差(ms)を計算(`violin_core.align`)。
- UI に「振り返り」バーを追加。記録を選んで「解析」すると譜面上にマーカーが出る(色 = 音程、バー = 早い/遅い、ホバーで詳細)。
- 評価用に正解つき合成演奏の生成(`violin_core.synth`)を追加。

### 2026-08-30 07:30 — Phase 1: 音声入力とリプレイ基盤

- 入力デバイス(マイク / オーディオ IF)から 48 kHz で取り込み、chroma・スペクトラルフラックス・レベルをリアルタイムに計算。
- UI に入力デバイス選択、レベルメータ、chroma バー、遅延表示、記録開始/停止ボタンを追加。
- セッション記録(音声 WAV・特徴量・再生位置)と、記録をオフラインで再処理する `violin_core.replay` を追加。`--input-wav` で WAV をマイク代わりに流せる。

### 2026-08-30 06:30 — 曲選択と楽譜フォルダ再編

- ツールバーに曲のプルダウンを追加。切り替えると core が MIDI を差し替え、譜面を再描画。前回の曲を記憶。
- `muse-score/` を `scores/<song_id>/`(score.mscz / score.mxl / score.mid / song.json)に再編。core は `--scores-dir` で走査。
- 反復記号のある曲でもカーソルが MIDI と揃うように、OSMD の反復展開後時刻を使用。

### 2026-08-30 05:40 — Electron 化・スタンドアローン exe

- `ui/electron/main.cjs` を追加。Electron が core を自動起動・停止する。
- core を PyInstaller で `violin_core.exe` 化(`npm run build:core`)、electron-builder で portable exe / インストーラを生成(`npm run dist`)。
- `start.bat` を Electron 開発起動用に変更。README を更新。

### 2026-08-30 04:10 — Phase 0

- `core/`(Python)を追加: MIDI を拍クロックで再生(Violin トラック除外、レート変更・シーク対応)し、`{position, tempo, confidence}` を WebSocket で配信。
- `ui/`(Vite + TypeScript + OpenSheetMusicDisplay)を追加: 譜面の横 1 段表示、連続値カーソル、再生 / 停止 / 最初から / レート / クリックでシーク。
- root `package.json`(`npm run core` / `npm run ui` / `npm run build`)、`README.md`、`.gitignore`、`start.bat`(core・ui を一括起動)を追加。

### 2026-08-30 02:52

- docs を整理。LLM との検討ドラフト 2 件を `docs/reference/draft-spec-claude.md` / `draft-spec-codex.md` に移動。
- 2 案の比較と採用方針(`docs/reference/approach-comparison.md`)、アーキテクチャ設計ガイド(`docs/design/architecture.md`)を追加。
- `docs/plan/` の goals / plan / progress を作成。
