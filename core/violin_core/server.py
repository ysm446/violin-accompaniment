"""WebSocket サーバ。UI へ {position, tempo, confidence} を配信し、制御コマンドを受ける。

core → ui:
  接続直後に 1 回:
    {"type": "songs", "songs": [{"id", "name", "xml"}, ...], "current": "<id>"}
    {"type": "devices", "devices": [{"id", "name", "hostapi", "samplerate"}, ...], "current": <id|null>}
  約 30 Hz:
    {"type": "state", "position": 12.5, "tempo": 70.0, "confidence": 1.0,
     "playing": true, "rate": 1.0, "length": 332.0, "song": "<id>", "time": <unix秒>,
     "audio": {"source", "level_db", "chroma": [12], "flux", "latency_ms", "frames", "overruns",
               "recording", "recording_dir"},
     "follow": {"position", "tempo", "confidence", "raw_position", "active", "enabled"}}
ui → core:
  {"cmd": "play"} / {"cmd": "stop"} / {"cmd": "reset"}
  {"cmd": "seek", "beat": 32.0} / {"cmd": "rate", "value": 0.9}
  {"cmd": "load", "song": "<id>"}
  {"cmd": "input", "device": <id|null>}      入力デバイス切り替え(null で入力停止)
  {"cmd": "record", "on": true|false}        セッション記録の開始 / 停止
  {"cmd": "sessions"}                        記録セッション一覧を要求 → {"type": "sessions", ...}
  {"cmd": "follow", "on": true|false}        追従モード: 追従器の位置に伴奏を同期する
  {"cmd": "follow_reset"}                    追従器を先頭に戻す
  {"cmd": "analyze", "session": "<id>"}      記録を楽譜と整列 → {"type": "analysis", ...}(analysis.json の内容)
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path

import numpy as np
import websockets
from websockets.asyncio.server import ServerConnection, serve

from .align import analyze_session
from .analysis import AnalysisEngine
from .follower import OnlineFollower
from .score_notes import load_part_notes
from .audio import InputDevice, MicSource
from .midi_score import load_midi
from .player import MidiPlayer
from .songs import Song

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ALLOWED_ORIGINS = [None, "file://", re.compile(r"^http://(?:localhost|127\.0\.0\.1):\d+$")]


class StateServer:
    def __init__(
        self,
        player: MidiPlayer,
        songs: list[Song],
        current: str | None,
        exclude_tracks: tuple[str, ...],
        analysis: AnalysisEngine | None = None,
        devices: list[InputDevice] | None = None,
        current_device: int | None = None,
        recordings_dir: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 8765,
        hz: float = 30.0,
    ):
        self.player = player
        self.songs = {s.id: s for s in songs}
        self.current = current
        self.exclude_tracks = exclude_tracks
        self.analysis = analysis
        self.devices = devices or []
        self.current_device = current_device
        self._recordings_dir = recordings_dir
        self.follower: OnlineFollower | None = None
        self.follow_enabled = False
        self._follow_rate = 1.0
        self._base_rate = 1.0  # 追従 ON 中の目標テンポ(楽譜テンポ比)
        # 保守的な同期のための状態(フレーム数は約 94 fps 基準)
        self._stable_frames = 0  # 確信度の高い有音フレームが連続した数(開始待ち)
        self._disagree_frames = 0  # 追従位置と再生位置が大きくずれたフレームの連続数
        self._last_seek_time = 0.0
        self._seek_count = 0
        self.follow_settings = {
            "start_wait_sec": 1.0,  # 位置が確実になってから伴奏を出すまでの待ち(この間に位置が飛べばやり直し)
            "uniqueness_min": 0.3,  # 楽譜上の他の場所より十分良いこと(誤った場所で鳴らさない)
            "seek_wait_sec": 0.6,  # ずれがこの時間続いたらシーク
            "seek_refractory_sec": 1.5,  # シーク後にシークしない時間
            "seek_threshold_beats": 1.0,
            "confidence_min": 0.5,
            "rate_gain": 0.15,
            "rate_min": 0.5,
            "rate_max": 1.6,
            "silence_stop_sec": 1.0,  # 休符でない無音がこの時間続いたら伴奏を止める
            "fade_in_sec": 1.0,  # 追従モードで伴奏を始めるときのフェードイン
            "tempo_wait_sec": 2.0,  # テンポの実測が立つまで伴奏の開始をこの時間まで待つ
            "local_beats": 16.0,  # 通しモード: 追従器が探す範囲(現在位置 ±拍)。None なら楽譜全体(どこからでも)
            "fade_out_sec": 0.3,  # 追従モードで伴奏を止めるときのフェードアウト
            "rest_grace_sec": 1.0,  # 休符中は休符の長さ + この時間まで待つ
        }
        self._silence_since: float | None = None
        self._rest_since: float | None = None
        self._last_follow_pos = 0.0
        self.follow_mode = "off"  # off / waiting / playing
        self.sync_mode = "wait"  # wait: バイオリンを待って入る(追従) / ensemble: 前奏から鳴らしてテンポを合わせる(合奏)
        self._setup_follower()
        if self.analysis is not None:
            self.analysis.listeners.append(self._on_frame)
        self.host = host
        self.port = port
        self.interval = 1.0 / hz
        self._clients: set[ServerConnection] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- 追従 ----

    def _setup_follower(self) -> None:
        song = self.songs.get(self.current or "")
        if song is None or self.analysis is None:
            self.follower = None
            return
        notes = load_part_notes(song.midi)
        fps = self.analysis.sr / self.analysis.hop
        self.follower = OnlineFollower(notes, self.player.score.score_bpm, fps, local_beats=self.follow_settings.get("local_beats"))
        self.follower.set_base_tempo(self.player.score.score_bpm * getattr(self, '_base_rate', 1.0))

    def _start_ensemble(self) -> None:
        p = self.player
        p.stop()
        start = float(p.position)
        if self.follower is not None:
            self.follower.reset(start)
        self._follow_rate = float(np.clip(self._base_rate, self.follow_settings["rate_min"], self.follow_settings["rate_max"]))
        p.set_rate(self._follow_rate)
        p.play()
        self._last_seek_time = time.perf_counter()
        self.follow_mode = "playing"

    def _rest_remaining_sec(self, position: float, p: MidiPlayer) -> float:
        """追従対象パートの現在の休符が終わるまでの秒数(楽譜テンポ換算)。休符でなければ 0。"""
        f = self.follower
        if f is None:
            return 0.0
        j = min(int(round(position / f.ref_step)), len(f.ref) - 1)
        if not f.ref_sil[j]:
            return 0.0
        k = j
        while k < len(f.ref) and f.ref_sil[k]:
            k += 1
        beats = (k - j) * f.ref_step
        return beats * 60.0 / max(p.score.score_bpm, 1e-6)

    def _on_frame(self, frame, adc_time: float) -> None:
        """analysis スレッドから hop ごとに呼ばれる。追従器を進め、追従モードなら伴奏を同期する。"""
        f = self.follower
        if f is None:
            return
        st = f.process(frame.chroma, frame.flux, frame.level_db, t=adc_time)
        if not self.follow_enabled:
            return
        p = self.player
        cfg = self.follow_settings
        fps = self.analysis.sr / self.analysis.hop if self.analysis else 94.0
        confident = st.active and st.confidence >= cfg["confidence_min"]
        certain = confident and not st.lost and st.uniqueness >= cfg["uniqueness_min"]
        # テンポは奏者の実測から自動で決める: 推定が立つまで(最大 tempo_wait_sec)は待ってから伴奏に入る
        if certain and not st.tempo_ready and self._stable_frames < (cfg["start_wait_sec"] + cfg["tempo_wait_sec"]) * fps:
            certain_now = self._stable_frames < cfg["start_wait_sec"] * fps  # 安定は数えるが開始はしない
        else:
            certain_now = True
        # 安定判定: 確実で、かつ位置が直前の予測から 1 拍以上飛んでいないこと
        if certain and abs(st.position - self._last_follow_pos) < 1.0 + abs(st.tempo) / 60.0 * 0.2:
            self._stable_frames += 1
        else:
            self._stable_frames = 0
        self._last_follow_pos = st.position
        now = time.perf_counter()

        if self.sync_mode == "ensemble":
            self._ensemble_frame(st, p, cfg, fps, confident, now)
            return

        # 無音の扱い: 休符でない無音が続いたら伴奏を止める(休符なら待つ)
        if st.active:
            self._silence_since = None
        else:
            if self._silence_since is None:
                self._silence_since = now
            silence = now - self._silence_since
            if p.playing:
                limit = cfg["silence_stop_sec"]
                if st.in_rest:
                    limit = self._rest_remaining_sec(st.position, p) + cfg["rest_grace_sec"]
                if silence >= limit or st.lost:
                    p.fade_stop(cfg["fade_out_sec"])
                    self._follow_rate = 1.0
                    p.set_rate(1.0)
                    self.follow_mode = "waiting"
            return

        if not p.playing:
            self.follow_mode = "waiting"
            # 弾き始めてしばらく安定してから伴奏を出す
            if self._stable_frames >= cfg["start_wait_sec"] * fps and certain_now:
                p.seek(st.position)
                # 開始時のレートは推定テンポから(ゆっくり弾いているなら最初からゆっくり入る)
                self._follow_rate = float(np.clip(st.tempo / max(p.score.score_bpm, 1e-6), cfg["rate_min"], cfg["rate_max"]))
                p.set_rate(self._follow_rate)
                p.play(fade_in_sec=cfg["fade_in_sec"])
                self._last_seek_time = now
                self.follow_mode = "playing"
            return
        self.follow_mode = "playing"

        if not confident:
            # 確信がないときは飛びつかず、レートを 1.0 に戻していく
            self._disagree_frames = 0
            self._follow_rate += float(np.clip(1.0 - self._follow_rate, -0.01, 0.01))
            p.set_rate(self._follow_rate)
            return

        error = st.position - p.position  # 拍。正なら伴奏が遅れている
        if abs(error) > cfg["seek_threshold_beats"]:
            # ずれが続いたら、飛びつく(シーク)のではなく伴奏を止めて、確実になってから再開する
            self._disagree_frames += 1
            if self._disagree_frames >= cfg["seek_wait_sec"] * fps:
                p.fade_stop(cfg["fade_out_sec"])
                p.set_rate(1.0)
                self._follow_rate = 1.0
                self._seek_count += 1
                self._disagree_frames = 0
                self._stable_frames = 0
                self.follow_mode = "waiting"
            return
        self._disagree_frames = 0
        # 連続的なレート変調: テンポ比 + 位置誤差の比例項、変化率を制限(Phase 4 で先読みに置き換える)
        target = st.tempo / max(p.score.score_bpm, 1e-6) + cfg["rate_gain"] * error
        target = float(np.clip(target, cfg["rate_min"], cfg["rate_max"]))
        self._follow_rate += float(np.clip(target - self._follow_rate, -0.01, 0.01))
        p.set_rate(self._follow_rate)

    def _ensemble_frame(self, st, p, cfg, fps, confident: bool, now: float) -> None:
        """合奏モード: 伴奏は止めない。バイオリンが鳴っていなければ追従器を伴奏の位置に同期させ、
        鳴って確信があれば伴奏のレートを奏者のテンポと位置誤差に合わせる。大きくずれたままなら位置を合わせ直す。"""
        if not p.playing:
            self.follow_mode = "waiting"
            return
        self.follow_mode = "playing"
        if not confident:
            # 前奏・休符・確信がない間: 追従器の位置は伴奏に合わせ、レートは目標テンポへ戻していく
            self._disagree_frames = 0
            if not st.active:
                self.follower.sync_position(p.position)
            target = float(np.clip(self._base_rate, cfg["rate_min"], cfg["rate_max"]))
            self._follow_rate += float(np.clip(target - self._follow_rate, -0.005, 0.005))
            p.set_rate(self._follow_rate)
            return
        error = st.position - p.position  # 拍。正なら伴奏が遅れている
        if abs(error) > cfg["seek_threshold_beats"]:
            self._disagree_frames += 1
            if self._disagree_frames >= cfg["seek_wait_sec"] * fps and now - self._last_seek_time >= cfg["seek_refractory_sec"]:
                p.seek(st.position)  # 合奏では止めずに位置を合わせ直す
                self._last_seek_time = now
                self._seek_count += 1
                self._disagree_frames = 0
            return
        self._disagree_frames = 0
        target = st.tempo / max(p.score.score_bpm, 1e-6) + cfg["rate_gain"] * error
        target = float(np.clip(target, cfg["rate_min"], cfg["rate_max"]))
        self._follow_rate += float(np.clip(target - self._follow_rate, -0.01, 0.01))
        p.set_rate(self._follow_rate)

    # ---- 記録セッション ----

    @property
    def recordings_dir(self) -> Path | None:
        if self._recordings_dir is not None:
            return self._recordings_dir
        if self.analysis is None or self.analysis.recorder is None:
            return None
        return self.analysis.recorder.root

    def sessions_message(self) -> dict:
        items = []
        root = self.recordings_dir
        if root is not None and root.is_dir():
            for d in sorted(root.iterdir(), reverse=True):
                meta = d / "meta.json"
                if not meta.exists():
                    continue
                try:
                    m = json.loads(meta.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                items.append({
                    "id": d.name,
                    "song": m.get("song"),
                    "started_at": m.get("started_at"),
                    "duration_sec": round(float(m.get("duration_sec", 0.0)), 1),
                    "analyzed": (d / "analysis.json").exists(),
                })
        return {"type": "sessions", "sessions": items}

    def _analyze_async(self, session_id: str) -> None:
        session = self._session_path(session_id)
        if session is None or not (session / "meta.json").exists():
            self._post({"type": "analysis", "error": f"セッションが見つかりません: {session_id}"})
            return

        def work() -> None:
            try:
                meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
                song = self.songs.get(meta.get("song") or "")
                if song is None:
                    raise ValueError(f"曲 {meta.get('song')} が見つかりません")
                result = analyze_session(session, song.midi)
                (session / "analysis.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                self._post({"type": "analysis", **result})
                self._post(self.sessions_message())
            except Exception as e:  # noqa: BLE001
                print(f"[core] analyze error: {e}")
                self._post({"type": "analysis", "error": str(e), "session": session_id})

        self._post({"type": "analysis", "session": session_id, "status": "running"})
        threading.Thread(target=work, name="analyze", daemon=True).start()

    def _session_path(self, session_id: str) -> Path | None:
        """recordings 直下に実在する安全なセッション ID だけを受け付ける。"""
        root = self.recordings_dir
        if root is None or not SESSION_ID_RE.fullmatch(session_id):
            return None
        root = root.resolve()
        session = (root / session_id).resolve()
        if session.parent != root or not session.is_dir():
            return None
        return session

    def _post(self, msg: dict) -> None:
        """他スレッドから全クライアントへ送る。"""
        if self._loop is None:
            return
        data = json.dumps(msg, ensure_ascii=False)
        asyncio.run_coroutine_threadsafe(self._broadcast(data), self._loop)

    async def _broadcast(self, data: str) -> None:
        await asyncio.gather(*(self._safe_send(c, data) for c in list(self._clients)))

    def state(self) -> dict:
        p = self.player
        st = {
            "type": "state",
            "position": p.position,
            "tempo": p.tempo,
            "score_bpm": float(self.player.score.score_bpm),
            "confidence": 1.0,  # Phase 0: 固定テンポ再生なので常に確定
            "playing": p.playing,
            "rate": p.rate,
            "length": p.score.length_beats,
            "song": self.current,
            "metronome": bool(getattr(self.player, "metronome", False)),
            "volume": float(getattr(self.player, "volume", 1.0)),
            "metronome_volume": float(getattr(self.player, "metronome_volume", 1.0)),
            "time": time.time(),  # 送信時刻(遅延計測用)
        }
        if self.analysis is not None:
            st["audio"] = self.analysis.status.to_dict()
        if self.follower is not None:
            st["follow"] = {**self.follower.current.to_dict(), "enabled": self.follow_enabled, "seeks": self._seek_count,
                            "mode": self.follow_mode if self.follow_enabled else "off", "sync_mode": self.sync_mode}
        return st

    def songs_message(self) -> dict:
        return {"type": "songs", "songs": [s.to_dict() for s in self.songs.values()], "current": self.current}

    def devices_message(self) -> dict:
        return {"type": "devices", "devices": [d.to_dict() for d in self.devices], "current": self.current_device}

    async def _handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        try:
            await ws.send(json.dumps(self.songs_message()))
            await ws.send(json.dumps(self.devices_message()))
            await ws.send(json.dumps(self.sessions_message(), ensure_ascii=False))
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                try:
                    reply = self._handle_command(msg)
                    if reply is not None:
                        await ws.send(json.dumps(reply, ensure_ascii=False))
                except Exception as e:  # noqa: BLE001
                    print(f"[core] command error {msg}: {e}")
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)

    def _handle_command(self, msg: dict) -> dict | None:
        cmd = msg.get("cmd")
        p = self.player
        if cmd == "play":
            if self.follow_enabled and self.sync_mode == "ensemble":
                # 合奏: 今の位置(曲頭または譜面クリックの位置)から前奏を含めて鳴らし始める
                self._start_ensemble()
            elif self.follow_enabled:
                # 追従中は _on_frame の確実性ゲートだけが再生を開始できる。
                p.stop()
                self.follow_mode = "waiting"
            else:
                p.play()
        elif cmd == "stop":
            p.stop()
        elif cmd == "reset":
            p.stop()
            p.seek(0.0)
        elif cmd == "seek":
            beat = float(msg.get("beat", 0.0))
            if self.follow_enabled and self.sync_mode == "ensemble":
                # 合奏中の譜面クリック = そこから鳴らし直す
                p.stop()
                p.seek(beat)
                self._start_ensemble()
            elif self.follow_enabled:
                # 追従 ON 中の譜面クリック = ここから弾く(追従器の位置を置き、伴奏は確実になってから入る)
                if self.follower is not None:
                    self.follower.reset(beat)
                p.stop()
                p.seek(beat)
                self._stable_frames = 0
                self.follow_mode = "waiting"
            else:
                p.seek(beat)
        elif cmd == "rate":
            value = float(msg.get("value", 1.0))
            if self.follow_enabled:
                # 追従 ON 中は「目標テンポ」: 追従器の初期テンポと推定範囲の中心にする
                self._base_rate = value
                if self.follower is not None:
                    self.follower.set_base_tempo(p.score.score_bpm * value)
            else:
                p.set_rate(value)
        elif cmd == "load":
            self.load_song(str(msg.get("song", "")))
        elif cmd == "input":
            self.set_input(msg.get("device"))
        elif cmd == "record":
            self.set_recording(bool(msg.get("on", False)))
            return self.sessions_message() if not msg.get("on") else None
        elif cmd == "sessions":
            return self.sessions_message()
        elif cmd == "ensemble":
            # 合奏モードの開始/終了。開始すると前奏からすぐ鳴る
            on = bool(msg.get("on", False))
            self.follow_enabled = on
            self.sync_mode = "ensemble" if on else "wait"
            self._stable_frames = 0
            self._disagree_frames = 0
            self._silence_since = None
            if on:
                self._base_rate = float(p.rate)
                if self.follower is not None:
                    self.follower.set_base_tempo(p.score.score_bpm * self._base_rate)
                self._start_ensemble()
            else:
                p.stop()
                p.set_rate(1.0)
                self._follow_rate = 1.0
                self.follow_mode = "off"
        elif cmd == "follow":
            self.sync_mode = "wait"
            self.follow_enabled = bool(msg.get("on", False))
            self._stable_frames = 0
            self._disagree_frames = 0
            self._silence_since = None
            self.follow_mode = "waiting" if self.follow_enabled else "off"
            p.stop()
            if self.follow_enabled:
                self._base_rate = float(p.rate)  # 追従に入る時点のレートを目標テンポとして引き継ぐ
                if self.follower is not None:
                    self.follower.set_base_tempo(p.score.score_bpm * self._base_rate)
            p.set_rate(1.0)
            self._follow_rate = 1.0
        elif cmd == "follow_reset":
            if self.follower is not None:
                self.follower.reset()
            p.stop()
            p.seek(0.0)
        elif cmd == "metronome":
            p.metronome = bool(msg.get("on", False))
        elif cmd == "volume":
            p.set_volume(float(msg.get("value", 1.0)))
        elif cmd == "metronome_volume":
            p.metronome_volume = max(0.0, min(1.0, float(msg.get("value", 1.0))))
        elif cmd == "analyze":
            self._analyze_async(str(msg.get("session", "")))
        return None

    def load_song(self, song_id: str) -> None:
        song = self.songs.get(song_id)
        if song is None:
            print(f"[core] 不明な曲 id: {song_id}")
            return
        if song_id == self.current:
            self.player.stop()
            self.player.seek(0.0)
            if self.follower is not None:
                self.follower.reset()
            self._stable_frames = 0
            self._disagree_frames = 0
            self.follow_mode = "waiting" if self.follow_enabled else "off"
            return
        score = load_midi(song.midi, exclude_tracks=self.exclude_tracks)
        self.player.load(score)
        self.current = song_id
        self._setup_follower()
        self._stable_frames = 0
        self._disagree_frames = 0
        self.follow_mode = "waiting" if self.follow_enabled else "off"
        print(f"[core] load: {song.midi.name} events={len(score.events)} length={score.length_beats:.1f} beats")

    def set_input(self, device) -> None:
        if self.analysis is None:
            return
        if device is None:
            self.analysis.set_source(None)
            self.current_device = None
            return
        device = int(device)
        if not any(d.id == device for d in self.devices):
            print(f"[core] 不明な入力デバイス: {device}")
            return
        try:
            self.analysis.set_source(MicSource(device, sr=self.analysis.sr, blocksize=self.analysis.hop))
            self.current_device = device
        except Exception as e:  # noqa: BLE001
            print(f"[core] 入力デバイスを開けません ({device}): {e}")
            self.analysis.set_source(None)
            self.current_device = None

    def set_recording(self, on: bool) -> None:
        if self.analysis is None or self.analysis.recorder is None:
            return
        rec = self.analysis.recorder
        if on and not rec.active:
            rec.start({"song": self.current, "input": self.analysis.status.source, "n_fft": self.analysis.n_fft, "hop": self.analysis.hop})
        elif not on and rec.active:
            rec.stop()

    async def _broadcast_loop(self) -> None:
        while True:
            st = self.state()
            if self.analysis is not None and self.analysis.recorder is not None and self.analysis.recorder.active:
                self.analysis.recorder.write_state({k: v for k, v in st.items() if k in ("position", "tempo", "confidence", "playing", "rate")})
            if self._clients:
                data = json.dumps(st)
                await asyncio.gather(*(self._safe_send(c, data) for c in list(self._clients)))
            await asyncio.sleep(self.interval)

    async def _safe_send(self, ws: ServerConnection, data: str) -> None:
        try:
            await ws.send(data)
        except websockets.ConnectionClosed:
            self._clients.discard(ws)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        async with serve(self._handler, self.host, self.port, origins=ALLOWED_ORIGINS):
            print(f"[core] ws://{self.host}:{self.port} で待機中")
            await self._broadcast_loop()
