"""オンライン追従器の回帰テスト(合成演奏、真値あり)。

同音連打(拍 50.5〜63.75 の B5 ×21)で数秒遅れないこと、途中開始と弾き直しから再アンカーできることを確認する。
数値の根拠は docs/plan/progress.md(Phase 5)の評価値。
"""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from violin_core.follower import OnlineFollower
from violin_core.replay import extract_offline
from violin_core.score_notes import load_part_notes
from violin_core.synth import render_performance

ROOT = Path(__file__).resolve().parents[2]
SCORE = ROOT / "scores" / "vivaldi_spring_1" / "score.mid"
SR, HOP, WIN = 48000, 512, 4096
FPS = SR / HOP


def run_follower(notes, audio, start=0.0, **kw):
    F = extract_offline(audio, SR, WIN, HOP)
    T = (np.arange(len(F["flux"])) + 1) / FPS
    f = OnlineFollower(notes, 70.0, FPS, **kw)
    f.reset(start)
    pos = np.array([
        f.process(F["chroma"][i], float(F["flux"][i]), float(F["level_db"][i]), t=T[i]).position for i in range(len(T))
    ])
    return T, pos, F["level_db"]


@unittest.skipUnless(SCORE.exists(), "scores/vivaldi_spring_1/score.mid が必要")
class FollowerRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notes = load_part_notes(SCORE)

    def test_repeated_notes_tempo_varying(self):
        notes = self.notes
        bpm = lambda b: 80 - 15 * np.sin(b / 20)
        audio, truth = render_performance(notes, SR, bpm_curve=bpm, timing_jitter_ms=20, max_beats=66)
        T, pos, level = run_follower(notes, audio)
        tb = np.array([n.beat for n in notes[: len(truth)]])
        tt = np.array([x.onset for x in truth])
        true = np.interp(T, tt, tb)
        active = (level > -45) & (T >= tt[0]) & (T <= tt[-1])
        err_ms = np.abs(pos - true) * 60.0 / np.array([bpm(b) for b in true]) * 1000
        in_run = (true >= 50.5) & (true < 63.75)
        self.assertLess(np.median(err_ms[active]), 100)
        self.assertLess(np.percentile(err_ms[active], 95), 900)
        self.assertLess(np.median(err_ms[active & in_run]), 150)
        self.assertLess(np.percentile(err_ms[active & in_run], 95), 900)

    def test_restart_after_silence(self):
        notes = self.notes
        sub = [n for n in notes if 32 <= n.beat < 64]
        a1, t1 = render_performance(sub, SR, bpm_curve=lambda b: 75.0, lead_silence=0.5)
        cut_i = next(i for i, n in enumerate(sub) if n.beat >= 48)
        t_cut = t1[cut_i].onset
        sub2 = [n for n in notes if 40 <= n.beat < 56]
        a2, t2 = render_performance(sub2, SR, bpm_curve=lambda b: 75.0, lead_silence=1.5)
        audio = np.concatenate([a1[: int(t_cut * SR)], a2])
        T, pos, level = run_follower(notes, audio, start=32.0)  # 譜面クリックで拍 32 に置いてから弾く
        tb1 = np.array([n.beat for n in sub]); tt1 = np.array([x.onset for x in t1]); m1 = tt1 < t_cut
        tb2 = np.array([n.beat for n in sub2]); tt2 = np.array([x.onset for x in t2]) + t_cut
        true = np.where(T < t_cut, np.interp(T, tt1[m1], tb1[m1]), np.interp(T, tt2, tb2))
        active = (level > -45) & (T >= tt1[0]) & (T <= tt2[-1])
        err_ms = np.abs(pos - true) * 60.0 / 75.0 * 1000
        # 拍 32 に置いてからの開始
        self.assertLess(np.median(err_ms[active & (T < t_cut) & (T > 3.0)]), 150)
        # 1.5 秒の無音のあと拍 40 から弾き直し: 拍 40 の楽句は拍 316 にも同一なので、再探索は
        # lost_max_listen_sec(2.5 秒)まで聞いてから直前の位置に近い候補を採る。3 秒以内に復帰し、その後は追従する
        idx = np.nonzero((T > t_cut + 1.5) & (err_ms < 500) & active)[0]
        self.assertTrue(len(idx) > 0)
        self.assertLess(T[idx[0]] - t_cut - 1.5, 3.0)
        self.assertLess(np.median(err_ms[active & (T > t_cut + 3.0)]), 150)


if __name__ == "__main__":
    unittest.main()
