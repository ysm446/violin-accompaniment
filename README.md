# violin-accompaniment

バイオリン演奏に追従する自動伴奏アプリ。設計と進捗は [docs/README.md](docs/README.md) から辿る。

## 構成

```
core/         Python: 音声処理・追従・MIDI 伴奏(現在は固定テンポ再生 + 位置配信)
ui/           Web(Vite + TypeScript + OpenSheetMusicDisplay): 譜面表示とカーソル
muse-score/   楽譜ソース(.mscz)と書き出し済みの .mxl / .mid
docs/         設計・計画・進捗
```

core と ui は別プロセスで、WebSocket(`ws://127.0.0.1:8765`)で `{position, tempo, confidence}` をやり取りする。

## セットアップ(初回)

```
python -m venv core/.venv
core/.venv/Scripts/python -m pip install -r core/requirements.txt
npm --prefix ui install
```

## 起動(Phase 0)

`start.bat` をダブルクリック(または `start.bat 5199` のようにポート指定)すると core・ui を別ウィンドウで起動し、ブラウザを開く。手動で起動する場合はターミナルを 2 つ使う。

```
npm run core     # MIDI 伴奏を Windows 標準音源(Microsoft GS Wavetable Synth)で再生し、位置を配信
npm run ui       # http://localhost:5173 を開く
```

ブラウザで「再生」を押すと伴奏が鳴り、譜面上の赤いカーソルが進む。譜面をクリックするとその位置へシークする。

- 別の MIDI 出力先を使う: `cd core && .venv/Scripts/python -m violin_core --list-ports` で確認し、`--midi-out "名前の一部"` を付ける。
- 別の曲: `--midi ../muse-score/xxx.mid` を変え、`ui/src/main.ts` の `SCORE_URL` を対応する `.mxl` にする。

## 検証

```
npm run build    # ui の型チェックとビルド
```
