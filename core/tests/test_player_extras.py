"""プレイヤーのフェードとメトロノーム。"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

import mido

from violin_core.midi_score import Click, Meter, MidiEvent, MidiScore


class FakeMidiOut:
    def __init__(self) -> None:
        self.messages: list[tuple[int, ...]] = []
        self.lock = threading.Lock()

    def get_ports(self):
        return ["test"]

    def open_port(self, _i):
        pass

    def close_port(self):
        pass

    def send_message(self, msg):
        with self.lock:
            self.messages.append(tuple(msg))


def make_player(score, fake_out):
    from violin_core import player as player_module

    with patch.object(player_module.rtmidi, "MidiOut", return_value=fake_out):
        return player_module.MidiPlayer(score, tick_interval=0.001)


class ClickGridTests(unittest.TestCase):
    def test_compound_meter_uses_dotted_quarter_and_pickup_is_short(self) -> None:
        score = MidiScore(length_beats=7.0, meters=[Meter(0.0, 1, 8), Meter(0.5, 12, 8)])
        clicks = score.clicks()
        # 1/8 の弱起は 1 拍に満たないので刻まず、最初の小節頭(0.5)から
        self.assertEqual(clicks[0], Click(0.5, True))
        self.assertEqual([c.beat for c in clicks[:4]], [0.5, 2.0, 3.5, 5.0])
        self.assertEqual([c.accent for c in clicks[:5]], [True, False, False, False, True])

    def test_default_meter_is_four_four(self) -> None:
        clicks = MidiScore(length_beats=8.0).clicks()
        self.assertEqual([c.beat for c in clicks], [0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual([c.accent for c in clicks], [True, False, False, False] * 2)


class PlayerFadeAndMetronomeTests(unittest.TestCase):
    def test_fade_in_ramps_expression_up_to_full(self) -> None:
        fake = FakeMidiOut()
        score = MidiScore(events=[MidiEvent(0.0, mido.Message("note_on", note=60, velocity=100))], length_beats=400.0,
                          tempos=[])
        player = make_player(score, fake)
        try:
            player.play(fade_in_sec=0.1)
            time.sleep(0.3)
            with fake.lock:
                expr = [m[2] for m in fake.messages if m[0] & 0xF0 == 0xB0 and m[1] == 11]
            self.assertEqual(expr[0], 0)
            self.assertEqual(expr[-1], 127)
            self.assertEqual(expr, sorted(expr))
            self.assertGreater(len(set(expr)), 5)
        finally:
            player.close()

    def test_fade_stop_stops_after_reaching_silence(self) -> None:
        fake = FakeMidiOut()
        player = make_player(MidiScore(length_beats=400.0), fake)
        try:
            player.play()
            player.fade_stop(0.1)
            self.assertTrue(player.playing)
            time.sleep(0.3)
            self.assertFalse(player.playing)
            with fake.lock:
                expr = [m[2] for m in fake.messages if m[0] & 0xF0 == 0xB0 and m[1] == 11]
                sound_off = [m for m in fake.messages if m[0] & 0xF0 == 0xB0 and m[1] == 120]
            self.assertEqual(expr[-1], 0)
            self.assertTrue(sound_off)
        finally:
            player.close()

    def test_metronome_clicks_on_beats_with_accent(self) -> None:
        fake = FakeMidiOut()
        # 4/4、120 bpm で 3 拍ぶん再生する
        player = make_player(MidiScore(length_beats=3.0, tempos=[]), fake)
        try:
            player.metronome = True
            player.play()
            time.sleep(1.8)
            with fake.lock:
                clicks = [m for m in fake.messages if m[0] == 0x99 and m[2] > 0]
            self.assertEqual([m[1] for m in clicks], [75, 76, 76])
        finally:
            player.close()

    def test_volume_scales_expression_and_click_velocity(self) -> None:
        fake = FakeMidiOut()
        player = make_player(MidiScore(length_beats=2.0), fake)
        try:
            player.set_volume(0.5)
            player.metronome = True
            player.metronome_volume = 0.5
            player.play()
            time.sleep(0.6)
            with fake.lock:
                expr = [m[2] for m in fake.messages if m[0] & 0xF0 == 0xB0 and m[1] == 11]
                clicks = [m for m in fake.messages if m[0] == 0x99 and m[2] > 0]
            self.assertEqual(expr[-1], 64)
            self.assertTrue(clicks)
            self.assertEqual(clicks[0][2], 64)
        finally:
            player.close()

    def test_metronome_off_sends_no_clicks(self) -> None:
        fake = FakeMidiOut()
        player = make_player(MidiScore(length_beats=2.0), fake)
        try:
            player.play()
            time.sleep(1.2)
            with fake.lock:
                clicks = [m for m in fake.messages if m[0] == 0x99]
            self.assertEqual(clicks, [])
        finally:
            player.close()


if __name__ == "__main__":
    unittest.main()
