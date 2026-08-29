# plan — 実装方針と優先順位

作成日時: 2026-08-30 02:52
更新日時: 2026-08-30 06:30

## 実装方針

- 構成は [../design/architecture.md](../design/architecture.md) に従う。core(Python)と ui(Web + OpenSheetMusicDisplay)を別プロセスにし、WebSocket で 3 値を配信する。
- 追従器は自作せず `matchmaker` を使うところから始める。精度が頭打ちになった時点で HMM 化や C++ 移植を検討する。
- 特徴量は chroma を主にする。f0 は演奏後フィードバック用。
- 伴奏は MIDI + FluidSynth。オーディオ伴奏は後回し。
- リプレイ基盤(録音・特徴量・推定値の記録とオフライン再評価)を Phase 1 から組み込む。
- 譜面 UI を追従より先に作り、デバッグビューアとして使う。
- Python の依存関係は `core/.venv`(`python -m venv`)に閉じ込める。グローバル環境にはインストールしない。
- 楽譜は `scores/<song_id>/` に曲ごとにまとめる(`score.mscz` がソース、`score.mxl` / `score.mid` は MuseScore 4 から書き出し、`song.json` に表示名)。構成は `scores/README.md`。

## フェーズと完了条件

| Phase | 内容 | 完了条件 |
| --- | --- | --- |
| 0 | リポジトリ骨格、楽譜変換(mscz → MusicXML / MIDI)、譜面 UI + MIDI 固定テンポ再生 | カーソルが譜面上を正しく進む |
| 1 | 音声入力 + chroma 抽出 + 可視化、記録(リプレイ)基盤 | 録音ファイルからも実機からも同じパイプラインが動き、遅延の実測値が取れる |
| 2 | オフライン DTW + 演奏後フィードバック | 録音と楽譜が対応し、音程・タイミング偏差が譜面に出る |
| 3 | オンライン追従(matchmaker)+ 固定テンポ MIDI 伴奏 | 一定テンポの短い曲で合奏が成立する |
| 4 | テンポ推定(カルマン)+ レート変調 + 先読みスケジューリング | テンポ変化に伴奏が追従する |
| 5 | ミス・飛ばし・停止・休符への耐性 | 完走率 > 95 % |
| 6 | オーディオ伴奏 + タイムストレッチ | — |
| 7 | ゲームモード | — |

Phase 3 で「一応合奏になる」。Phase 4〜5 が音楽的な仕上がりの勝負で、工数の大半がここに集中する想定。Phase 2 は追従が未完成でも価値があるため、優先度を下げない。

## 技術選定(初期)

| 用途 | 採用 | 代替 |
| --- | --- | --- |
| 音声入出力 | sounddevice | — |
| 特徴量 | librosa(CQT / chroma) | 自作 |
| 楽譜解析 | partitura | music21 |
| 追従 | matchmaker | 自作 Online DTW |
| MIDI 音源 | Phase 0: Microsoft GS Wavetable Synth(python-rtmidi)。Phase 3 以降: FluidSynth(pyfluidsynth) | sfizz |
| 譜面描画 | OpenSheetMusicDisplay | Verovio |
| UI 基盤 | Electron + Vite + TypeScript(electron-builder で exe 配布) | — |
| 通信 | WebSocket(JSON) | — |

ライセンスは同梱形態が決まった時点で個別に確認する。

## 直近の作業(Phase 1)

1. `core` に `sounddevice` で 48 kHz mono 入力を追加し、ブロック単位で chroma(CQT)+ spectral flux を計算する。
2. 生音声(WAV)・特徴量・3 値をセッション単位で記録する `recorder` を作り、録音ファイルからも同じパイプラインを回せるようにする(リプレイ基盤)。
3. UI に入力レベルと chroma の簡易表示を足し、マイク / ライン入力が拾えていることを確認できるようにする。
4. 発音 → 特徴量更新までの遅延を実測する。

Phase 0 の成果物は [progress.md](progress.md) と [../../README.md](../../README.md) を参照。

## 未決事項

- Python core を C++ に移植する判断基準(Phase 3 の精度が出た時点で決める)。
- 反復記号の展開に対応する時期。
- 伴奏音源(SF2 / SFZ)の配布方法とライセンス。
