"""楽譜の追従対象パート(Violin)の音符列を MIDI から取り出す。

MusicXML を解析しなくても、MuseScore が書き出した score.mid の Violin トラックに
拍位置・音高・長さがそのまま入っている(反復は展開済み)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido
import numpy as np


@dataclass(frozen=True)
class ScoreNote:
    index: int
    beat: float  # 発音位置(四分音符 = 1.0)
    duration: float  # 拍
    midi: int

    @property
    def freq(self) -> float:
        return 440.0 * 2 ** ((self.midi - 69) / 12)

    def to_dict(self) -> dict:
        return {"index": self.index, "beat": self.beat, "duration": self.duration, "midi": self.midi}


def load_part_notes(path: str | Path, track_prefix: str = "Violin") -> list[ScoreNote]:
    mid = mido.MidiFile(str(path))
    ppq = mid.ticks_per_beat
    notes: list[ScoreNote] = []
    for track in mid.tracks:
        name = next((m.name for m in track if m.type == "track_name"), "")
        if not name.lower().startswith(track_prefix.lower()):
            continue
        tick = 0
        active: dict[int, int] = {}
        raw: list[tuple[int, int, int]] = []  # (start_tick, end_tick, midi)
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                active[msg.note] = tick
            elif msg.type in ("note_off", "note_on"):
                start = active.pop(msg.note, None)
                if start is not None:
                    raw.append((start, tick, msg.note))
        raw.sort()
        for i, (s, e, n) in enumerate(raw):
            notes.append(ScoreNote(len(notes), s / ppq, max(e - s, 1) / ppq, n))
    return notes


def reference_chroma(notes: list[ScoreNote], length_beats: float, step: float = 1 / 16, fifth: float = 0.25) -> np.ndarray:
    """音符列から参照 chroma 系列 (N, 12) を作る。休符はゼロベクトル。

    実音には倍音(第 3 倍音 = 完全 5 度上)が乗るので、5 度に小さな重みを足しておく。
    """
    n = int(np.ceil(length_beats / step)) + 1
    ref = np.zeros((n, 12), dtype=np.float32)
    for note in notes:
        a = int(round(note.beat / step))
        b = max(a + 1, int(round((note.beat + note.duration) / step)))
        pc = note.midi % 12
        ref[a:b, pc] += 1.0
        ref[a:b, (pc + 7) % 12] += fifth
    norms = np.linalg.norm(ref, axis=1, keepdims=True)
    ref = np.where(norms > 0, ref / np.maximum(norms, 1e-9), 0.0).astype(np.float32)
    return ref
