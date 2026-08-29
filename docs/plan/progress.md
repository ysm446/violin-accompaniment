# progress — 進捗と注意点

作成日時: 2026-08-30 02:52
更新日時: 2026-08-30 14:00

## 現在の状態

Phase 3 完了(オンライン追従 + 伴奏同期の最初の形)。「弾くと伴奏がついてくる」が成立。次は Phase 4(テンポ推定の改善 + 先読みスケジューリング)と Phase 5(耐性)。

## 完了済み

- 2026-08-30: LLM との検討で得た 2 つの仕様ドラフトを `docs/reference/` に保存し、比較と採用方針を [../reference/approach-comparison.md](../reference/approach-comparison.md) にまとめた。
- 2026-08-30: 採用する構成を [../design/architecture.md](../design/architecture.md) に整理し、goals / plan を作成した。
- 2026-08-30: **Phase 0 完了。** `core/`(Python venv、mido + python-rtmidi + websockets)で MIDI を拍クロックで再生し、`{position, tempo, confidence}` を WebSocket 配信。`ui/`(Vite + TypeScript + OSMD 2.1.2)で Vivaldi 春の譜面を横 1 段表示し、連続値カーソルが追従。再生 / 停止 / 最初から / レート変更 / クリックでシークが動作。起動手順は [../../README.md](../../README.md)。
  - 検証: プレイヤークロックの実効 BPM 69.97(期待 70)、WebSocket 配信遅延 ≈ 0 ms、OSMD のカーソル表(1118 点)の拍が MIDI の発音拍と一致(弱起も含む)。Edge(headless)での自動操作とスクリーンショットで確認。

## 完了済み(続き)

- 2026-08-30: **Electron 化とスタンドアローン exe 化。** `ui/electron/main.cjs` が core を子プロセスとして起動・停止。core は PyInstaller(onedir)で `violin_core.exe` に固め、electron-builder で `ui/release/` に portable exe と NSIS インストーラを生成(約 110 MB)。パッケージ版を実際に起動し、同梱 core の自動起動と再生を確認。

- 2026-08-30: **曲選択 UI と楽譜フォルダの再編。** `muse-score/` を `scores/<song_id>/`(score.mscz / score.mxl / score.mid / song.json)に変更。core が `--scores-dir` でフォルダを走査し、接続時に曲一覧を送る。UI はプルダウンで切り替え(`{"cmd":"load"}`)、前回の曲を localStorage に記憶。カーソル表の拍を `CurrentEnrolledTimestamp`(反復展開後)にしたので、反復記号のある曲(ノクターン)も MIDI と揃う。

- 2026-08-30: **Phase 1 完了。** `core` に音声パイプラインを追加: `audio.py`(MicSource / WavSource、入力デバイス列挙)、`features.py`(numpy だけで chroma 12 次元・スペクトラルフラックス・レベル)、`analysis.py`(入力→hop ごとの特徴量、遅延計測、購読者)、`recorder.py`(`recordings/<日時>/` に audio.wav / features.npz / states.jsonl / meta.json)、`replay.py`(記録を再処理してオンラインと比較)。UI に入力デバイス選択・レベルメータ・chroma バー・遅延表示・記録ボタンを追加。
  - 検証: 合成音(A4 ビブラート付き / D5)で chroma の主成分と flux のオンセット位置(誤差 7 ms)を確認。1 ホップの計算 0.12 ms。実マイク(JVCKENWOOD USB Audio、WASAPI 48 kHz)で 2 秒 186 フレーム・取りこぼし 0・パイプライン遅延 22 ms。WAV → 記録 → オフライン再処理で特徴量が完全一致。

