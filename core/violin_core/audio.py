"""音声入力源。マイク(sounddevice)と WAV ファイル(リプレイ用)を同じインターフェースで扱う。

どちらも `on_block(block: np.ndarray(float32, mono), adc_time: float)` を呼ぶ。
adc_time はそのブロックの末尾サンプルが AD 変換された時刻(perf_counter 基準)。
遅延計測に使う。
"""

from __future__ import annotations

import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

BlockCallback = Callable[[np.ndarray, float], None]


@dataclass(frozen=True)
class InputDevice:
    id: int
    name: str
    hostapi: str
    default_samplerate: float

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "hostapi": self.hostapi, "samplerate": self.default_samplerate}


def list_input_devices() -> list[InputDevice]:
    import sounddevice as sd

    apis = sd.query_hostapis()
    out: list[InputDevice] = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        out.append(InputDevice(i, d["name"], apis[d["hostapi"]]["name"], float(d["default_samplerate"])))
    return out


def default_input_device() -> int | None:
    """WASAPI の既定入力を優先し、無ければ全体の既定入力。"""
    import sounddevice as sd

    for api in sd.query_hostapis():
        if api["name"] == "Windows WASAPI" and api["default_input_device"] >= 0:
            return int(api["default_input_device"])
    dev = sd.default.device
    idx = dev[0] if isinstance(dev, (list, tuple)) else dev
    return int(idx) if idx is not None and idx >= 0 else None


class AudioSource:
    name: str = ""
    blocking: bool = False  # True なら on_block は処理が追いつくまで待ってよい(WAV など)

    def start(self, on_block: BlockCallback) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class MicSource(AudioSource):
    def __init__(self, device: int | None, sr: int = 48000, blocksize: int = 512):
        import sounddevice as sd

        self._sd = sd
        self.device = device
        self.sr = sr
        self.blocksize = blocksize
        self._stream = None
        self._latency = 0.0
        self.blocking = False  # コールバックをブロックしてはいけない
        self.name = sd.query_devices(device)["name"] if device is not None else "default"

    def start(self, on_block: BlockCallback) -> None:
        sd = self._sd

        def callback(indata, frames, time_info, status):
            if status:
                print(f"[audio] {status}")
            # WASAPI の inputBufferAdcTime は信用できない(コールバック時刻より未来の値が返る)ので、
            # PortAudio が報告する入力レイテンシを引いて AD 時刻の近似とする
            adc_end = time.perf_counter() - self._latency
            on_block(indata[:, 0].copy(), adc_end)

        self._stream = sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=self.sr,
            blocksize=self.blocksize,
            dtype="float32",
            latency="low",
            callback=callback,
        )
        self._stream.start()
        self._latency = float(self._stream.latency)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class WavSource(AudioSource):
    """WAV ファイルをブロック単位で流す。realtime=True なら実時間のペースで、False なら最速で。"""

    def __init__(self, path: str | Path, sr: int = 48000, blocksize: int = 512, realtime: bool = True, loop: bool = False):
        self.path = Path(path)
        self.sr = sr
        self.blocksize = blocksize
        self.realtime = realtime
        self.loop = loop
        self.name = f"wav:{self.path.name}"
        self.blocking = True  # リプレイではブロックを落とさない
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.samples = read_wav_mono(self.path, sr)

    def start(self, on_block: BlockCallback) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(on_block,), name="wav-source", daemon=True)
        self._thread.start()

    def _run(self, on_block: BlockCallback) -> None:
        n = self.blocksize
        block_dur = n / self.sr
        while not self._stop.is_set():
            t0 = time.perf_counter()
            for i in range(0, len(self.samples) - n + 1, n):
                if self._stop.is_set():
                    return
                target = t0 + (i + n) / self.sr
                if self.realtime:
                    delay = target - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
                on_block(self.samples[i : i + n], target if self.realtime else time.perf_counter())
            if not self.loop:
                break
        del block_dur

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None


def read_wav_mono(path: str | Path, sr: int) -> np.ndarray:
    """16/24/32bit PCM WAV を float32 mono に読む。サンプルレートが違えば線形補間でリサンプル。"""
    with wave.open(str(path), "rb") as w:
        ch = w.getnchannels()
        width = w.getsampwidth()
        file_sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
        data = ints.astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"未対応のサンプル幅: {width} bytes")
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if file_sr != sr:
        n_out = int(len(data) * sr / file_sr)
        x_old = np.linspace(0.0, 1.0, len(data), endpoint=False)
        x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
        data = np.interp(x_new, x_old, data).astype(np.float32)
    return np.ascontiguousarray(data, dtype=np.float32)
