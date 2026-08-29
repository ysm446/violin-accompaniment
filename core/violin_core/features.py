"""特徴量抽出: chroma(12 次元)、スペクトラルフラックス、レベル。

numpy だけで実装する(librosa / numba を同梱すると配布物が重くなるため)。
1 ホップ(512 samples ≒ 10.7 ms)ごとに、直近 n_fft(4096)サンプルの窓で計算する。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-10


@dataclass
class FeatureFrame:
    chroma: np.ndarray  # (12,) L2 正規化済み。無音時はゼロ
    flux: float  # スペクトラルフラックス(正の変化分の合計)。オンセット検出用
    level_db: float  # 直近ホップの RMS(dBFS)
    energy: float  # スペクトルのエネルギー(無音判定用)


def chroma_filterbank(sr: int, n_fft: int, fmin: float = 150.0, fmax: float = 5000.0, width_cents: float = 60.0) -> np.ndarray:
    """FFT ビン → ピッチクラス(12)の重み行列 (12, n_fft//2+1)。

    各ビンの周波数を最も近い半音に割り当て、半音中心からのずれ(セント)でガウス重みを付ける。
    ビブラート(±50〜100 セント)で隣の半音へ漏れるのはある程度許容する。
    """
    n_bins = n_fft // 2 + 1
    freqs = np.arange(n_bins) * sr / n_fft
    fb = np.zeros((12, n_bins), dtype=np.float32)
    valid = (freqs >= fmin) & (freqs <= fmax)
    midi = 69.0 + 12.0 * np.log2(np.maximum(freqs, EPS) / 440.0)
    nearest = np.round(midi)
    cents = (midi - nearest) * 100.0
    weight = np.exp(-0.5 * (cents / width_cents) ** 2)
    pitch_class = (nearest.astype(int) % 12)
    for k in np.nonzero(valid)[0]:
        fb[pitch_class[k], k] = weight[k]
    # 高域の倍音がピッチクラスを汚しすぎないよう、周波数で緩やかに減衰させる
    rolloff = np.exp(-(freqs / fmax) ** 2 * 1.5)
    fb *= rolloff.astype(np.float32)
    return fb


class FeatureExtractor:
    def __init__(self, sr: int = 48000, n_fft: int = 4096, hop: int = 512, silence_db: float = -60.0):
        self.sr = sr
        self.n_fft = n_fft
        self.hop = hop
        self.silence_db = silence_db
        self._window = np.hanning(n_fft).astype(np.float32)
        self._fb = chroma_filterbank(sr, n_fft)
        self._prev_logmag: np.ndarray | None = None

    def reset(self) -> None:
        self._prev_logmag = None

    def process(self, frame: np.ndarray) -> FeatureFrame:
        """frame: 直近 n_fft サンプル(float32, mono)。末尾 hop サンプルが「今」のホップ。"""
        hop_block = frame[-self.hop :]
        rms = float(np.sqrt(np.mean(hop_block.astype(np.float64) ** 2)) + EPS)
        level_db = 20.0 * np.log10(rms)

        spec = np.fft.rfft(frame * self._window)
        mag = np.abs(spec).astype(np.float32)
        energy = float(np.sum(mag**2))
        logmag = np.log1p(mag)

        if self._prev_logmag is None:
            flux = 0.0
        else:
            diff = logmag - self._prev_logmag
            flux = float(np.sum(diff[diff > 0]))
        self._prev_logmag = logmag

        chroma = self._fb @ mag
        norm = float(np.linalg.norm(chroma))
        if level_db < self.silence_db or norm < EPS:
            chroma = np.zeros(12, dtype=np.float32)
        else:
            chroma = (chroma / norm).astype(np.float32)
        return FeatureFrame(chroma=chroma, flux=flux, level_db=float(level_db), energy=energy)


PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
