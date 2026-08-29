# Changelog

作成日時: 2026-08-30 02:52
更新日時: 2026-08-30 04:30

## 未リリース

### 2026-08-30 04:10 — Phase 0

- `core/`(Python)を追加: MIDI を拍クロックで再生(Violin トラック除外、レート変更・シーク対応)し、`{position, tempo, confidence}` を WebSocket で配信。
- `ui/`(Vite + TypeScript + OpenSheetMusicDisplay)を追加: 譜面の横 1 段表示、連続値カーソル、再生 / 停止 / 最初から / レート / クリックでシーク。
- root `package.json`(`npm run core` / `npm run ui` / `npm run build`)、`README.md`、`.gitignore`、`start.bat`(core・ui を一括起動)を追加。

### 2026-08-30 02:52

- docs を整理。LLM との検討ドラフト 2 件を `docs/reference/draft-spec-claude.md` / `draft-spec-codex.md` に移動。
- 2 案の比較と採用方針(`docs/reference/approach-comparison.md`)、アーキテクチャ設計ガイド(`docs/design/architecture.md`)を追加。
- `docs/plan/` の goals / plan / progress を作成。
