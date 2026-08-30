from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mido
import numpy as np
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from violin_core.analysis import AnalysisEngine
from violin_core.midi_score import MidiEvent, MidiScore
from violin_core.recorder import SessionRecorder
from violin_core.server import ALLOWED_ORIGINS, StateServer


class FakePlayer:
    def __init__(self) -> None:
        self.score = MidiScore(length_beats=16.0)
        self.playing = True
        self.stop_calls = 0
        self.rate = 1.0

    def stop(self) -> None:
        self.playing = False
        self.stop_calls += 1

    def play(self) -> None:
        self.playing = True

    def set_rate(self, rate: float) -> None:
        self.rate = rate


class FakeMidiOut:
    def __init__(self) -> None:
        self.messages: list[tuple[int, ...]] = []
        self.note_started = threading.Event()
        self.release_note = threading.Event()
        self._blocked = False

    def get_ports(self) -> list[str]:
        return ["test"]

    def open_port(self, _index: int) -> None:
        pass

    def close_port(self) -> None:
        pass

    def send_message(self, msg: list[int]) -> None:
        is_note_on = msg[0] & 0xF0 == 0x90 and msg[2] > 0
        if is_note_on and not self._blocked:
            self._blocked = True
            self.note_started.set()
            self.release_note.wait(timeout=1.0)
        self.messages.append(tuple(msg))


class ReliabilityTests(unittest.TestCase):
    def test_follow_mode_is_the_only_path_that_can_start_playback(self) -> None:
        player = FakePlayer()
        server = StateServer(player, [], None, ())

        server._handle_command({"cmd": "follow", "on": True})
        self.assertFalse(player.playing)
        server._handle_command({"cmd": "play"})

        self.assertFalse(player.playing)
        self.assertEqual(server.follow_mode, "waiting")

    def test_ensemble_mode_starts_intro_immediately(self) -> None:
        player = FakePlayer()
        player.position = 0.0
        player.seek = lambda beat: setattr(player, "position", beat)
        server = StateServer(player, [], None, ())

        server._handle_command({"cmd": "ensemble", "on": True})
        self.assertTrue(player.playing)
        self.assertEqual(server.sync_mode, "ensemble")
        self.assertEqual(server.follow_mode, "playing")

        server._handle_command({"cmd": "ensemble", "on": False})
        self.assertFalse(player.playing)
        self.assertEqual(server.sync_mode, "wait")

    def test_realtime_audio_queue_drops_oldest_blocks(self) -> None:
        engine = AnalysisEngine(sr=48000, n_fft=4096, hop=512)
        engine._source = SimpleNamespace(blocking=False)
        engine._source_generation = 1
        for value in range(12):
            engine._on_block(np.full(512, value, dtype=np.float32), float(value), 1)

        self.assertEqual(engine._queue.qsize(), 8)
        oldest, _adc_time, _generation = engine._queue.get_nowait()
        self.assertEqual(float(oldest[0]), 4.0)
        self.assertEqual(engine.status.overruns, 4)

    def test_recordings_started_in_same_second_get_unique_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = SessionRecorder(tmp, 48000)
            first = recorder.start({"song": "test"})
            recorder.stop()
            second = recorder.start({"song": "test"})
            recorder.stop()

            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())

    def test_session_path_cannot_escape_recordings_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "20260830-120000-000001"
            valid.mkdir()
            server = StateServer(FakePlayer(), [], None, (), recordings_dir=root)

            self.assertEqual(server._session_path(valid.name), valid.resolve())
            self.assertIsNone(server._session_path("../outside"))
            self.assertIsNone(server._session_path(str(root.resolve())))

    def test_stop_orders_all_notes_off_after_inflight_note_on(self) -> None:
        from violin_core import player as player_module

        fake_out = FakeMidiOut()
        score = MidiScore(
            events=[MidiEvent(0.0, mido.Message("note_on", note=60, velocity=100))],
            length_beats=8.0,
        )
        with patch.object(player_module.rtmidi, "MidiOut", return_value=fake_out):
            player = player_module.MidiPlayer(score, tick_interval=0.001)
            try:
                player.play()
                self.assertTrue(fake_out.note_started.wait(timeout=1.0))
                stopper = threading.Thread(target=player.stop)
                stopper.start()
                time.sleep(0.02)
                fake_out.release_note.set()
                stopper.join(timeout=1.0)
                self.assertFalse(stopper.is_alive())

                note_on = [i for i, msg in enumerate(fake_out.messages) if msg[0] & 0xF0 == 0x90 and msg[2] > 0]
                sound_off = [i for i, msg in enumerate(fake_out.messages) if msg[0] & 0xF0 == 0xB0 and msg[1] == 120]
                self.assertTrue(note_on)
                self.assertTrue(sound_off)
                self.assertLess(max(note_on), max(sound_off))
            finally:
                fake_out.release_note.set()
                player.close()


class WebSocketOriginTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_local_ui_origins_are_accepted(self) -> None:
        async def handler(ws) -> None:
            await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0, origins=ALLOWED_ORIGINS) as test_server:
            port = test_server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            async with connect(url, origin="file://"):
                pass
            async with connect(url, origin="http://localhost:5199"):
                pass
            with self.assertRaises(InvalidStatus):
                async with connect(url, origin="https://example.invalid"):
                    pass


if __name__ == "__main__":
    unittest.main()
