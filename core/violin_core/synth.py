"""楽譜の音符列から「正解つきの演奏音声」を合成する(評価用)。

テンポ曲線・ビブラート・音程のずれ・発音のゆらぎを与えて WAV を作り、
各音符の真の発音時刻を返す。整列アルゴリズムの精度測定と、Phase 3 以降の追従テストに使う。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .score_notes import ScoreNote


@dataclass
class SynthNote:
    index: int
    onset: float  # 秒
    end: float
    freq: float  # 実際に鳴らした周波数
    cents: float  # 参照からのずれ


def render_performance(
    notes: list[ScoreNote],
    sr: int = 48000,
    bpm_curve=lambda beat: 80.0,
    detune_cents=lambda i: 0.0,
    timing_jitter_ms: float = 0.0,
    vibrato_cents: float = 30.0,
    vibrato_hz: float = 5.5,
    lead_silence: float = 1.0,
    seed: int = 0,
    max_beats: float | None = None,
) -> tuple[np.ndarray, list[SynthNote]]:
    rng = np.random.default_rng(seed)
    # 拍 → 秒(テンポ曲線を積分)
    step = 1 / 32
    length = max(n.beat + n.duration for n in notes)
    if max_beats is not None:
        length = min(length, max_beats)
    grid = np.arange(0, length + step, step)
    sec = np.concatenate([[0.0], np.cumsum([60.0 / bpm_curve(b) * step for b in grid[:-1]])])

    def beat_to_sec(b: float) -> float:
        return float(np.interp(b, grid, sec)) + lead_silence

    total = beat_to_sec(length) + 1.0
    out = np.zeros(int(total * sr) + sr, dtype=np.float32)
    truth: list[SynthNote] = []
    for n in notes:
        if max_beats is not None and n.beat >= max_beats:
            break
        onset = beat_to_sec(n.beat) + rng.normal(0, timing_jitter_ms / 1000.0)
        end = beat_to_sec(n.beat + n.duration)
        dur = max(end - onset, 0.05)
        cents = float(detune_cents(n.index))
        freq = n.freq * 2 ** (cents / 1200)
        t = np.arange(int(dur * sr)) / sr
        vib = 2 ** (vibrato_cents / 1200 * np.sin(2 * np.pi * vibrato_hz * t + rng.uniform(0, 6.28)))
        phase = 2 * np.pi * np.cumsum(freq * vib) / sr
        tone = 0.5 * np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase) + 0.08 * np.sin(4 * phase)
        # 立ち上がり 20 ms、減衰 30 ms
        env = np.ones_like(t)
        a = min(int(0.02 * sr), len(t))
        env[:a] = np.linspace(0, 1, a)
        r = min(int(0.03 * sr), len(t))
        env[-r:] *= np.linspace(1, 0, r)
        s0 = int(onset * sr)
        out[s0 : s0 + len(t)] += (tone * env * 0.4).astype(np.float32)
        truth.append(SynthNote(n.index, onset, onset + dur, freq, cents))
    out += rng.normal(0, 0.002, len(out)).astype(np.float32)  # 軽いノイズ
    return out, truth
