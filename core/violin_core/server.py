"""WebSocket サーバ。UI へ {position, tempo, confidence} を配信し、制御コマンドを受ける。

core → ui:
  接続直後に 1 回:
    {"type": "songs", "songs": [{"id", "name", "xml"}, ...], "current": "<id>"}
    {"type": "devices", "devices": [{"id", "name", "hostapi", "samplerate"}, ...], "current": <id|null>}
  約 30 Hz:
    {"type": "state", "position": 12.5, "tempo": 70.0, "confidence": 1.0,
     "playing": true, "rate": 1.0, "length": 332.0, "song": "<id>", "time": <unix秒>,
     "audio": {"source", "level_db", "chroma": [12], "flux", "latency_ms", "frames", "overruns",
               "recording", "recording_dir"}}
ui → core:
  {"cmd": "play"} / {"cmd": "stop"} / {"cmd": "reset"}
  {"cmd": "seek", "beat": 32.0} / {"cmd": "rate", "value": 0.9}
  {"cmd": "load", "song": "<id>"}
  {"cmd": "input", "device": <id|null>}      入力デバイス切り替え(null で入力停止)
  {"cmd": "record", "on": true|false}        セッション記録の開始 / 停止
"""

from __future__ import annotations

import asyncio
import json
import time

import websockets
from websockets.asyncio.server import ServerConnection, serve

from .analysis import AnalysisEngine
from .audio import InputDevice, MicSource
from .midi_score import load_midi
from .player import MidiPlayer
from .songs import Song


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
        self.host = host
        self.port = port
        self.interval = 1.0 / hz
        self._clients: set[ServerConnection] = set()

    def state(self) -> dict:
        p = self.player
        st = {
            "type": "state",
            "position": p.position,
            "tempo": p.tempo,
            "confidence": 1.0,  # Phase 0: 固定テンポ再生なので常に確定
            "playing": p.playing,
            "rate": p.rate,
            "length": p.score.length_beats,
            "song": self.current,
            "time": time.time(),  # 送信時刻(遅延計測用)
        }
        if self.analysis is not None:
            st["audio"] = self.analysis.status.to_dict()
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
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                try:
                    self._handle_command(msg)
                except Exception as e:  # noqa: BLE001
                    print(f"[core] command error {msg}: {e}")
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)

    def _handle_command(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        p = self.player
        if cmd == "play":
            p.play()
        elif cmd == "stop":
            p.stop()
        elif cmd == "reset":
            p.stop()
            p.seek(0.0)
        elif cmd == "seek":
            p.seek(float(msg.get("beat", 0.0)))
        elif cmd == "rate":
            p.set_rate(float(msg.get("value", 1.0)))
        elif cmd == "load":
            self.load_song(str(msg.get("song", "")))
        elif cmd == "input":
            self.set_input(msg.get("device"))
        elif cmd == "record":
            self.set_recording(bool(msg.get("on", False)))

    def load_song(self, song_id: str) -> None:
        song = self.songs.get(song_id)
        if song is None:
            print(f"[core] 不明な曲 id: {song_id}")
            return
        if song_id == self.current:
            self.player.stop()
            self.player.seek(0.0)
            return
        score = load_midi(song.midi, exclude_tracks=self.exclude_tracks)
        self.player.load(score)
        self.current = song_id
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
        async with serve(self._handler, self.host, self.port):
            print(f"[core] ws://{self.host}:{self.port} で待機中")
            await self._broadcast_loop()
