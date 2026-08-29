# progress — 進捗と注意点

作成日時: 2026-08-30 02:52
更新日時: 2026-08-30 04:50

## 現在の状態

Phase 0 完了。譜面 UI と MIDI 固定テンポ再生が連携して動く。

## 完了済み

- 2026-08-30: LLM との検討で得た 2 つの仕様ドラフトを `docs/reference/` に保存し、比較と採用方針を [../reference/approach-comparison.md](../reference/approach-comparison.md) にまとめた。
- 2026-08-30: 採用する構成を [../design/architecture.md](../design/architecture.md) に整理し、goals / plan を作成した。
- 2026-08-30: **Phase 0 完了。** `core/`(Python venv、mido + python-rtmidi + websockets)で MIDI を拍クロックで再生し、`{position, tempo, confidence}` を WebSocket 配信。`ui/`(Vite + TypeScript + OSMD 2.1.2)で Vivaldi 春の譜面を横 1 段表示し、連続値カーソルが追従。再生 / 停止 / 最初から / レート変更 / クリックでシークが動作。起動手順は [../../README.md](../../README.md)。
  - 検証: プレイヤークロックの実効 BPM 69.97(期待 70)、WebSocket 配信遅延 ≈ 0 ms、OSMD のカーソル表(1118 点)の拍が MIDI の発音拍と一致(弱起も含む)。Edge(headless)での自動操作とスクリーンショットで確認。

## 未完了

- Phase 1: 音声入力(sounddevice)+ chroma 抽出 + 可視化、記録(リプレイ)基盤。詳細は [plan.md](plan.md)。

## 注意点

- Python は必ず venv(`core/.venv`)で作業する。
- 楽譜ソースは `muse-score/` の `.mscz`(MuseScore 4)。書き出し済みの `.mxl`(圧縮 MusicXML)と `.mid` を同じフォルダに置く。いずれも Violin + Piano の 2 パート、MIDI は format 1・3 トラック(Violin / Piano 右手 / Piano 左手)、480 ppq。
  - `vivaldi_spring_first_movement_20251102`: 4/4、♩=70、83 小節、反復なし。**Phase 0 の対象曲**。
  - `noctune_op9-2_d_major_20250119`: 12/8、♩=50、19 小節、反復記号・1/2 番括弧あり。反復展開に対応するまで対象外。
  - `meditation_from_thais_20250120`: `.mscz` のみで未書き出し(反復なし、♩=77)。必要になったら書き出す。
- 伴奏再生では MIDI の Violin トラックを除外し、Piano の 2 トラックだけを鳴らす。Violin トラックは固定テンポ再生時のカーソル検証に使える。
- この PC には MuseScore 4 の CLI(`MuseScore4.exe`)が既定パスに見つからなかった。書き出しはユーザーが別途行う。
- `package.json`(root)は起動スクリプト用。`version` は 0.1.0。
- Phase 0 の MIDI 音源は Windows 標準の Microsoft GS Wavetable Synth(python-rtmidi 経由)。遅延が大きい(数十 ms 以上)ので、伴奏の同期精度を測る段階(Phase 3〜4)で FluidSynth 等に置き換える。
- WebSocket の配信周期は 30 Hz 指定だが Windows のタイマ粒度で実測 ≈ 21 Hz(47 ms)。UI は requestAnimationFrame で補間なしに描くので、必要なら UI 側で位置を外挿する。
- 再生開始直後に 100〜200 ms 程度の位置オフセットが観測された(クロック自体は正確)。GS 音源への最初のイベント送出が詰まる可能性。Phase 3 で伴奏遅延を測るときに再確認する。
- Vivaldi 春は弱起(0.5 拍)で始まる。MIDI も MusicXML も拍 0 から始まり整合するが、小節番号は MusicXML の `number` 属性(弱起 = 0)を使う。
- `start.bat` は ASCII のみで書く(日本語を入れると cmd.exe が UTF-8 を誤解釈して行が壊れる)。メッセージは英語。
- ポート 5173 は他プロジェクトが使っていることがある。`ui/vite.config.ts` は 5173 固定なので、衝突時は `npx vite --port 5199` などで起動する。
- 追従器は最初から自作しない。`matchmaker` の動作確認を Phase 3 の入口にする。
- リプレイ基盤を後回しにしない。Phase 1 の成果物に含める。
- マイク入力で試す場合はヘッドホンを使い、伴奏の回り込みを避ける。
