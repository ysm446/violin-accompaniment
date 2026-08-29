# アーキテクチャ設計ガイド

作成日時: 2026-08-30 02:52
更新日時: 2026-08-30 06:30

採用方針の根拠は [../reference/approach-comparison.md](../reference/approach-comparison.md) を参照。本書は実装時に守る構成と境界を定める。

## 1. 共有インターフェース

システムの唯一の共有値。伴奏・UI・将来のゲーム判定はこの値だけを購読する。

```
{ position:   float,   // 楽譜上の位置。四分音符 = 1.0 の拍単位、連続値
  tempo:      float,   // BPM(平滑化済み)
  confidence: float }  // 0.0〜1.0
```

- follower の内部実装(DTW / HMM)を下流に漏らさない。
- 音符 ID は返さない。UI は拍位置から描画座標を計算する。
- 実装(Phase 0): core は接続時に `{"type":"songs", songs, current}` を 1 回、その後約 30 Hz で `{"type":"state", position, tempo, confidence, playing, rate, length, song, time}` を送る。UI → core は `{"cmd": "play"|"stop"|"reset"|"seek"|"rate"|"load", ...}`。詳細は `core/violin_core/server.py` の docstring。

## 2. プロセス構成

| プロセス | 技術(初期) | 役割 |
| --- | --- | --- |
| core | Python | 音声入力、特徴量、追従、テンポ推定、MIDI 伴奏、記録 |
| ui | Electron(レンダラは Vite + TypeScript) | 譜面表示、カーソル、操作、セッション分析。メインプロセスが core を子プロセスとして起動・停止 |

通信は WebSocket(localhost)。core → ui は 3 値を約 30 Hz で配信、ui → core は制御コマンド(load / start / stop / seek / 設定)。音声データは通さない。

core を後で C++ に置き換える場合も、この境界は変えない。

配布形態: core は PyInstaller(onedir)で exe 化し、electron-builder の `extraResources` で `resources/core/` に同梱する。楽譜は `resources/scores/`。開発時は Electron が `core/.venv` の python を直接起動する(`ui/electron/main.cjs` の `resolveCoreLaunch`)。

## 3. core のモジュール

| モジュール | 初期実装 | 備考 |
| --- | --- | --- |
| audio-io | `sounddevice`、48 kHz mono、ブロック 512 samples | ピエゾ / マイク向けの HPF・EQ を設定で差し込めるようにする |
| feature | CQT ベース chroma(12 次元、L2 正規化)+ spectral flux | ホップ 512 samples(≒ 10.7 ms)。f0 は演奏後フィードバック用に別途 |
| follower | `matchmaker`(Online DTW)。精度が頭打ちなら HMM へ | 参照系列は楽譜から合成した chroma。confidence は累積コストから導出 |
| tempo | カルマンフィルタ。状態 (position, tempo)、観測ノイズは confidence で動的に変える | 瞬間変動をそのまま伴奏に流さない |
| accomp | MIDI を拍クロックで再生(`player.py`)。音源は Phase 0 では Microsoft GS Wavetable Synth(python-rtmidi)、Phase 3 以降 FluidSynth | レート変調と先読みスケジューリング(下記) |
| recorder | 生音声・特徴量・3 値・楽譜・パラメータを記録 | リプレイで follower だけ差し替えて再評価できること |
| score | `partitura` で MusicXML を解析 | `part-name` が `Violin` のパートを追従対象、他を伴奏とする。反復記号を含む楽譜は初期は弾く |

### 3.1 伴奏の同期制御

通常時(連続的なレート変調):

```
error    = position_est - position_playback
rate_raw = tempo_est / tempo_score + Kp * error
rate     = clamp(slew_limit(rate_raw), 0.7, 1.4)
```

- `Kp` は位置誤差を 10〜20 拍かけて吸収する程度に緩くする。
- `slew_limit` は必須(レート変化率を制限しないと可聴なワウが出る)。
- クランプ範囲外を要求されたら不連続シークへ切り替える(`allNotesOff()` → 短いリリース → `seek()`)。

先読みスケジューリング: 検出レイテンシがある以上、反応型では必ず遅れる。伴奏イベントは推定位置とテンポから 100〜200 ms 先を予測してスケジュールする。

ロスト時: confidence が閾値を下回っても位置推定を止めず、直前のテンポで外挿を続ける。confidence は UI に伝播させる。

## 4. ui の要点

- OpenSheetMusicDisplay で MusicXML を描画する。
- カーソルは音符単位の遷移ではなく、拍位置(連続値)から座標を計算して描く。実装は `ui/src/cursor.ts`: 読み込み時に OSMD の Cursor を末尾まで進めて (拍, x 座標, 小節番号) の表を作り、実行時はその表を線形補間する。OSMD のタイムスタンプは全音符 = 1.0 なので 4 倍して拍にする。反復で後ろへ飛ぶ区間は補間せず、飛ぶ瞬間まで留まる。
- 横 1 段の連続スクロール。現在位置を画面の 1/3 に固定し、次の小節が常に見えるようにする。
- confidence をカーソルの不透明度または色で表す。
- セッション後は音符ごとの音程偏差(セント)・タイミング偏差(ms)を譜面に重ねる。

## 5. 楽譜データの流れ

MuseScore プロジェクト(`score.mscz`)を唯一のソースとし、MusicXML と MIDI を書き出して同じフォルダに置く(`scores/<song_id>/`、構成は `scores/README.md`)。

```
scores/<id>/score.mscz ─ MuseScore4 -o score.mxl ─→ ui(OSMD)/ core(partitura)
                       └ MuseScore4 -o score.mid ─→ core(伴奏)
```

core は `scores/` を走査して曲一覧を作り(`songs.py`)、UI は `song.json` の表示名でプルダウンを出す。MuseScore の MIDI は反復記号を展開して書き出されるので、UI 側のカーソル表は OSMD の `CurrentEnrolledTimestamp`(展開後の時刻)を拍として使う。

## 6. 検証戦略

1. **リプレイ基盤を最初に作る。** 録音に対してオフラインで follower を走らせ、結果を数値化する。
2. 自分の演奏を難易度・テンポ別に録音し、オフライン DTW で正解アライメントを作って目視修正する。弾き直し・飛ばし・停止・明確なミスを意図的に含む録音も用意する。
3. 譜面 UI を追従より先に完成させ、「MIDI 固定テンポ再生でカーソルが動く」状態をデバッグビューアとして使う。

## 7. 精度・遅延の目標(オンライン追従が動いてから測る)

| 指標 | 目標 |
| --- | --- |
| 検出レイテンシ(発音 → 位置更新) | 30〜80 ms |
| 位置誤差 中央値(通常演奏) | < 50 ms |
| 位置誤差 95 パーセンタイル | < 150 ms |
| ロストからの再捕捉 | < 2 秒 |
| 完走率(意図的なミスを含む) | > 95 % |

Python プロトタイプ段階では、これらを満たすことより「リプレイで再現でき、数値が取れる」ことを優先する。

## 8. 将来拡張(設計方針のみ)

- オーディオ伴奏 + タイムストレッチ(Signalsmith Stretch など)。`IAccompanimentSource` 相当の抽象で MIDI と同じ扱いにする。
- ゲームモード: follower と judge は要求が逆(寛容 vs 厳格)なので分離する。フレーズ単位で採点し、音程は連続的に評価する。偽陰性を強く避ける。
- 反復記号の展開(partitura の unfold + 表示位置への対応表)。
- スピーカー運用時の回り込み対策。
