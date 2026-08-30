"""拍位置ベースの MIDI プレイヤー。

再生クロックは「拍」で進む。実時間への変換は 楽譜テンポ × rate で行うため、
rate を変えればテンポが変わり、seek すれば位置が変わる。
Phase 4 以降はこの rate と seek を追従器の推定値から駆動する。
"""

from __future__ import annotations

import threading
import time

import rtmidi

from .midi_score import MidiScore

ALL_NOTES_OFF = 123
ALL_SOUND_OFF = 120


class MidiPlayer:
    def __init__(self, score: MidiScore, port_name: str | None = None, tick_interval: float = 0.002):
        self.score = score
        self._out = rtmidi.MidiOut()
        ports = self._out.get_ports()
        if not ports:
            raise RuntimeError("MIDI 出力ポートが見つかりません")
        index = 0
        if port_name:
            matches = [i for i, p in enumerate(ports) if port_name.lower() in p.lower()]
            if not matches:
                raise RuntimeError(f"MIDI 出力ポート '{port_name}' が見つかりません: {ports}")
            index = matches[0]
        self.port_name = ports[index]
        self._out.open_port(index)

        self._tick_interval = tick_interval
        self._lock = threading.Lock()
        # MIDI 出力は再生スレッドと制御スレッドの双方から触る。
        # generation が変わった送信バッチは破棄し、stop/seek 後に古い note_on が
        # ALL_NOTES_OFF を追い越さないよう send_lock で順序を直列化する。
        self._send_lock = threading.Lock()
        self._generation = 0
        self._position = 0.0  # 拍
        self._rate = 1.0
        self._playing = False
        self._next_event = 0
        self._stop_flag = threading.Event()
        self._thread = threading.Thread(target=self._run, name="midi-player", daemon=True)
        self._thread.start()

    # ---- 公開 API(スレッドセーフ) ----

    @property
    def position(self) -> float:
        return self._position

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def tempo(self) -> float:
        """現在の実効テンポ(BPM)。"""
        return self.score.bpm_at(self._position) * self._rate

    def play(self) -> None:
        with self._lock:
            self._playing = True

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._generation += 1
        self.all_notes_off()

    def seek(self, beat: float) -> None:
        beat = max(0.0, min(beat, self.score.length_beats))
        with self._lock:
            self._position = beat
            self._next_event = self._first_event_at_or_after(beat)
            self._generation += 1
        self.all_notes_off()

    def load(self, score: MidiScore) -> None:
        """曲を差し替える。停止して先頭に戻る。"""
        with self._lock:
            self._playing = False
            self.score = score
            self._position = 0.0
            self._next_event = 0
            self._generation += 1
        self.all_notes_off()

    def set_rate(self, rate: float) -> None:
        with self._lock:
            self._rate = max(0.1, min(rate, 4.0))

    def all_notes_off(self) -> None:
        with self._send_lock:
            self._all_notes_off_unlocked()

    def _all_notes_off_unlocked(self) -> None:
        for ch in range(16):
            self._out.send_message([0xB0 | ch, ALL_NOTES_OFF, 0])
            self._out.send_message([0xB0 | ch, ALL_SOUND_OFF, 0])

    def close(self) -> None:
        self.stop()
        self._stop_flag.set()
        self._thread.join(timeout=1.0)
        self._out.close_port()

    # ---- 内部 ----

    def _first_event_at_or_after(self, beat: float) -> int:
        events = self.score.events
        lo, hi = 0, len(events)
        while lo < hi:
            mid = (lo + hi) // 2
            if events[mid].beat < beat:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _run(self) -> None:
        last = time.perf_counter()
        while not self._stop_flag.is_set():
            time.sleep(self._tick_interval)
            now = time.perf_counter()
            dt = now - last
            last = now
            with self._lock:
                if not self._playing:
                    continue
                bpm = self.score.bpm_at(self._position)
                self._position += dt * bpm / 60.0 * self._rate
                events = self.score.events
                to_send = []
                generation = self._generation
                while self._next_event < len(events) and events[self._next_event].beat <= self._position:
                    to_send.append(events[self._next_event].message)
                    self._next_event += 1
                if self._position >= self.score.length_beats:
                    self._playing = False
            if to_send:
                with self._send_lock:
                    with self._lock:
                        stale = generation != self._generation
                    if not stale:
                        for msg in to_send:
                            self._out.send_message(msg.bytes())
