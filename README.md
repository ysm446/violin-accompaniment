# violin-accompaniment

バイオリン演奏に追従する自動伴奏アプリ。設計と進捗は [docs/README.md](docs/README.md) から辿る。

## 構成

```
core/         Python: 音声入力・特徴量抽出・記録・MIDI 伴奏(追従は未実装、固定テンポ再生)
ui/           Electron + Vite + TypeScript + OpenSheetMusicDisplay: 譜面表示とカーソル
scores/       楽譜。曲ごとに 1 フォルダ(score.mscz / score.mxl / score.mid / song.json)。詳細は scores/README.md
docs/         設計・計画・進捗
```

core と ui は別プロセスで、WebSocket(`ws://127.0.0.1:8765`)で `{position, tempo, confidence}` をやり取りする。Electron のメインプロセスが起動時に core を立ち上げ、終了時に止める。

## スタンドアローン版(利用者向け)

`npm run dist` で `ui/release/` に以下ができる。Python も Node も不要で、そのまま動く。

- `violin-accompaniment-0.1.0-portable.exe` — インストール不要。ダブルクリックで起動
- `violin-accompaniment Setup 0.1.0.exe` — インストーラ版

起動すると譜面が表示され、「再生」で伴奏(Windows 標準音源)が鳴りカーソルが進む。譜面クリックでシーク。曲はツールバーのプルダウンで切り替える(前回の曲を記憶)。

2 段目のバーが音声入力: 入力デバイスを選ぶとレベルメータと chroma(C〜B の 12 本)が動く。「記録開始」でセッション(音声・特徴量・再生位置)を `%APPDATA%iolin-accompanimentecordings\<日時>\` に保存する(開発時はリポジトリの `recordings/`)。

## 開発環境のセットアップ(初回)

```
python -m venv core/.venv
core/.venv/Scripts/python -m pip install -r core/requirements.txt
npm --prefix ui install
```

## 開発時の起動

```
start.bat        # ui をビルドして Electron を起動(core も自動起動)
```

または個別に:

```
npm run app      # 同上
npm run core     # core だけ(ターミナルで位置配信を見たいとき)
npm run ui       # ブラウザ版(http://localhost:5173、core は別途起動が必要)
```

- VS Code のターミナルでは `ELECTRON_RUN_AS_NODE=1` が設定されていることがあり、Electron が素の Node として起動してしまう。`start.bat` はこれを解除している。手動のときは `set ELECTRON_RUN_AS_NODE=` を先に実行する。
- WAV をマイク代わりに流す(リプレイ): `cd core && .venv\Scripts\python -m violin_core --scores-dir ..\scores --input-wav path	oudio.wav`
- 記録の再処理: `cd core && .venv\Scripts\python -m violin_core.replay ..ecordings\<日時>`
- 記録を楽譜と整列して音符ごとの評価を出す: `cd core && .venv\Scripts\python -m violin_core.align ..\recordings\<日時> --scores-dir ..\scores`(アプリの「振り返り」バーからも実行できる)
- 入力デバイス一覧: `cd core && .venv\Scripts\python -m violin_core --list-inputs`
- 別の MIDI 出力先を使う: `cd core && .venv/Scripts/python -m violin_core --list-ports` で確認し、`--midi-out "名前の一部"` を付ける。
- 曲の追加: `scores/<曲id>/` に `score.mxl` と `score.mid`(と `song.json`)を置く。詳細は [scores/README.md](scores/README.md)。

## ビルドと検証

```
npm run build        # ui の型チェックとビルド
npm run build:core   # core を PyInstaller で core/dist/violin_core.exe にする
npm run dist         # 上記 2 つ + electron-builder で ui/release/ に exe を作る
```