- 2026-08-30: **Phase 2 完了。** `score_notes.py`(score.mid の Violin トラックから音符列と参照 chroma)、`align.py`(subsequence DTW、オンセット特徴、YIN による f0、音符ごとの発音時刻・タイミング偏差・音程偏差 → analysis.json)、`synth.py`(正解つき合成演奏の生成)。server に `sessions` / `analyze` コマンド。UI に「振り返り」バー(記録選択 → 解析 → 譜面上に音符ごとのマーカー: 色 = 音程偏差、バー = 早い/遅い、ホバーで詳細)。
  - 検証(合成演奏、テンポ 65〜95 で変動、±15c のずれ、20 ms ジッタ、80 拍): 発音時刻の誤差 中央値 24 ms・p90 46 ms、誤検出 0、音程誤差 中央値 4 セント、タイミング偏差の偏り +3 ms。DTW は 5000 × 5000 で 0.8 秒。
  - 実演奏(`20260830-042258`): 121 音を整列、|音程| 中央値 10.7 セント、|タイミング| 中央値 90 ms。

- 2026-08-30: **Phase 3 完了。** `follower.py`(オンライン追従器)を `analysis.py` の購読者として接続。UI の「◎ 追従」ボタンで追従モード: カーソルが追従器の位置で動き(青)、伴奏は追従位置に同期(誤差 1 拍以上でシーク、以内はテンポ比 + 位置誤差でレートを緩やかに変調)。評価スクリプト `core/tools/eval_follower.py`。
  - 追従器の構造: align.py の DTW 行再帰をフレームごとに進める(1 フレーム 63 µs)。観測 = 予測位置の窓内で累積コストがほぼ最小の列のうち予測に最も近いもの。窓外に大幅に良い列があれば再アンカー(弾き直し・途中開始)。どの列からも定数ペナルティで新しいパスを始められる再スタート項。オンセット(レベル急上昇 + chroma 変化の立ち上がりエッジ)をコストに非対称に加える。無音はノイズ床 +12 dB で判定し、無音中はテンポ外挿・確信度低下。
  - 評価(合成演奏、真値あり): テンポ 65〜95 変動で誤差 中央値 62 ms、途中開始(拍 32)41 ms、弾き直し(48→40)後 61 ms・再アンカー 0.67 s。実演奏の記録(オフライン整列を正解): 中央値 149 ms。
  - **未達**: 同じ音の長い連打(拍 51.5〜64 の B5 ×20 など)で最大 3 秒遅れ、通過後に復帰(p95 2.7 s)。原因は chroma に情報がなく、オンセット項だけでは DTW の累積コストに十分な差が出ないこと。同音連打の音符カウント(`_count_repeated`)を試したが、オンセット項ありの構成では効かず、オンセット項なしの構成(p95 326 ms)では弾き直しの再アンカーが失敗する trade-off が残る。Phase 5 の主題。

- 2026-08-30: ユーザーが実演奏で追従 ON を試し「せわしなく切り替わる」と指摘 → 伴奏の同期を保守的に変更(`server.follow_settings`): 弾き始めて 0.5 秒安定してから開始、ずれが 0.6 秒続いたときだけシーク、シーク後 1.5 秒は再シークしない、確信度が低いときはレートを 1.0 に戻す、レート変調のゲイン 0.15・範囲 0.8〜1.25。追従器の窓外ジャンプにも 6 フレームの猶予。

- 2026-08-30: ユーザーの要望「何も聞こえなければ伴奏を弾かない。適当な場所を弾いたらそこに追従」に合わせて無音の扱いを変更。追従器: 休符でない無音が 1 秒続いたら「見失い(lost)」として位置を保持し、次の音で楽譜全体の最小コスト列を数フレーム確認して再アンカー(`lost_after_sec`)。server: 休符でない無音が 1 秒(休符なら休符の長さ + 1 秒)続いたら伴奏を止め、音が戻って 0.5 秒安定したら再開。state.follow に `mode`(waiting / playing)、`lost`、`in_rest`。UI に「待機中 / 追従中」表示。
  - 見失い後の再探索は 1.0 秒(`lost_listen_sec`)聞いてから楽譜全体の最小コスト列を採る(最初の 1 音だけでは同じ音高の列が 90 以上同点になる)。合成テスト「2 秒の無音を挟んで別々の 8 拍を弾く」8 箇所: 楽譜上で特徴的な楽句は 1.0〜2.7 秒で再アンカー(5/8)。失敗した 3 箇所は同じ音の連打で始まる楽句(B5 連打、休符後の E6 連打)で、音高だけでは楽譜中のどの連打か決まらない(連打の回数を数える Phase 5 の課題)。

