"""オンライン楽譜追従(Phase 3、同音連打対応は Phase 5)。

align.py の subsequence DTW の行再帰は因果的(前の行と現在のコストだけで次の行が決まる)なので、
それをフレームごとに 1 行ずつ進める。参照全列(数千)に対するベクトル演算なので 1 フレーム数十 µs。

位置の推定は「予測(位置 + テンポ)」と「観測」の混合。観測は DTW の累積コストがほぼ最小の列の集合
(同点区間)で、予測が区間内なら補正せず、外なら近い端へ引き寄せる。同じ音が続く区間(同音連打)では
DTW は位置を決められないので、テンポと発火カウント(発火 1 回 = 次の音符の頭)で進める。
無音が続けば見失い(lost)として楽譜全体から再探索し、候補が 1 箇所に絞れてから再アンカーする。

出力は共有インターフェースの 3 値 {position, tempo, confidence}(+ raw_position / lost / uniqueness など)。
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
    lost: bool = False  # 無音が続き、位置を保持している(次の音で楽譜全体から探し直す)
    in_rest: bool = False  # 現在位置が楽譜上の休符
    uniqueness: float = 0.0  # 0..1。楽譜上の別の場所(2 拍以上離れた列)より現在位置がどれだけ良いか
    candidates: int = 0  # 再探索中の候補(離れた場所)の数。1 なら一意
    tempo_ready: bool = False  # 奏者のテンポを実測から推定できた(伴奏開始の条件)
    alternates: list = None  # 副仮説 [{position, lead}](主仮説より良い状態が続いている秒数つき)
    frames: int = 0

    def to_dict(self) -> dict:
        return {
            "position": round(self.position, 3),
            "tempo": round(self.tempo, 1),
            "confidence": round(self.confidence, 2),
            "raw_position": round(self.raw_position, 3),
            "active": self.active,
            "lost": self.lost,
            "in_rest": self.in_rest,
            "uniqueness": round(self.uniqueness, 2),
            "candidates": self.candidates,
            "tempo_ready": self.tempo_ready,
            "alternates": [{"position": round(a["pos"], 2), "lead": round(a["lead"], 2)} for a in (self.alternates or [])],
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
        onset_bonus: float = 0.5,
        onset_miss_cost: float = 0.5,
        onset_hold: tuple[float, ...] = (0.7, 0.4),
        transient_damp: float = 0.5,
        fire_snap: bool = True,
        snap_back_frac: float = 0.3,
        block_gap_beats: float = 0.3,
        snap_min_interval: float = 0.5,
        snap_min_strength: float = 0.5,
        tempo_range: tuple[float, float] = (0.6, 1.5),
        tempo_gain: float = 0.2,
        skip_penalty: float = 0.1,
        window_back: float = 1.5,
        window_fwd: float = 3.0,
        near_eps: float = 0.05,
        jump_margin: float = 6.0,
        jump_dwell_frames: int = 6,
        alt_margin: float = 4.0,
        alt_switch_near_sec: float = 1.5,
        alt_switch_far_sec: float = 4.0,
        alt_near_beats: float = 8.0,
        alt_max: int = 4,
        alt_trust_sec: float = 5.0,
        local_beats: float | None = 16.0,
        lost_after_sec: float = 1.0,
        lost_listen_sec: float = 1.0,
        lost_max_listen_sec: float = 2.5,
        lost_unique_frames: int = 20,
        distinct_beats: float = 2.0,
        uniqueness_scale: float = 6.0,
        confidence_floor: float = 0.2,
        margin_scale: float = 4.0,
        restart_penalty: float = 30.0,
        silence_db: float = -55.0,
        chroma_peak_min: float = 0.45,
        tempo_window_sec: float = 3.0,
        measurement_gain: float = 0.25,
        snap_beats: float = 2.0,
    ):
        self.notes = notes
        self.score_bpm = score_bpm
        self.base_bpm = score_bpm  # 奏者が弾くと想定するテンポ(初期値と推定範囲の中心)。UI の目標テンポで変わる
        self.fps = fps
        self.ref_step = ref_step
        length = max((n.beat + n.duration for n in notes), default=0.0)
        self.ref = reference_chroma(notes, length, ref_step)
        self.ref_sil = np.linalg.norm(self.ref, axis=1) < 1e-6
        base = reference_onsets(notes, len(self.ref), ref_step)
        self.ref_head = base.astype(np.float32)  # 音符の頭の列そのもの(報酬用)
        # ±2 刻み(1/8 拍)の三角パルスに広げる: ジッタで 1〜2 刻みずれても「一致」とみなす
        self.ref_onset = np.maximum.reduce([
            np.roll(base, k) * (1.0 - 0.3 * abs(k)) for k in (-2, -1, 0, 1, 2)
        ]).astype(np.float32)
        self.ref_beats = np.arange(len(self.ref)) * ref_step
        # 各列について、参照 chroma が同じ列が連続するブロック(同じ音が続く区間・同音連打)の最後の列。
        # スタッカートや MIDI のゲートで音符の間に短い無音列(≤ block_gap_beats)が挟まっていても、
        # 前後が同じ音高なら同じブロックとして橋渡しする(実演奏の連打はスタッカートが多い)
        n_ref = len(self.ref)
        same = np.zeros(n_ref - 1, dtype=bool)  # same[k]: 列 k と k+1 が同じブロック
        max_gap = int(round(block_gap_beats / ref_step))
        last_sound = -1  # 直前の有音列
        for k in range(n_ref):
            if self.ref_sil[k]:
                continue
            if last_sound >= 0 and k - last_sound - 1 <= max_gap and np.allclose(self.ref[k], self.ref[last_sound]):
                same[last_sound:k] = True
            last_sound = k
        self._block_end = np.arange(n_ref)
        for k in range(len(self.ref) - 2, -1, -1):
            if same[k]:
                self._block_end[k] = self._block_end[k + 1]
        self._block_start = np.arange(len(self.ref))
        for k in range(1, len(self.ref)):
            if same[k - 1]:
                self._block_start[k] = self._block_start[k - 1]
        self._head_cols = np.nonzero(self.ref_head > 0)[0]
        self.length_beats = length
        self.onset_weight = onset_weight
        self.onset_threshold = onset_threshold
        self.onset_rise_db = onset_rise_db
        self.onset_chroma_scale = onset_chroma_scale
        self.onset_bonus = onset_bonus
        self.onset_miss_cost = onset_miss_cost
        self.onset_hold = tuple(onset_hold)
        self.transient_damp = transient_damp
        self.fire_snap = fire_snap
        self.snap_back_frac = snap_back_frac
        self.snap_min_interval = snap_min_interval
        self.snap_min_strength = snap_min_strength
        self.tempo_range = tempo_range
        self.tempo_gain = tempo_gain
        self.skip_penalty = skip_penalty
        self.window_back = window_back
        self.window_fwd = window_fwd
        self.near_eps = near_eps
        self.jump_margin = jump_margin
        self.jump_dwell_frames = jump_dwell_frames
        self.alt_margin = alt_margin
        self.alt_switch_near_sec = alt_switch_near_sec
        self.alt_switch_far_sec = alt_switch_far_sec
        self.alt_near_beats = alt_near_beats
        self.alt_max = alt_max
        self.alt_trust_sec = alt_trust_sec
        # 通しモード: 再スタート・副仮説・再探索を現在位置の ±local_beats に限定する(None なら楽譜全体)。
        # 「通しで弾く(飛ばない)」前提なら、遠くの似た楽句に乗り換える失敗が原理的に消える
        self.local_beats = local_beats
        self.lost_after_sec = lost_after_sec
        self.lost_listen_sec = lost_listen_sec
        self.lost_max_listen_sec = lost_max_listen_sec
        self.lost_unique_frames = lost_unique_frames
        self.distinct_beats = distinct_beats
        self.uniqueness_scale = uniqueness_scale
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

    def _local_mask(self, position: float) -> np.ndarray:
        if self.local_beats is None:
            return np.ones(len(self.ref), dtype=bool)
        return np.abs(self.ref_beats - position) <= self.local_beats

    def _restart_row(self, position: float) -> np.ndarray | float | None:
        """再スタート項: 列ごとの「新しいパスを始めるコスト」。通しモードでは近く以外は不可(inf)。"""
        if self.restart_penalty <= 0:
            return None
        if self.local_beats is None:
            return self.restart_penalty
        row = np.full(len(self.ref), np.inf, dtype=np.float32)
        row[self._local_mask(position)] = self.restart_penalty
        return row

    # ---- 公開 ----

    def sync_position(self, beat: float) -> None:
        """位置だけを外から合わせる(合奏モードでバイオリンが鳴っていない間、伴奏の位置に追随させる)。
        累積コストは保つので、鳴り始めたら近くから追従を再開できる。"""
        with self._lock:
            self.state.position = float(beat)
            self.state.raw_position = float(beat)

    def set_base_tempo(self, bpm: float) -> None:
        """想定テンポを変える(追従 ON 中のレートスライダー)。推定中のテンポも範囲内に収める。"""
        with self._lock:
            self.base_bpm = max(10.0, float(bpm))
            lo, hi = self.base_bpm * self.tempo_range[0], self.base_bpm * self.tempo_range[1]
            if not self.state.active or self.state.lost:
                self.state.tempo = self.base_bpm
            else:
                self.state.tempo = float(np.clip(self.state.tempo, lo, hi))

    def reset(self, position: float = 0.0) -> None:
        with self._lock:
            self.D = np.zeros(len(self.ref), dtype=np.float32)
            self.Du = np.zeros(len(self.ref), dtype=np.float32)
            self.state = FollowState(position=position, tempo=self.base_bpm, raw_position=position)
            self._history: list[tuple[float, float]] = []  # (時刻, 拍): 同点区間の下端が上がったイベント
            self._match_ema = 1.0
            self._last_time: float | None = None
            self._flux_scale = 1.0
            self._level_hist: list[float] = []
            self._chroma_hist: list[np.ndarray] = []
            self._onset_prev = 0.0
            self._refractory = 0
            self._onset_strength = 0.0
            self._hold_left: list[float] = []
            self._plateau_lo: float | None = None  # 同点区間の下端(テンポ推定のイベント検出用)
            self._last_snap_time: float | None = None
            self._rest_end: float | None = None  # 無音が楽譜の休符にかかっているとき、その休符の終わり(拍)
            self._jump_frames = 0
            self._unique_frames = 0
            self._alts: list[dict] = []  # 副仮説 {pos, lead, deficit}
            self._good_sec = 0.0  # 主仮説の実績(窓が全体最小に近かった累積秒)
            self._silence_start: float | None = None
            self._last_fired = 0.0
            self._active_frames = 0
            self._floor_db = -90.0  # ノイズ床の推定(速く下がり、ゆっくり上がる)

    def _onset(self, chroma: np.ndarray, level_db: float) -> float:
        """オンセット強度 0..1。スペクトラルフラックスはビブラートで常に高くなるので使わない。
        (a) レベルの急上昇(直近 3 フレームの最小からの上昇、同音連打の再アタック)と
        (b) chroma の変化(3 フレーム前とのコサイン距離、音高変化)の大きい方を取り、
        閾値の立ち上がりエッジで 1 回だけ発火(不応期 3 フレーム)、onset_hold の比率で数フレーム減衰保持。"""
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
        self._onset_strength = strength  # 閾値前の連続値(過渡の減衰に使う)
        fired = 0.0
        if self._refractory > 0:
            self._refractory -= 1
        elif strength >= self.onset_threshold and self._onset_prev < self.onset_threshold:
            fired = strength
            self._refractory = 3
        self._onset_prev = strength
        if fired > 0:
            self._hold_left = [fired * h for h in self.onset_hold]
            return fired
        return self._hold_left.pop(0) if self._hold_left else 0.0

    def _clusters(self, cols: np.ndarray) -> list[tuple[int, int]]:
        """列の集合を、distinct_beats 以上離れたら別の場所とみなして分ける。"""
        if len(cols) == 0:
            return []
        gap = int(self.distinct_beats / self.ref_step)
        out = []
        start = prev = int(cols[0])
        for c in cols[1:]:
            c = int(c)
            if c - prev > gap:
                out.append((start, prev))
                start = c
            prev = c
        out.append((start, prev))
        return out

    def _snap_to_head(self, predicted: float, now: float, tempo: float) -> float | None:
        """同音連打の中でオンセットが発火したら、予測位置の次の音符の頭へ進める(予測が頭のすぐ後ろなら
        その頭に揃える)。DTW は同じ音が続く間は位置を決められず、発火の取りこぼしがあると DTW の
        数え上げは恒久的に遅れるので、数え上げは予測位置を基準に前進専用で行う。"""
        jp = min(max(int(round(predicted / self.ref_step)), 0), len(self.ref) - 1)
        bs, be = int(self._block_start[jp]), int(self._block_end[jp])
        heads = self._head_cols[(self._head_cols >= bs) & (self._head_cols <= be)]
        if len(heads) < 2:
            return None
        hb = self.ref_beats[heads]
        k = int(np.searchsorted(hb, predicted + 1e-9) - 1)
        if k < 0:
            return float(hb[0])
        spacing = float(hb[k + 1] - hb[k]) if k + 1 < len(hb) else float(hb[k] - hb[k - 1])
        if self._last_snap_time is not None and now - self._last_snap_time < self.snap_min_interval * spacing * 60.0 / max(tempo, 1e-6):
            return None
        if predicted - hb[k] < self.snap_back_frac * spacing:
            return float(hb[k])
        if k + 1 >= len(hb):
            return None  # 連打の最後の音: 次は別の音なので chroma に任せる
        return float(hb[k + 1])

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
                c_raw = c.copy()  # 減衰・報酬なしの素の証拠(一意性の判定用)
                transient = max(on, self._onset_strength)
                if transient > 0 and self.transient_damp > 0:
                    # 発音の過渡(レベルや chroma が動いている間)は chroma が当てにならない。実演奏では
                    # 過渡ごとに真の経路が 2〜3 損をし、隣に別の音を持つ別区間(過渡を迂回路で吸収できる)
                    # に負ける。発火の閾値に届かない弱い過渡でも減衰させる
                    c *= 1.0 - self.transient_damp * transient
                fired_now = on > self._last_fired and on >= self.onset_threshold
                self._last_fired = on
                if self.onset_weight > 0:
                    # 音の余分なオンセット(弓返し等)が楽譜に無い所で鳴ったときの軽いペナルティ(毎フレーム、
                    # 三角パルスで ±2 刻みの許容)。「楽譜のオンセットが音に無い」ほうは毎フレームではなく、
                    # 頭の列を通過するときの 1 回きりの遷移コスト(下の head_cost)にする。毎フレームにすると
                    # 16 分音符の楽句(4 列ごとに頭 → 全列が三角の中)で音符ごとに数点積み上がり、同じ音型を
                    # 長い音価で持つ別区間(再現部の tutti 等)に必ず負ける
                    extra = (self.onset_weight * 0.3) * np.maximum(on - self.ref_onset, 0.0)
                    c += extra
                    c_raw += extra
                # 頭の列に前進ステップで入るときの遷移コスト: 発火に一致していれば報酬(負)、無ければ小さな減点。
                # 停滞には付けない(付けると、いま居る音符の頭が毎回最小になって raw が 1 音戻る)
                head_cost = (self.onset_miss_cost * (1.0 - on) - self.onset_bonus * on) * self.ref_head
                head_cost_raw = (self.onset_miss_cost * (1.0 - on)) * self.ref_head
                self._active_frames += 1
            else:
                c = np.zeros(len(self.ref), dtype=np.float32)
                c_raw = c
                head_cost = head_cost_raw = None
            # --- 一意性判定用の累積コスト: 減衰・報酬を入れない(それらは追従の頑健さには効くが、
            # 「楽譜上の他の場所より十分良いか」の証拠を弱める。伴奏の開始条件はこちらで測る)
            pu = self.Du
            u1 = np.empty_like(pu); u1[0] = np.inf; u1[1:] = pu[:-1]
            u2 = np.empty_like(pu); u2[:2] = np.inf; u2[2:] = pu[:-2] + self.skip_penalty
            if head_cost_raw is not None:
                u1[1:] += head_cost_raw[1:]
                u2[2:] += head_cost_raw[2:] + head_cost_raw[1:-1]  # 早送りで飛び越す頭の分も払う
            bu = np.minimum(pu, np.minimum(u1, u2))
            restart = self._restart_row(st.position)
            if restart is not None:
                bu = np.minimum(bu, restart)
            self.Du = c_raw + bu
            self.Du -= self.Du.min()
            # --- DTW 行再帰: 停滞 / 対角 / 早送り ---
            prev = self.D
            cand1 = np.empty_like(prev)
            cand1[0] = np.inf
            cand1[1:] = prev[:-1]
            cand2 = np.empty_like(prev)
            cand2[:2] = np.inf
            cand2[2:] = prev[:-2] + self.skip_penalty
            if head_cost is not None:
                cand1[1:] += head_cost[1:]
                cand2[2:] += head_cost[2:] + head_cost[1:-1]
            best_prev = np.minimum(prev, np.minimum(cand1, cand2))
            if restart is not None:
                # 新しいパスを始められる(弾き直し・途中からの開始への耐性)。通しモードでは近くだけ
                best_prev = np.minimum(best_prev, restart)
            self.D = c + best_prev
            self.D -= self.D.min()  # 数値の発散防止
            # --- 予測と観測 ---
            predicted = st.position + dt * st.tempo / 60.0
            if active:
                # 観測: 予測位置の周りの窓で、累積コストがほぼ最小の列の集合(同点区間)。
                # 同じ音が続く間はコストが平坦で区間内の位置は決められないので、区間を「位置はこの範囲」
                # という観測として扱う: 予測が区間内なら補正せず、外なら近い端に引き寄せる。
                # (予測に最も近い列を点の観測にすると、観測が予測に引きずられて列境界の手前に固定される)
                # 窓の外に大幅に良い列があれば(弾き直し・途中開始)そこへ再アンカーする。
                lo = max(0, int((predicted - self.window_back) / self.ref_step))
                hi = min(len(self.D), int((predicted + self.window_fwd) / self.ref_step) + 1)
                if hi <= lo:
                    lo, hi = 0, len(self.D)
                win = self.D[lo:hi]
                gmin = float(self.D.min())
                near = np.nonzero(win <= win.min() + self.near_eps)[0] + lo
                j = int(near[np.argmin(np.abs(self.ref_beats[near] - predicted))])
                point = False  # 観測が 1 点に決まった(再アンカー)か
                if st.lost:
                    # 見失った後の音: 最初の 1 音だけでは同じ音高の列が楽譜中に多数同点で並ぶので、
                    # lost_listen_sec 聞いてから楽譜全体の最小コスト列を採る。ただし候補が楽譜上の複数の
                    # 離れた場所に同点で残っている間は決めない(誤った場所で伴奏を鳴らすより待つ)。
                    self._jump_frames += 1
                    if self.local_beats is not None:
                        gmin = float(self.D[self._local_mask(st.position)].min())
                    cand = np.nonzero((self.D <= gmin + self.near_eps) & self._local_mask(st.position))[0]
                    clusters = self._clusters(cand)
                    st.candidates = len(clusters)
                    listened = self._jump_frames / self.fps
                    # 一意になっても数フレームは続くのを待つ(過渡の 1 フレームで他の候補が落ちただけのことがある)
                    self._unique_frames = self._unique_frames + 1 if len(clusters) == 1 else 0
                    unique = self._unique_frames >= self.lost_unique_frames
                    if listened < self.lost_listen_sec or (not unique and listened < self.lost_max_listen_sec):
                        return FollowState(**st.__dict__)
                    if not unique:
                        # 十分聞いても曖昧: 直前の位置に近い候補を採る(確信度は低いまま)
                        j = int(cand[np.argmin(np.abs(self.ref_beats[cand] - st.position))])
                    else:
                        j = int(cand[len(cand) // 2])
                    self._jump_frames = 0
                    point = True
                else:
                    # 複数仮説: 主仮説(いま追跡している位置)のほかに、累積コストが主仮説の窓より
                    # alt_margin 以上良い場所を副仮説として並走させ、「主仮説より良い状態が続いた時間
                    # (lead)」を数える。乗り換えは lead が閾値(近くなら alt_switch_near_sec、遠くなら
                    # alt_switch_far_sec)を超えたときだけ。瞬間的な差(ミス・過渡・同じ音名の別の箇所)
                    # では飛ばず、窓が悪いのに副仮説が育っていない間は位置を保持する(確信度が下がるので
                    # 伴奏側が止まる)。弾き直しは近くが多いので、近い副仮説ほど早く乗り換える
                    wmin = float(win.min())
                    if wmin - gmin < self.alt_margin:
                        self._good_sec += dt
                    cand = np.nonzero((self.D <= wmin - self.alt_margin) & self._local_mask(st.position))[0]
                    clusters = self._clusters(cand) if len(cand) else []
                    seen = []
                    for a, b in clusters[: self.alt_max * 2]:
                        seg = self.D[a:b + 1]
                        col = a + int(np.argmin(seg))
                        beat = float(self.ref_beats[col])
                        if abs(beat - st.position) < self.distinct_beats:
                            continue
                        match_alt = None
                        for alt in self._alts:
                            if abs(alt["pos"] - beat) < 3.0 + abs(st.tempo) / 60.0 * 0.5 and alt not in seen:
                                match_alt = alt
                                break
                        if match_alt is None:
                            match_alt = {"pos": beat, "lead": 0.0}
                            self._alts.append(match_alt)
                        match_alt["pos"] = beat
                        match_alt["lead"] += dt  # 主仮説より良い間は増える
                        match_alt["deficit"] = wmin - float(seg.min())
                        seen.append(match_alt)
                    for alt in self._alts:
                        if alt not in seen:
                            alt["lead"] -= 0.5 * dt  # 良くない間はゆっくり減り、0 を切ったら消える(一瞬の揺れでは消えない)
                            alt["deficit"] = 0.0
                    self._alts = sorted([a for a in self._alts if a["lead"] > 0], key=lambda a: -a["lead"])[: self.alt_max]
                    st.candidates = len(self._alts) + 1 if self._alts else 0
                    if self._alts:
                        # lead が閾値(近い候補は短め)を超えた副仮説のうち、コストが最良のものと同点の候補が
                        # 複数あれば(同じ楽句のコピー)、現在位置に近いほうを採る
                        # 主仮説に実績(窓が良かった累積秒)が無いうち(曲頭・乗り換え直後)は、遠い候補にも
                        # 早く乗り換える。実績があれば、ミス中に一時的に負けても遠い候補には 4 秒待つ
                        untrusted = self._good_sec < self.alt_trust_sec

                        def need_of(a):
                            near = abs(a["pos"] - st.position) <= self.alt_near_beats
                            return self.alt_switch_near_sec if (near or untrusted) else self.alt_switch_far_sec
                        ready = [a for a in self._alts if a in seen and a["lead"] >= need_of(a)]
                        if ready:
                            top = max(a["deficit"] for a in ready)
                            tied = [a for a in ready if a["deficit"] >= top - self.alt_margin]
                            best = min(tied, key=lambda a: abs(a["pos"] - st.position))
                            j = min(int(round(best["pos"] / self.ref_step)), len(self.D) - 1)
                            point = True
                            self._alts = []
                            self._good_sec = 0.0
                if point:
                    seg_lo = seg_hi = float(self.ref_beats[j])
                else:
                    # j を含む連続した同点区間の両端
                    a = b = int(np.searchsorted(near, j))
                    while a > 0 and near[a - 1] == near[a] - 1:
                        a -= 1
                    while b + 1 < len(near) and near[b + 1] == near[b] + 1:
                        b += 1
                    seg_lo, seg_hi = float(self.ref_beats[near[a]]), float(self.ref_beats[near[b]])
                    # 同じ音が続くブロックの中では、上側の境界はオンセット項(次の音符の頭の三角パルス)による
                    # もので chroma の証拠ではない。発火を取りこぼしても遅れが積み上がらないよう、上側は
                    # ブロックの終わりまで広げてテンポで進ませる(下側は発火の報酬で押し上げられる)。
                    seg_hi = max(seg_hi, float(self.ref_beats[self._block_end[near[b]]]))
                    bs, be = int(self._block_start[near[a]]), int(self._block_end[near[a]])
                    if self._head_cols[(self._head_cols >= bs) & (self._head_cols <= be)].size >= 2:
                        # 同音連打ブロックの中: DTW の下端はオンセット項で決まっていて、実演奏では
                        # 余分な発火(弓返し等)のたびに 1 音進んでしまうので信用しない。区間 = ブロック
                        # 全体にして、位置はテンポと発火カウント(_snap_to_head)だけで動かす
                        seg_lo = min(seg_lo, float(self.ref_beats[bs]))
                raw = float(np.clip(predicted, seg_lo, seg_hi))
                snapped = None
                if self.fire_snap and fired_now and on >= self.snap_min_strength and not point and not st.lost:
                    snapped = self._snap_to_head(predicted, now, st.tempo)
                    if snapped is not None:
                        self._last_snap_time = now
                        raw = seg_lo = snapped
                        point = True
                        # DTW の累積コストにも反映: 数えた音符の頭を現在の最小に揃える。実演奏では発火が弱く
                        # (強度 0.3 前後)三角ペナルティを打ち消せないため、放っておくと真の経路のコストが
                        # 数フレームで数点上がり、別の場所の同じ音の長い音符(停滞が無料)へ窓ジャンプする
                        jt = min(int(round(snapped / self.ref_step)), len(self.D) - 1)
                        # 頭の列だけでなく三角パルスの後半(+2 列)までそろえ、次のフレームで無料の列へ抜けられるようにする
                        self.D[jt:jt + 3] = np.minimum(self.D[jt:jt + 3], float(self.D.min()))
                        j = jt
                match = float(np.clip(c[j], 0.0, 1.0))
                self._match_ema = 0.8 * self._match_ema + 0.2 * match
                # テンポ: 同点区間の下端が上がった(次の音符に入った)時刻をイベントとして集め、回帰する。
                # 区間内の位置は予測そのものなので、毎フレームの raw を回帰に使うと循環して情報がない
                new_event = self._plateau_lo is None or seg_lo > self._plateau_lo + 1e-6 or point
                if new_event:
                    self._history.append((now, seg_lo))
                self._plateau_lo = seg_lo
                cutoff = now - self.tempo_window
                while self._history and self._history[0][0] < cutoff:
                    self._history.pop(0)
                # テンポの更新はイベントが増えたときだけ(毎フレーム更新すると数点の回帰に即座に追従して
                # 暴走する)。範囲も楽譜の 0.6〜1.5 倍に絞る(余分な発火で数え過ぎたときの歯止め)
                if new_event and len(self._history) >= 4:
                    tt = np.array([h[0] for h in self._history])
                    bb = np.array([h[1] for h in self._history])
                    if tt[-1] - tt[0] > 1.0:
                        slope = float(np.polyfit(tt, bb, 1)[0])  # 拍/秒
                        bpm = float(np.clip(slope * 60.0, self.base_bpm * self.tempo_range[0], self.base_bpm * self.tempo_range[1]))
                        # 実測が無いうち(曲頭・再アンカー直後)は実測をほぼそのまま採り、実績がつくほど緩やかに追従する
                        gain = self.tempo_gain if st.tempo_ready else 0.7
                        st.tempo = (1.0 - gain) * st.tempo + gain * bpm
                        st.tempo_ready = True
                # 位置: 予測に観測を混ぜる。大きくずれていたらスナップ
                if abs(raw - predicted) > self.snap_beats or snapped is not None:
                    st.position = raw
                else:
                    st.position = predicted + self.gain * (raw - predicted)
                st.raw_position = raw
                # 一意性: 現在位置から distinct_beats 以上離れた列の最小コストとの差
                far = np.abs(self.ref_beats - raw) >= self.distinct_beats
                near_u = float(self.Du[max(0, j - 4):j + 5].min())  # 現在位置の近傍(±1/4 拍)の最小
                second = float(self.Du[far].min()) if far.any() else self.uniqueness_scale
                st.uniqueness = float(np.clip((second - near_u) / self.uniqueness_scale, 0.0, 1.0))
                st.alternates = [{"pos": a["pos"], "lead": a["lead"]} for a in self._alts]
                if st.lost:
                    # 見失いからの復帰: 観測をそのまま位置にし、テンポは楽譜の値に戻す
                    st.position = raw
                    st.tempo = self.base_bpm
                    self._history = [(now, raw)]
                    self._plateau_lo = raw
                    st.lost = False
                    st.tempo_ready = False
                self._silence_start = None
                self._rest_end = None
                st.in_rest = False
                # 確信度 = 直近の一致度 × 「選んだ列が全体最小からどれだけ離れているか」
                margin = float(self.D[j])  # D は min が 0 になるよう正規化済み
                st.confidence = float(np.clip((1.0 - self._match_ema) * np.exp(-margin / self.margin_scale), 0.0, 1.0))
                st.active = True
            else:
                # 無音: 楽譜上の休符なら直前のテンポで外挿。休符でない無音が lost_after_sec 続いたら
                # 「見失った」として位置を保持し、次の音で楽譜全体から探し直す
                if self._silence_start is None:
                    self._silence_start = now
                jp = min(int(round(predicted / self.ref_step)), len(self.ref) - 1)
                if self.ref_sil[jp] and self._rest_end is None:
                    # 休符に入った: 休符の終わり(次に音がある列)を覚え、位置はそこで止める。奏者が休符を
                    # 長めに取っても次の音符へ勝手に進まず、休符の長さ + lost_after_sec まで待つ
                    nxt = np.nonzero(~self.ref_sil[jp:])[0]
                    self._rest_end = float(self.ref_beats[jp + int(nxt[0])]) if len(nxt) else self.length_beats
                    self._rest_end_deadline = now + (self._rest_end - st.position) * 60.0 / max(st.tempo, 1e-6)
                st.in_rest = self._rest_end is not None
                silence = now - self._silence_start
                if st.lost:
                    pass  # 位置を保持
                elif st.in_rest:
                    st.position = min(predicted, self._rest_end - self.ref_step, self.length_beats)
                    if now > self._rest_end_deadline + self.lost_after_sec:
                        st.lost = True
                elif silence < self.lost_after_sec:
                    st.position = min(predicted, self.length_beats)
                else:
                    # 見失い: 無音の前の履歴は次の音の場所を決める根拠にならないので累積コストを白紙にし、
                    # 再探索は無音のあとに聞いた音だけで行う(残すと直前の位置の経路が再スタートペナルティの
                    # 分だけ有利なまま残り、別の場所から弾き直したときの再アンカーが数秒遅れる)
                    st.lost = True
                    self.D[:] = 0.0
                    self.Du[:] = 0.0
                    self._good_sec = 0.0
                    self._alts = []
                st.confidence = max(self.confidence_floor, st.confidence * 0.97)
                st.active = False
            st.frames += 1
            return FollowState(**st.__dict__)

    @property
    def current(self) -> FollowState:
        with self._lock:
            return FollowState(**self.state.__dict__)
