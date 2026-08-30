"""セッション記録。リプレイ基盤の要。

recordings/<YYYYMMDD-HHMMSS>/
  audio.wav       入力音声(48 kHz mono 16bit)
  features.npz    chroma (N,12) / flux (N,) / level_db (N,) / t (N,) 各フレームの入力時刻
  states.jsonl    core が配信した 3 値(position / tempo / confidence など)の履歴
  meta.json       曲 id、入力デバイス、パラメータ、開始時刻
"""

from __future__ import annotations

import json
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np


class SessionRecorder:
    def __init__(self, root: str | Path, sr: int):
        self.root = Path(root)
        self.sr = sr
        self._lock = threading.Lock()
        self._dir: Path | None = None
        self._wav: wave.Wave_write | None = None
        self._states_f = None
        self._chroma: list[np.ndarray] = []
        self._flux: list[float] = []
        self._level: list[float] = []
        self._t: list[float] = []
        self._meta: dict = {}
        self._t0 = 0.0

    @property
    def active(self) -> bool:
        return self._dir is not None

    @property
    def directory(self) -> Path | None:
        return self._dir

    def start(self, meta: dict) -> Path:
        with self._lock:
            if self._dir is not None:
                return self._dir
            self.root.mkdir(parents=True, exist_ok=True)
            # 秒単位では素早い再開時に既存セッションを上書きするため、マイクロ秒まで含める。
            self._dir = self.root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self._dir.mkdir(parents=True, exist_ok=False)
            self._wav = wave.open(str(self._dir / "audio.wav"), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(self.sr)
            self._states_f = open(self._dir / "states.jsonl", "w", encoding="utf-8")
            self._chroma, self._flux, self._level, self._t = [], [], [], []
            self._t0 = time.perf_counter()
            self._meta = {**meta, "sr": self.sr, "started_at": datetime.now().isoformat(timespec="seconds")}
            print(f"[rec] start {self._dir}")
            return self._dir

    def write_audio(self, block: np.ndarray) -> None:
        with self._lock:
            if self._wav is None:
                return
            pcm = np.clip(block, -1.0, 1.0)
            self._wav.writeframes((pcm * 32767.0).astype("<i2").tobytes())

    def write_features(self, chroma: np.ndarray, flux: float, level_db: float, t: float) -> None:
        with self._lock:
            if self._dir is None:
                return
            self._chroma.append(chroma.copy())
            self._flux.append(flux)
            self._level.append(level_db)
            self._t.append(t - self._t0)

    def write_state(self, state: dict) -> None:
        with self._lock:
            if self._states_f is None:
                return
            self._states_f.write(json.dumps({"t": time.perf_counter() - self._t0, **state}) + "\n")

    def stop(self) -> Path | None:
        with self._lock:
            if self._dir is None:
                return None
            d = self._dir
            self._wav.close()
            self._wav = None
            self._states_f.close()
            self._states_f = None
            np.savez(
                d / "features.npz",
                chroma=np.array(self._chroma, dtype=np.float32).reshape(-1, 12),
                flux=np.array(self._flux, dtype=np.float32),
                level_db=np.array(self._level, dtype=np.float32),
                t=np.array(self._t, dtype=np.float64),
            )
            self._meta["duration_sec"] = time.perf_counter() - self._t0
            self._meta["frames"] = len(self._t)
            (d / "meta.json").write_text(json.dumps(self._meta, ensure_ascii=False, indent=2), encoding="utf-8")
            self._dir = None
            print(f"[rec] stop {d} ({len(self._t)} frames)")
            return d
