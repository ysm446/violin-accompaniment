"""WebSocket サーバ。UI へ {position, tempo, confidence} を配信し、制御コマンドを受ける。

core → ui(約 30 Hz):
  {"type": "state", "position": 12.5, "tempo": 70.0, "confidence": 1.0,
   "playing": true, "rate": 1.0, "length": 332.0}
ui → core:
  {"cmd": "play"} / {"cmd": "stop"} / {"cmd": "reset"}
  {"cmd": "seek", "beat": 32.0} / {"cmd": "rate", "value": 0.9}
"""

from __future__ import annotations

import asyncio
import json
import time

import websockets
from websockets.asyncio.server import ServerConnection, serve

from .player import MidiPlayer


class StateServer:
    def __init__(self, player: MidiPlayer, host: str = "127.0.0.1", port: int = 8765, hz: float = 30.0):
        self.player = player
        self.host = host
        self.port = port
        self.interval = 1.0 / hz
        self._clients: set[ServerConnection] = set()

    def state(self) -> dict:
        p = self.player
        return {
            "type": "state",
            "position": p.position,
            "tempo": p.tempo,
            "confidence": 1.0,  # Phase 0: 固定テンポ再生なので常に確定
            "playing": p.playing,
            "rate": p.rate,
            "length": p.score.length_beats,
            "time": time.time(),  # 送信時刻(遅延計測用)
        }

    async def _handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._handle_command(msg)
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

    async def _broadcast_loop(self) -> None:
        while True:
            if self._clients:
                data = json.dumps(self.state())
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
