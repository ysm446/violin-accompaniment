# progress — 進捗と注意点

作成日時: 2026-08-30 02:52
更新日時: 2026-08-30 06:30

## 現在の状態

Phase 0 完了。Electron のスタンドアローンアプリ(exe)として配布できる状態。

## 完了済み

- 2026-08-30: LLM との検討で得た 2 つの仕様ドラフトを `docs/reference/` に保存し、比較と採用方針を [../reference/approach-comparison.md](../reference/approach-comparison.md) にまとめた。
- 2026-08-30: 採用する構成を [../design/architecture.md](../design/architecture.md) に整理し、goals / plan を作成した。
- 2026-08-30: **Phase 0 完了。** `core/`(Python venv、mido + python-rtmidi + websockets)で MIDI を拍クロックで再生し、`{position, tempo, confidence}` を WebSocket 配信。`ui/`(Vite + TypeScript + OSMD 2.1.2)で Vivaldi 春の譜面を横 1 段表示し、連続値カーソルが追従。再生 / 停止 / 最初から / レート変更 / クリックでシークが動作。起動手順は [../../README.md](../../README.md)。
  - 検証: プレイヤークロックの実効 BPM 69.97(期待 70)、WebSocket 配信遅延 ≈ 0 ms、OSMD のカーソル表(1118 点)の拍が MIDI の発音拍と一致(弱起も含む)。Edge(headless)での自動操作とスクリーンショットで確認。

## 完了済み(続き)

- 2026-08-30: **Electron 化とスタンドアローン exe 化。** `ui/electron/main.cjs` が core を子プロセスとして起動・停止。core は PyInstaller(onedir)で `violin_core.exe` に固め、electron-builder で `ui/release/` に portable exe と NSIS インストーラを生成(約 110 MB)。パッケージ版を実際に起動し、同梱 core の自動起動と再生を確認。

- 2026-08-30: **曲選択 UI と楽譜フォルダの再編。** `muse-score/` を `scores/<song_id>/`(score.mscz / score.mxl / score.mid / song.json)に変更。core が `--scores-dir` でフォルダを走査し、接続時に曲一覧を送る。UI はプルダウンで切り替え(`{"cmd":"load"}`)、前回の曲を localStorage に記憶。カーソル表の拍を `CurrentEnrolledTimestamp`(反復展開後)にしたので、反復記号のある曲(ノクターン)も MIDI と揃う。

## 未完了

- Phase 1: 音声入力(sounddevice)+ chroma 抽出 + 可視化、記録(リプレイ)基盤。詳細は [plan.md](plan.md)。

## 注意点

- Python は必ず venv(`core/.venv`)で作業する。
- 楽譜は `scores/<song_id>/`(構成は [../../scores/README.md](../../scores/README.md))。いずれも Violin + Piano の 2 パート、MIDI は format 1・3 トラック(Violin / Piano 右手 / Piano 左手)、480 ppq。
  - `vivaldi_spring_1`: 4/4、♩=70、83 小節、反復なし、弱起 0.5 拍。
  - `chopin_nocturne_op9_2`: 12/8、♩=50、19 小節、反復記号・1/2 番括弧あり。MIDI は展開済み、UI 側は OSMD の展開後時刻で対応。 検証: OSMD の展開後拍は 144.5 拍まで MIDI の発音拍と全点一致(装飾音の細分は除く)。最終小節だけ OSMD 側に 146〜147.5 拍の点が余り、MIDI(146 拍で終了)より 1.5 拍長い。曲末のみの差なので保留。`.mscz` は 20251212 版、`.mxl` / `.mid` は 20250119 版から書き出したものなので、内容がずれていたら書き出し直す。
  - タイスの瞑想曲は `.mscz` がリポジトリに無い(削除済み)。必要なら `scores/massenet_meditation_thais/` に置く。
- 伴奏再生では MIDI の Violin トラックを除外し、Piano の 2 トラックだけを鳴らす。Violin トラックは固定テンポ再生時のカーソル検証に使える。
- この PC には MuseScore 4 の CLI(`MuseScore4.exe`)が既定パスに見つからなかった。書き出しはユーザーが別途行う。
- `package.json`(root)は起動スクリプト用。`version` は 0.1.0。
- Phase 0 の MIDI 音源は Windows 標準の Microsoft GS Wavetable Synth(python-rtmidi 経由)。遅延が大きい(数十 ms 以上)ので、伴奏の同期精度を測る段階(Phase 3〜4)で FluidSynth 等に置き換える。
- WebSocket の配信周期は 30 Hz 指定だが Windows のタイマ粒度で実測 ≈ 21 Hz(47 ms)。UI は requestAnimationFrame で補間なしに描くので、必要なら UI 側で位置を外挿する。
- 再生開始直後に 100〜200 ms 程度の位置オフセットが観測された(クロック自体は正確)。GS 音源への最初のイベント送出が詰まる可能性。Phase 3 で伴奏遅延を測るときに再確認する。
- 弱起の曲は MIDI も MusicXML も拍 0 から始まり整合する。小節番号は MusicXML の `number` 属性(弱起 = 0)を使う。
- Electron のビルド・起動時は環境変数 `ELECTRON_RUN_AS_NODE` を解除する(VS Code のターミナルが `1` を設定し、Electron が素の Node として起動して `app` が undefined になる)。`start.bat` と `npm run dist` 前の手動実行で注意。
- electron-builder は `npmRebuild: false` にする(OSMD が依存する Node ネイティブモジュール `gl` の再ビルドが失敗する。レンダラは Vite でバンドル済みなので不要)。`node_modules` はパッケージに含めない。
- electron-builder の Electron 展開で `win-unpacked.tmp` → `win-unpacked` の rename が EPERM になる環境のため、`electronDist: node_modules/electron/dist` を指定してコピー方式にしている。
- パッケージ版の楽譜は `resources/scores/<song_id>/`(score.mid / score.mxl / song.json のみ同梱)、core は `resources/core/violin_core.exe`。
- `start.bat` は ASCII のみで書く(日本語を入れると cmd.exe が UTF-8 を誤解釈して行が壊れる)。メッセージは英語。
- ポート 5173 は他プロジェクトが使っていることがある。`ui/vite.config.ts` は 5173 固定なので、衝突時は `npx vite --port 5199` などで起動する。
- 追従器は最初から自作しない。`matchmaker` の動作確認を Phase 3 の入口にする。
- リプレイ基盤を後回しにしない。Phase 1 の成果物に含める。
- マイク入力で試す場合はヘッドホンを使い、伴奏の回り込みを避ける。
