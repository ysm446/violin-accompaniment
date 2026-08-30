# violin-accompaniment

バイオリン演奏に追従する自動伴奏アプリ。設計と進捗は [docs/README.md](docs/README.md) から辿る。

## 構成

```
core/         Python: 音声入力・特徴量抽出・記録・オフライン整列(演奏後フィードバック)・オンライン追従・MIDI 伴奏
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

ツールバーの「◎ 追従」を ON にすると、演奏を聴き取って伴奏がついてくる(青いカーソルが演奏位置)。弾き始めると伴奏が始まり、止まると確信度が下がる。「最初から」で追従器もリセット。

2 段目のバーが音声入力: 入力デバイスを選ぶとレベルメータと chroma(C〜B の 12 本)が動く。「記録開始」でセッション(音声・特徴量・再生位置)を `%APPDATA%/violin-accompaniment/recordings/<日時>/` に保存する(開発時はリポジトリの `recordings/`)。

3 段目の「振り返り」で記録を選んで「解析」すると、譜面上に音符ごとの音程偏差(色)とタイミング偏差(バー)が出る。

## 開発環境のセットアップ(初回)

```
npm --prefix ui install
```

`core/.venv` は `start.bat` が無ければ自動で作る(Python 3 が PATH にあること)。手で作るなら:

```
python -m venv core/.venv
core/.venv/Scripts/python -m pip install -r core/requirements.txt
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

以下は `cd core` してから(パスは PowerShell / cmd どちらでも通るよう `/` 区切りで書く):

- VS Code のターミナルでは `ELECTRON_RUN_AS_NODE=1` が設定されていることがあり、Electron が素の Node として起動してしまう。`start.bat` はこれを解除している。手動のときは `set ELECTRON_RUN_AS_NODE=` を先に実行する。
- WAV をマイク代わりに流す(リプレイ): `.venv/Scripts/python -m violin_core --scores-dir ../scores --input-wav path/to/audio.wav`
- 記録の再処理: `.venv/Scripts/python -m violin_core.replay ../recordings/<日時>`
- 記録を楽譜と整列して音符ごとの評価を出す: `.venv/Scripts/python -m violin_core.align ../recordings/<日時> --scores-dir ../scores`(アプリの「振り返り」バーからも実行できる)
- 追従器の評価(合成演奏 + 実演奏): `.venv/Scripts/python tools/eval_follower.py`
- 入力デバイス一覧: `.venv/Scripts/python -m violin_core --list-inputs`
- 別の MIDI 出力先を使う: `.venv/Scripts/python -m violin_core --list-ports` で確認し、`--midi-out "名前の一部"` を付ける。
- 曲の追加: `scores/<曲id>/` に `score.mxl` と `score.mid`(と `song.json`)を置く。詳細は [scores/README.md](scores/README.md)。

## ビルドと検証

```
npm run build        # ui の型チェックとビルド
npm run test:core    # core の回帰テスト
npm run build:core   # core を PyInstaller で core/dist/violin_core.exe にする
npm run dist         # 上記 2 つ + electron-builder で ui/release/ に exe を作る
```
