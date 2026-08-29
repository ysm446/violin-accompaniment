"""オンライン楽譜追従(Phase 3)。

align.py の subsequence DTW の行再帰は因果的(前の行と現在のコストだけで次の行が決まる)なので、
それをフレームごとに 1 行ずつ進める。参照全列(数千)に対するベクトル演算なので 1 フレーム数十 µs。

出力は共有インターフェースの 3 値 {position, tempo, confidence}。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from .align import REF_STEP, reference_onsets
from .score_notes import ScoreNote, reference_chroma


@dataclass
class FollowState:
    position: float = 0.0  # 拍(平滑化済み)
    tempo: float = 0.0  # BPM(推定)
    confidence: float = 0.0
    raw_position: float = 0.0  # DTW の生の推定
    active: bool = False  # 音が鳴っていて追従中
    frames: int = 0

    def to_dict(self) -> dict:
        return {
            "position": round(self.position, 3),
            "tempo": round(self.tempo, 1),
            "confidence": round(self.confidence, 2),
            "raw_position": round(self.raw_position, 3),
            "active": self.active,
        }


class OnlineFollower:
    def __init__(
        self,
        notes: list[ScoreNote],
        score_bpm: float,
        fps: float,
        ref_step: float = REF_STEP,
        onset_weight: float = 0.5,
        onset_threshold: float = 0.3,
        onset_rise_db: float = 6.0,
        onset_chroma_scale: float = 0.5,
        skip_penalty: float = 0.1,
        window_back: float = 1.5,
        window_fwd: float = 3.0,
        near_eps: float = 0.05,
        jump_margin: float = 6.0,
        confidence_floor: float = 0.2,
        margin_scale: float = 4.0,
        restart_penalty: float = 15.0,
        silence_db: float = -55.0,
        chroma_peak_min: float = 0.45,
        tempo_window_sec: float = 3.0,
        measurement_gain: float = 0.25,
        snap_beats: float = 2.0,
    ):
        self.notes = notes
        self.score_bpm = score_bpm
        self.fps = fps
        self.ref_step = ref_step
        length = max((n.beat + n.duration for n in notes), default=0.0)
        self.ref = reference_chroma(notes, length, ref_step)
        self.ref_sil = np.linalg.norm(self.ref, axis=1) < 1e-6
        base = reference_onsets(notes, len(self.ref), ref_step)
        # ±2 刻み(1/8 拍)の三角パルスに広げる: ジッタで 1〜2 刻みずれても「一致」とみなす
        self.ref_onset = np.maximum.reduce([
            np.roll(base, k) * (1.0 - 0.3 * abs(k)) for k in (-2, -1, 0, 1, 2)
        ]).astype(np.float32)
        self.ref_beats = np.arange(len(self.ref)) * ref_step
        self.length_beats = length
        # 同音連打の区間: 連続する同じ音高の音符(間に休符なし)。DTW は区間内の位置を決められないので
        # オンセットの発火で音符を数えて進める。
        # MIDI の音価は次の音の開始より少し短い(ゲート)ので、「終わり」は次の音符の開始とする
        order = sorted(range(len(notes)), key=lambda i: notes[i].beat)
        starts = np.array([notes[i].beat for i in order])
        ends = np.array([notes[i].beat + notes[i].duration for i in order])
        for k in range(len(order) - 1):
            if starts[k + 1] > starts[k]:
                ends[k] = max(ends[k], starts[k + 1])
        self._note_starts = starts
        self._note_ends = ends
        self._run_next: dict[int, int] = {}  # (order 上の) 音符 index → 同音連打で次の音符 index
        for k in range(len(order) - 1):
            na, nb = notes[order[k]], notes[order[k + 1]]
            gap = nb.beat - (na.beat + na.duration)
            if nb.midi == na.midi and nb.beat > na.beat and gap < 0.26:
                self._run_next[k] = k + 1
        self.onset_weight = onset_weight
        self.onset_threshold = onset_threshold
        self.onset_rise_db = onset_rise_db
        self.onset_chroma_scale = onset_chroma_scale
        self.skip_penalty = skip_penalty
        self.window_back = window_back
        self.window_fwd = window_fwd
        self.near_eps = near_eps
        self.jump_margin = jump_margin
        self.restart_penalty = restart_penalty
        self.confidence_floor = confidence_floor
        self.margin_scale = margin_scale
        self.silence_db = silence_db
        self.chroma_peak_min = chroma_peak_min
        self.tempo_window = tempo_window_sec
        self.gain = measurement_gain
        self.snap_beats = snap_beats
        self._lock = threading.Lock()
        self.reset()

    # ---- 公開 ----

    def reset(self, position: float = 0.0) -> None:
        with self._lock:
            self.D = np.zeros(len(self.ref), dtype=np.float32)
            self.state = FollowState(position=position, tempo=self.score_bpm, raw_position=position)
            self._history: list[tuple[float, float]] = []  # (時刻, 生の位置)
            self._match_ema = 1.0
            self._last_time: float | None = None
            self._flux_scale = 1.0
            self._level_hist: list[float] = []
            self._chroma_hist: list[np.ndarray] = []
            self._onset_prev = 0.0
            self._refractory = 0
            self._on_hold = 0.0
            self._last_count_time: float | None = None
            self._last_fired = 0.0
            self._active_frames = 0
            self._floor_db = -90.0  # ノイズ床の推定(速く下がり、ゆっくり上がる)

    def _onset(self, chroma: np.ndarray, level_db: float) -> float:
        """オンセット強度 0..1。スペクトラルフラックスはビブラートで常に高くなるので使わない。
        (a) レベルの急上昇(直近 3 フレームの最小からの上昇、同音連打の再アタック)と
        (b) chroma の変化(3 フレーム前とのコサイン距離、音高変化)の大きい方を取り、
        閾値の立ち上がりエッジで 1 回だけ発火(不応期 3 フレーム)、次のフレームまで半分保持。"""
        self._level_hist.append(level_db)
        self._chroma_hist.append(chroma)
        if len(self._level_hist) > 4:
            self._level_hist.pop(0)
            self._chroma_hist.pop(0)
        rise = 0.0
        if len(self._level_hist) >= 2:
            rise = (level_db - min(self._level_hist[:-1])) / self.onset_rise_db
        change = 0.0
        if len(self._chroma_hist) >= 4 and chroma.any() and self._chroma_hist[0].any():
            change = (1.0 - float(chroma @ self._chroma_hist[0])) / self.onset_chroma_scale
        strength = float(np.clip(max(rise, change), 0.0, 1.0))
        fired = 0.0
        if self._refractory > 0:
            self._refractory -= 1
        elif strength >= self.onset_threshold and self._onset_prev < self.onset_threshold:
            fired = strength
            self._refractory = 3
        self._onset_prev = strength
        on = max(fired, self._on_hold)
        self._on_hold = fired * 0.5
        return on

    def _count_repeated(self, raw: float, predicted: float, now: float, tempo: float) -> float:
        """位置が同音連打の区間内で、直前のカウントから期待音価の 4 割以上経っていれば次の音符の頭へ。"""
        pos = max(raw, predicted)
        inside = np.nonzero((self._note_starts <= pos + 1e-6) & (self._note_ends > pos))[0]
        if len(inside) == 0:
            return raw
        i = int(inside[0])
        nxt = self._run_next.get(i)
        if nxt is None:
            return raw
        expected = (self._note_ends[i] - self._note_starts[i]) * 60.0 / max(tempo, 1e-6)
        if self._last_count_time is not None and now - self._last_count_time < 0.4 * expected:
            return raw
        self._last_count_time = now
        target = float(self._note_starts[nxt])
        self.state.position = target
        # DTW の累積コストにも反映: カウントした音符の頭を現在の最小に揃え、以降の観測がそこから続くようにする
        jt = min(int(round(target / self.ref_step)), len(self.D) - 1)
        self.D[jt] = float(self.D.min())
        return target

    def process(self, chroma: np.ndarray, flux: float, level_db: float, t: float | None = None) -> FollowState:
        """1 フレーム分の特徴量を入れて状態を更新する。t は perf_counter 基準の時刻。"""
        now = time.perf_counter() if t is None else t
        with self._lock:
            st = self.state
            dt = 0.0 if self._last_time is None else max(0.0, now - self._last_time)
            self._last_time = now
            # 無音判定: 固定閾値ではなく推定ノイズ床 + 12 dB(ノイズだけのフレームを音と誤認しない)
            if level_db < self._floor_db:
                self._floor_db = level_db
            else:
                self._floor_db += 0.002 * (level_db - self._floor_db)
            active = (
                level_db >= max(self.silence_db, self._floor_db + 12.0)
                and float(chroma.max()) >= self.chroma_peak_min
            )
            # --- コスト行 ---
            if active:
                c = 1.0 - self.ref @ chroma.astype(np.float32)
                c[self.ref_sil] = 1.0
                on = self._onset(chroma, level_db)
                fired_now = on > self._last_fired and on >= self.onset_threshold
                self._last_fired = on
                if self.onset_weight > 0:
                    # 非対称: 楽譜のオンセットが音に無い → 重い。音の余分なオンセット(弓返し等)→ 軽い
                    c += self.onset_weight * (np.maximum(self.ref_onset - on, 0.0) + 0.3 * np.maximum(on - self.ref_onset, 0.0))
                self._active_frames += 1
            else:
                c = np.zeros(len(self.ref), dtype=np.float32)
            # --- DTW 行再帰: 停滞 / 対角 / 早送り ---
            prev = self.D
            cand1 = np.empty_like(prev)
            cand1[0] = np.inf
            cand1[1:] = prev[:-1]
            cand2 = np.empty_like(prev)
            cand2[:2] = np.inf
            cand2[2:] = prev[:-2] + self.skip_penalty
            best_prev = np.minimum(prev, np.minimum(cand1, cand2))
            if self.restart_penalty > 0:
                # どの列からでも新しいパスを始められる(弾き直し・途中からの開始への耐性)
                best_prev = np.minimum(best_prev, self.restart_penalty)
            self.D = c + best_prev
            self.D -= self.D.min()  # 数値の発散防止
            # --- 予測と観測 ---
            predicted = st.position + dt * st.tempo / 60.0
            if active:
                # 観測: 予測位置の周りの窓で、累積コストがほぼ最小の列のうち予測に最も近いもの。
                # 同じ音の連打(コストが一様)の中では予測テンポで進み、境目で補正される。
                # 窓の外に大幅に良い列があれば(弾き直し・途中開始)そこへ再アンカーする。
                lo = max(0, int((predicted - self.window_back) / self.ref_step))
                hi = min(len(self.D), int((predicted + self.window_fwd) / self.ref_step) + 1)
                if hi <= lo:
                    lo, hi = 0, len(self.D)
                win = self.D[lo:hi]
                gmin = float(self.D.min())
                if gmin + self.jump_margin < float(win.min()):
                    j = int(np.argmin(self.D))
                else:
                    near = np.nonzero(win <= win.min() + self.near_eps)[0] + lo
                    j = int(near[np.argmin(np.abs(self.ref_beats[near] - predicted))])
                raw = float(self.ref_beats[j])
                # 同音連打の中なら、オンセットの発火で次の音符の頭へ進める
                if fired_now and self._run_next:
                    raw = self._count_repeated(raw, predicted, now, st.tempo)
                match = float(c[j] if self.onset_weight == 0 else min(c[j], 1.0))
                self._match_ema = 0.8 * self._match_ema + 0.2 * match
                self._history.append((now, raw))
                cutoff = now - self.tempo_window
                while self._history and self._history[0][0] < cutoff:
                    self._history.pop(0)
                # テンポ: 直近の観測の回帰
                if len(self._history) >= int(self.fps * 0.8):
                    tt = np.array([h[0] for h in self._history])
                    bb = np.array([h[1] for h in self._history])
                    if tt[-1] - tt[0] > 0.5:
                        slope = float(np.polyfit(tt, bb, 1)[0])  # 拍/秒
                        bpm = slope * 60.0
                        lo, hi = self.score_bpm * 0.5, self.score_bpm * 2.0
                        if lo <= bpm <= hi:
                            st.tempo = 0.9 * st.tempo + 0.1 * bpm
                # 位置: 予測に観測を混ぜる。大きくずれていたらスナップ
                if abs(raw - predicted) > self.snap_beats:
                    st.position = raw
                else:
                    st.position = predicted + self.gain * (raw - predicted)
                st.raw_position = raw
                # 確信度 = 直近の一致度 × 「選んだ列が全体最小からどれだけ離れているか」
                margin = float(self.D[j])  # D は min が 0 になるよう正規化済み
                st.confidence = float(np.clip((1.0 - self._match_ema) * np.exp(-margin / self.margin_scale), 0.0, 1.0))
                st.active = True
            else:
                # 無音: 直前のテンポで外挿し、確信度を下げていく
                st.position = min(predicted, self.length_beats)
                st.confidence = max(self.confidence_floor, st.confidence * 0.97)
                st.active = False
            st.frames += 1
            return FollowState(**st.__dict__)

    @property
    def current(self) -> FollowState:
        with self._lock:
            return FollowState(**self.state.__dict__)
