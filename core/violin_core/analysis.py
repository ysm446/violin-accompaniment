"""音声入力 → 特徴量 のパイプライン。

入力源(マイク / WAV)からブロックを受け取り、リングバッファに溜め、
hop ごとに FeatureExtractor を回して最新の特徴量を保持する。
録音中なら SessionRecorder に音声と特徴量を流す。

Phase 3 以降はここに follower(位置推定)を接続する。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from .audio import AudioSource
from .features import FeatureExtractor, FeatureFrame
from .recorder import SessionRecorder


@dataclass
class AudioStatus:
    source: str = ""
    level_db: float = -100.0
    chroma: list[float] = field(default_factory=lambda: [0.0] * 12)
    flux: float = 0.0
    latency_ms: float = 0.0  # 入力の AD 時刻 → 特徴量が出るまで
    frames: int = 0
    overruns: int = 0
    recording: bool = False
    recording_dir: str | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "level_db": round(self.level_db, 1),
            "chroma": [round(float(c), 3) for c in self.chroma],
            "flux": round(self.flux, 3),
            "latency_ms": round(self.latency_ms, 1),
            "frames": self.frames,
            "overruns": self.overruns,
            "recording": self.recording,
            "recording_dir": self.recording_dir,
        }


class AnalysisEngine:
    def __init__(self, sr: int = 48000, n_fft: int = 4096, hop: int = 512, recorder: SessionRecorder | None = None):
        self.sr = sr
        self.n_fft = n_fft
        self.hop = hop
        self.extractor = FeatureExtractor(sr=sr, n_fft=n_fft, hop=hop)
        self.recorder = recorder
        self._source: AudioSource | None = None
        self._queue: queue.Queue[tuple[np.ndarray, float]] = queue.Queue(maxsize=256)
        self._ring = np.zeros(n_fft, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self._latest: FeatureFrame | None = None
        self._status = AudioStatus()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latency_ema = 0.0
        # 特徴量の購読者(follower など)。(frame, adc_time) を受け取る
        self.listeners: list = []

    # ---- 入力源の切り替え ----

    def set_source(self, source: AudioSource | None) -> None:
        self.stop_source()
        self._source = source
        if source is None:
            self._status.source = ""
            return
        self._status.source = source.name
        self.extractor.reset()
        with self._lock:
            self._ring[:] = 0.0
            self._pending = np.zeros(0, dtype=np.float32)
        source.start(self._on_block)
        print(f"[audio] source: {source.name}")

    def stop_source(self) -> None:
        if self._source is not None:
            try:
                self._source.stop()
            except Exception as e:  # noqa: BLE001
                print(f"[audio] stop error: {e}")
            self._source = None

    # ---- ライフサイクル ----

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="analysis", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.stop_source()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self.recorder is not None and self.recorder.active:
            self.recorder.stop()

    # ---- 状態 ----

    @property
    def status(self) -> AudioStatus:
        rec = self.recorder
        self._status.recording = bool(rec and rec.active)
        self._status.recording_dir = str(rec.directory) if rec and rec.directory else None
        return self._status

    @property
    def latest(self) -> FeatureFrame | None:
        return self._latest

    # ---- 内部 ----

    def _on_block(self, block: np.ndarray, adc_time: float) -> None:
        # 入力スレッドからは queue に積むだけ。マイクのコールバックはブロック不可なので溢れたら捨てる
        if self._source is not None and self._source.blocking:
            self._queue.put((block, adc_time))
            return
        try:
            self._queue.put_nowait((block, adc_time))
        except queue.Full:
            self._status.overruns += 1

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                block, adc_time = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if self.recorder is not None and self.recorder.active:
                self.recorder.write_audio(block)
            self._pending = np.concatenate([self._pending, block]) if len(self._pending) else block
            # ブロックが hop の倍数でない入力にも対応する
            while len(self._pending) >= self.hop:
                hop_block = self._pending[: self.hop]
                self._pending = self._pending[self.hop :]
                self._ring = np.concatenate([self._ring[self.hop :], hop_block])
                frame = self.extractor.process(self._ring)
                now = time.perf_counter()
                # このホップ末尾の AD 時刻 = ブロック末尾の AD 時刻 - 残りサンプル分
                hop_adc = adc_time - len(self._pending) / self.sr
                latency = max(0.0, (now - hop_adc) * 1000.0)
                self._latency_ema = latency if self._status.frames == 0 else 0.9 * self._latency_ema + 0.1 * latency
                with self._lock:
                    self._latest = frame
                    s = self._status
                    s.level_db = frame.level_db
                    s.chroma = frame.chroma.tolist()
                    s.flux = frame.flux
                    s.latency_ms = self._latency_ema
                    s.frames += 1
                if self.recorder is not None and self.recorder.active:
                    self.recorder.write_features(frame.chroma, frame.flux, frame.level_db, hop_adc)
                for fn in self.listeners:
                    fn(frame, hop_adc)
