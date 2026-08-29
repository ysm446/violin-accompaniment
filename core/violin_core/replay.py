"""記録したセッションをオフラインで再処理する。

  core/.venv/Scripts/python -m violin_core.replay recordings/<dir>

audio.wav を FeatureExtractor に通し直し、記録時の features.npz と比較する。
オンラインとオフラインで同じ特徴量が出ること(= リプレイで再現できること)を確認する。
Phase 2 以降は、ここで follower を差し替えて評価する。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .audio import read_wav_mono
from .features import FeatureExtractor


def extract_offline(samples: np.ndarray, sr: int, n_fft: int, hop: int) -> dict[str, np.ndarray]:
    ex = FeatureExtractor(sr=sr, n_fft=n_fft, hop=hop)
    ring = np.zeros(n_fft, dtype=np.float32)
    chroma, flux, level = [], [], []
    for i in range(0, len(samples) - hop + 1, hop):
        ring = np.concatenate([ring[hop:], samples[i : i + hop]])
        f = ex.process(ring)
        chroma.append(f.chroma)
        flux.append(f.flux)
        level.append(f.level_db)
    return {
        "chroma": np.array(chroma, dtype=np.float32).reshape(-1, 12),
        "flux": np.array(flux, dtype=np.float32),
        "level_db": np.array(level, dtype=np.float32),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="セッション記録のオフライン再処理")
    parser.add_argument("session", type=Path, help="recordings/<dir>")
    args = parser.parse_args()

    meta = json.loads((args.session / "meta.json").read_text(encoding="utf-8"))
    sr, n_fft, hop = int(meta["sr"]), int(meta["n_fft"]), int(meta["hop"])
    samples = read_wav_mono(args.session / "audio.wav", sr)
    rec = np.load(args.session / "features.npz")
    off = extract_offline(samples, sr, n_fft, hop)

    n = min(len(rec["flux"]), len(off["flux"]))
    print(f"session: {args.session}  song={meta.get('song')}  input={meta.get('input')}  duration={meta.get('duration_sec', 0):.1f}s")
    print(f"frames: recorded={len(rec['flux'])} offline={len(off['flux'])} compared={n}")
    if n == 0:
        return
    chroma_err = np.abs(rec["chroma"][:n] - off["chroma"][:n]).max()
    level_err = np.abs(rec["level_db"][:n] - off["level_db"][:n]).max()
    print(f"max |chroma diff| = {chroma_err:.4f}   max |level diff| = {level_err:.2f} dB  (16bit 量子化の分だけ差が出る)")
    active = rec["level_db"][:n] > -60
    print(f"active frames: {int(active.sum())} / {n}")
    if active.any():
        mean_chroma = rec["chroma"][:n][active].mean(axis=0)
        from .features import PITCH_CLASSES

        top = np.argsort(mean_chroma)[::-1][:3]
        print("dominant pitch classes:", ", ".join(f"{PITCH_CLASSES[i]}={mean_chroma[i]:.2f}" for i in top))


if __name__ == "__main__":
    main()
