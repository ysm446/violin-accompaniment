"""オフライン整列と演奏後フィードバック(Phase 2)。

  core/.venv/Scripts/python -m violin_core.align ../recordings/<dir> --scores-dir ../scores

記録の chroma 系列と楽譜から合成した参照 chroma 系列を DTW で整列し、
音符ごとの発音時刻・タイミング偏差・音程偏差を analysis.json に書く。
Phase 3 以降のオンライン追従の「正解データ」もここから作る。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from .audio import read_wav_mono
from .score_notes import ScoreNote, load_part_notes, reference_chroma
from .songs import scan_songs

REF_STEP = 1 / 16  # 参照系列の刻み(拍)
SILENCE_DB = -55.0


# ---------------------------------------------------------------- DTW

def cost_matrix(
    audio_chroma: np.ndarray,
    ref: np.ndarray,
    audio_onset: np.ndarray | None = None,
    ref_onset: np.ndarray | None = None,
    onset_weight: float = 0.5,
) -> np.ndarray:
    """コサイン距離 (N_audio, N_ref)。無音同士は 0、片方だけ無音は 1。

    audio_onset / ref_onset(0..1)を渡すと、オンセットの有無の食い違いにコストを足す。
    chroma だけでは同音連打の境目が決められないので、これが同音連打の発音時刻を決める。
    """
    a_sil = np.linalg.norm(audio_chroma, axis=1) < 1e-6
    r_sil = np.linalg.norm(ref, axis=1) < 1e-6
    c = 1.0 - audio_chroma @ ref.T  # 両方 L2 正規化済み
    c = c.astype(np.float32)
    # 音声が無音のフレームはどこにいても良い(コスト 0)。停滞と前進が同点なら停滞が選ばれるので、
    # 無音中にパスが楽譜を勝手に進むことはない。参照が休符で音声が鳴っているときは不一致(1)。
    c[:, r_sil] = 1.0
    c[a_sil, :] = 0.0
    if audio_onset is not None and ref_onset is not None and onset_weight > 0:
        c += (onset_weight * np.abs(audio_onset[:, None] - ref_onset[None, :])).astype(np.float32)
    return c


def onset_strength(flux: np.ndarray, level_db: np.ndarray, silence_db: float = SILENCE_DB) -> np.ndarray:
    """スペクトラルフラックスを 0..1 に正規化し、局所ピークだけを残す。"""
    x = flux.astype(np.float32).copy()
    x[level_db < silence_db] = 0.0
    active = x[x > 0]
    scale = float(np.percentile(active, 95)) if len(active) else 1.0
    x = np.clip(x / max(scale, 1e-6), 0.0, 1.0)
    # 前後 2 フレームより大きい点だけピークとして残す
    peak = np.zeros_like(x)
    for i in range(2, len(x) - 2):
        if x[i] >= x[i - 1] and x[i] >= x[i + 1] and x[i] >= x[i - 2] and x[i] >= x[i + 2] and x[i] > 0.1:
            peak[i] = x[i]
    return peak


def reference_onsets(notes: list[ScoreNote], n_ref: int, ref_step: float) -> np.ndarray:
    """参照系列の各刻みに、そこで始まる音符があれば 1。"""
    r = np.zeros(n_ref, dtype=np.float32)
    for n in notes:
        j = int(round(n.beat / ref_step))
        if 0 <= j < n_ref:
            r[j] = 1.0
    return r


def subsequence_dtw(c: np.ndarray, stall_penalty: float = 0.0, skip_penalty: float = 0.1) -> tuple[np.ndarray, float]:
    """開始・終了自由の DTW。ステップは (1,0) 停滞 / (1,1) / (1,2) 早送り。

    行(音声フレーム)は必ず 1 つ進むので、行ごとにベクトル化できる。
    同点のときは argmin が先頭(停滞)を選ぶ。停滞ペナルティを正にすると、コストが一様な区間
    (無音など)でパスが前進を選んで楽譜を勝手に進めてしまうので、既定は 0。
    戻り値: path (N_audio,) 各音声フレームが対応する参照インデックス、正規化コスト。
    """
    n, m = c.shape
    D = np.full((n, m), np.inf, dtype=np.float32)
    B = np.zeros((n, m), dtype=np.int8)  # 0: (1,0), 1: (1,1), 2: (1,2)
    D[0] = c[0]
    for i in range(1, n):
        prev = D[i - 1]
        cand0 = prev + stall_penalty
        cand1 = np.full(m, np.inf, dtype=np.float32)
        cand1[1:] = prev[:-1]
        cand2 = np.full(m, np.inf, dtype=np.float32)
        cand2[2:] = prev[:-2] + skip_penalty
        stacked = np.stack([cand0, cand1, cand2])
        best = np.argmin(stacked, axis=0)
        D[i] = c[i] + stacked[best, np.arange(m)]
        B[i] = best
    j = int(np.argmin(D[-1]))
    total = float(D[-1, j])
    path = np.zeros(n, dtype=np.int32)
    for i in range(n - 1, -1, -1):
        path[i] = j
        if i > 0:
            j -= int(B[i, j])
    return path, total / n


# ---------------------------------------------------------------- f0

def yin_f0(x: np.ndarray, sr: int, fmin: float = 150.0, fmax: float = 2000.0, threshold: float = 0.15) -> float | None:
    """1 フレーム分の f0(YIN、CMND 閾値法)。見つからなければ None。"""
    n = len(x)
    tau_max = min(int(sr / fmin), n // 2)
    tau_min = max(2, int(sr / fmax))
    x = x - x.mean()
    if np.max(np.abs(x)) < 1e-4:
        return None
    # 差分関数(FFT で自己相関を取って計算)
    f = np.fft.rfft(x, 2 * n)
    acf = np.fft.irfft(f * np.conj(f))[:tau_max + 1]
    cumsum = np.cumsum(x**2)
    energy_head = cumsum[n - 1 - np.arange(tau_max + 1)]
    energy_tail = cumsum[-1] - np.concatenate([[0.0], cumsum[: tau_max]])
    d = energy_head + energy_tail - 2.0 * acf
    d[0] = 1.0
    cmnd = d[1:] * np.arange(1, tau_max + 1) / np.maximum(np.cumsum(d[1:]), 1e-12)
    cmnd = np.concatenate([[1.0], cmnd])
    tau = None
    for t in range(tau_min, tau_max):
        if cmnd[t] < threshold:
            while t + 1 < tau_max and cmnd[t + 1] < cmnd[t]:
                t += 1
            tau = t
            break
    if tau is None:
        t = int(np.argmin(cmnd[tau_min:tau_max])) + tau_min
        if cmnd[t] > 0.5:
            return None
        tau = t
    # 放物線補間
    if 1 <= tau < tau_max - 1:
        a, b, cc = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
        denom = a - 2 * b + cc
        if abs(denom) > 1e-12:
            tau = tau + 0.5 * (a - cc) / denom
    return sr / tau


def segment_f0(samples: np.ndarray, sr: int, t0: float, t1: float, win: int = 2048, hop: int = 512) -> float | None:
    """区間 [t0, t1) の f0 の中央値(Hz)。"""
    a, b = int(t0 * sr), int(t1 * sr)
    vals = []
    for s in range(a, max(a + 1, b - win), hop):
        seg = samples[s : s + win]
        if len(seg) < win:
            break
        f = yin_f0(seg, sr)
        if f is not None:
            vals.append(f)
    if len(vals) < 2:
        return None
    return float(np.median(vals))


# ---------------------------------------------------------------- 音符ごとの評価

def evaluate_notes(
    notes: list[ScoreNote],
    path: np.ndarray,
    frame_times: np.ndarray,
    level_db: np.ndarray,
    samples: np.ndarray,
    sr: int,
    ref_step: float = REF_STEP,
    tempo_window_beats: float = 4.0,
) -> list[dict]:
    """各音符について、整列から発音時刻を求め、周囲の音符の発音時刻から推定した局所テンポに対する
    タイミング偏差と、f0 による音程偏差を計算する。"""
    ref_beats = path * ref_step  # 各音声フレームが対応する拍
    results: list[dict] = []
    # 1 周目: 発音時刻
    for note in notes:
        onset_beat = note.beat
        end_beat = note.beat + note.duration
        idx = np.nonzero((ref_beats >= onset_beat) & (ref_beats < end_beat))[0]
        if len(idx) == 0:
            results.append({**note.to_dict(), "played": False})
            continue
        i0, i1 = int(idx[0]), int(idx[-1]) + 1
        seg = level_db[i0:i1]
        # 「演奏した」= 音符の冒頭 150 ms 以内に有音フレームがある、または区間の 3 割以上が有音
        head = seg[: max(1, int(0.15 * sr / 512))]
        if not (np.any(head >= SILENCE_DB) or np.mean(seg >= SILENCE_DB) >= 0.3):
            results.append({**note.to_dict(), "played": False})
            continue
        results.append({
            **note.to_dict(),
            "played": True,
            "onset_time": float(frame_times[i0]),
            "end_time": float(frame_times[min(i1, len(frame_times) - 1)]),
        })
    # 2 周目: 周囲の音符(自分を除く、同じ拍の重音も除く)の発音時刻で 時刻 = a + b * 拍 を最小二乗
    played = [r for r in results if r["played"]]
    pb = np.array([r["beat"] for r in played])
    pt = np.array([r["onset_time"] for r in played])
    for r in results:
        if not r["played"]:
            continue
        sel = (np.abs(pb - r["beat"]) <= tempo_window_beats) & (pb != r["beat"])
        timing_ms = None
        local_bpm = None
        if sel.sum() >= 4 and pb[sel].max() - pb[sel].min() >= 1.0:
            b_coef, a_coef = np.polyfit(pb[sel], pt[sel], 1)
            if b_coef > 0.05:
                timing_ms = (r["onset_time"] - (a_coef + b_coef * r["beat"])) * 1000.0
                local_bpm = 60.0 / b_coef
        # 音程: 発音直後の立ち上がりを避けて 30 ms 後から、最長 400 ms
        f0 = segment_f0(samples, sr, r["onset_time"] + 0.03, min(r["end_time"], r["onset_time"] + 0.43))
        cents = None
        if f0:
            nominal = 440.0 * 2 ** ((r["midi"] - 69) / 12)
            cents = 1200.0 * np.log2(f0 / nominal)
            while cents > 600:
                cents -= 1200
            while cents < -600:
                cents += 1200
            if abs(cents) > 250:
                cents = None
        r["onset_time"] = round(r["onset_time"], 3)
        r["end_time"] = round(r["end_time"], 3)
        r["timing_ms"] = None if timing_ms is None else round(float(timing_ms), 1)
        r["local_bpm"] = None if local_bpm is None else round(float(local_bpm), 1)
        r["f0"] = None if f0 is None else round(f0, 2)
        r["cents"] = None if cents is None else round(float(cents), 1)
    return results


# ---------------------------------------------------------------- セッション解析

def analyze_session(session: Path, midi_path: Path, ref_step: float = REF_STEP, onset_weight: float = 1.0) -> dict:
    t_start = time.perf_counter()
    meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
    feats = np.load(session / "features.npz")
    chroma = feats["chroma"]
    level_db = feats["level_db"]
    frame_times = feats["t"]
    sr = int(meta["sr"])
    samples = read_wav_mono(session / "audio.wav", sr)
    # features の t は記録開始からの AD 時刻。WAV は記録開始からの音声なので、フレーム i の末尾 ≒ (i+1)*hop/sr
    hop = int(meta["hop"])
    frame_times = (np.arange(len(chroma)) + 1) * hop / sr

    notes = load_part_notes(midi_path)
    length = max(n.beat + n.duration for n in notes) if notes else 0.0
    ref = reference_chroma(notes, length, ref_step)
    # 無音判定: ノイズ床(レベルの 10 パーセンタイル)+ 12 dB、かつ chroma にはっきりしたピークがあること。
    # ノイズだけのフレームは chroma が平坦(最大成分 0.3 前後)で、楽譜のどこにでも半端に一致してしまう。
    silence_db = max(SILENCE_DB, float(np.percentile(level_db, 10)) + 12.0)
    active = (level_db >= silence_db) & (chroma.max(axis=1) >= 0.45)
    audio = chroma.copy()
    audio[~active] = 0.0
    level_gated = np.where(active, level_db, -120.0)
    a_on = onset_strength(feats["flux"], level_gated, silence_db) if onset_weight > 0 else None
    r_on = reference_onsets(notes, len(ref), ref_step) if onset_weight > 0 else None
    c = cost_matrix(audio, ref, a_on, r_on, onset_weight)
    path, cost = subsequence_dtw(c)
    results = evaluate_notes(notes, path, frame_times, level_gated, samples, sr, ref_step)

    played = [r for r in results if r["played"]]
    cents = np.array([r["cents"] for r in played if r["cents"] is not None])
    timing = np.array([r["timing_ms"] for r in played if r["timing_ms"] is not None])
    ref_beats = path * ref_step
    summary = {
        "notes_total": len(notes),
        "notes_played": len(played),
        "first_beat": round(float(ref_beats[active][0]), 2) if active.any() else None,
        "last_beat": round(float(ref_beats[active][-1]), 2) if active.any() else None,
        "median_abs_cents": round(float(np.median(np.abs(cents))), 1) if len(cents) else None,
        "mean_cents": round(float(np.mean(cents)), 1) if len(cents) else None,
        "median_abs_timing_ms": round(float(np.median(np.abs(timing))), 1) if len(timing) else None,
        "dtw_cost": round(cost, 4),
        "silence_db": round(silence_db, 1),
        "onset_weight": onset_weight,
        "elapsed_sec": round(time.perf_counter() - t_start, 2),
    }
    # テンポ曲線(1 秒ごと)
    tempo_curve = []
    fps = sr / hop
    for s in range(int(len(chroma) / fps)):
        i0, i1 = int(s * fps), int((s + 1) * fps)
        seg = ref_beats[i0:i1]
        if len(seg) and active[i0:i1].any():
            tempo_curve.append({"t": s, "beat": round(float(seg[-1]), 2)})
    return {
        "session": session.name,
        "song": meta.get("song"),
        "summary": summary,
        "notes": results,
        "tempo_curve": tempo_curve,
        "path_beats": [round(float(b), 3) for b in ref_beats[:: int(fps / 10) or 1]],  # 0.1 秒刻み
    }


def find_song_midi(scores_dir: Path, song_id: str | None) -> Path | None:
    for s in scan_songs(scores_dir):
        if s.id == song_id:
            return s.midi
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="セッション記録を楽譜と整列して analysis.json を書く")
    parser.add_argument("session", type=Path)
    parser.add_argument("--scores-dir", type=Path, default=Path("../scores"))
    parser.add_argument("--midi", type=Path, default=None, help="参照にする MIDI(省略時は meta.json の song から探す)")
    parser.add_argument("--onset-weight", type=float, default=1.0, help="オンセット特徴の重み(0 で chroma のみ)")
    args = parser.parse_args()
    meta = json.loads((args.session / "meta.json").read_text(encoding="utf-8"))
    midi = args.midi or find_song_midi(args.scores_dir, meta.get("song"))
    if midi is None:
        raise SystemExit(f"曲 {meta.get('song')} の MIDI が見つかりません")
    result = analyze_session(args.session, midi, onset_weight=args.onset_weight)
    out = args.session / "analysis.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    s = result["summary"]
    print(f"notes: {s['notes_played']} / {s['notes_total']} played  beats {s['first_beat']}..{s['last_beat']}  "
          f"|cents| median {s['median_abs_cents']}  mean {s['mean_cents']}  |timing| median {s['median_abs_timing_ms']} ms  "
          f"cost {s['dtw_cost']}  ({s['elapsed_sec']} s)")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