## 未完了

- Phase 4: テンポ推定(カルマン)+ 先読みスケジューリング。Phase 5: 同音連打・長い休符・ミスへの耐性。詳細は [plan.md](plan.md)。
- 実演奏で追従モードを再度試し、「落ち着き」と「追従の遅れ」のバランスを見る(`follow_settings` の待ち時間で調整)。
- 実演奏の記録を増やす(弾き直し・停止・明確なミスを含むもの、伴奏を鳴らしながらのもの)。

## 実演奏の記録(Phase 2 の材料)

- `recordings/20260830-042258/`: ヴィヴァルディ春の冒頭を途中まで、53.8 秒(有音 35 秒)、マイク AT2020USB-X、伴奏なし。ピーク −21 dBFS、有音時の中央値 −35 dBFS。chroma は E 長調の構成音(B / F# / G# / E / C# / D#)が支配的で、クロマグラムに音符の帯とフレーズ間の切れ目がはっきり出る。オフライン再処理との差は有音フレームで chroma 0.07 以下・レベル 0.06 dB(無音付近の差は 16bit 量子化と無音判定の境界によるもの)。

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
- 追従器のパラメータは `OnlineFollower.__init__` の既定値(onset_weight 0.5 / near_eps 0.05 / measurement_gain 0.25 / jump_margin 6 / restart_penalty 15)。観測ゲインを上げると悪化する(生の観測が予測に引きずられる循環がある)ので、テンポ推定の改善(Phase 4)とセットで見直す。
- DTW の既知の弱点: 同じ音高の長い音の連打(例: 拍 51.5〜53.5 の B5 ×3)では発音時刻が 1〜1.7 秒ずれることがある(chroma が同一でオンセットのピークが弱い)。オンセット特徴の重みは 1.0(`--onset-weight`)。無音判定はノイズ床 +12 dB と chroma ピーク ≥ 0.45 の両方で行う(ノイズだけのフレームは chroma が平坦で、楽譜のどこにでも半端に一致するため)。
- 発音時刻は約 50 ms 遅れて検出される(窓長 4096 = 85 ms の影響)。相対的なタイミング偏差では相殺されるが、絶対時刻が必要な場面では補正する。
- 入力の遅延計測: WASAPI では `inputBufferAdcTime` がコールバック時刻より未来の値を返して信用できないため、PortAudio の `stream.latency`(このデバイスで 22 ms)を引いて AD 時刻の近似としている。
- chroma は ±60 セントのビブラートだと単フレームで隣の半音に主成分が移ることがある(平均では正しい)。DTW に入れる前に時間方向の平滑化か窓長の見直しを検討する。
- 記録の保存先: 開発時はリポジトリの `recordings/`(gitignore 済み)、パッケージ版は `%APPDATA%/violin-accompaniment/recordings/`。
- `start.bat` は ASCII のみで書く(日本語を入れると cmd.exe が UTF-8 を誤解釈して行が壊れる)。メッセージは英語。
- ポート 5173 は他プロジェクトが使っていることがある。`ui/vite.config.ts` は 5173 固定なので、衝突時は `npx vite --port 5199` などで起動する。
- 追従器は最初から自作しない。`matchmaker` の動作確認を Phase 3 の入口にする。
- リプレイ基盤を後回しにしない。Phase 1 の成果物に含める。
- マイク入力で試す場合はヘッドホンを使い、伴奏の回り込みを避ける。
