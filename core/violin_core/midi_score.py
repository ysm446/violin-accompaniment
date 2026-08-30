"""MIDI ファイルを「拍位置付きイベント列」に変換する。

拍位置は四分音符 = 1.0 の連続値(MIDI の ticks / ticks_per_beat)。
UI 側の MusicXML タイムスタンプ(全音符 = 1.0)とは 4 倍の関係にある。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import mido

DEFAULT_EXCLUDE_TRACKS = ("violin",)


@dataclass(frozen=True)
class MidiEvent:
    beat: float
    message: mido.Message  # channel message (note_on / note_off / program_change / control_change ...)


@dataclass(frozen=True)
class TempoChange:
    beat: float
    bpm: float


@dataclass(frozen=True)
class Meter:
    beat: float
    numerator: int
    denominator: int

    @property
    def click_unit(self) -> float:
        """メトロノームの 1 拍の長さ(四分音符 = 1.0)。複合拍子(6/8, 12/8 …)は付点四分。"""
        if self.denominator == 8 and self.numerator % 3 == 0 and self.numerator >= 6:
            return 1.5
        return 4.0 / self.denominator

    @property
    def clicks_per_bar(self) -> int:
        if self.click_unit == 1.5:
            return self.numerator // 3
        return self.numerator


@dataclass(frozen=True)
class Click:
    beat: float
    accent: bool  # 小節の頭


@dataclass
class MidiScore:
    events: list[MidiEvent] = field(default_factory=list)
    tempos: list[TempoChange] = field(default_factory=list)
    meters: list[Meter] = field(default_factory=list)
    length_beats: float = 0.0
    track_names: list[str] = field(default_factory=list)
    played_tracks: list[str] = field(default_factory=list)

    def bpm_at(self, beat: float) -> float:
        bpm = 120.0
        for t in self.tempos:
            if t.beat <= beat:
                bpm = t.bpm
            else:
                break
        return bpm

    @property
    def score_bpm(self) -> float:
        return self.tempos[0].bpm if self.tempos else 120.0

    def clicks(self) -> list[Click]:
        """メトロノームのクリック列。拍子ごとに区間を分け、各区間の先頭から拍を刻む。
        拍子が無ければ 4/4 とみなす。弱起の小節(例: 1/8)は短い区間として扱われる。"""
        meters = self.meters or [Meter(0.0, 4, 4)]
        out: list[Click] = []
        for i, m in enumerate(meters):
            end = meters[i + 1].beat if i + 1 < len(meters) else self.length_beats
            unit, per_bar = m.click_unit, max(1, m.clicks_per_bar)
            if i == 0 and i + 1 < len(meters):
                nxt = meters[1]
                if end - m.beat < nxt.click_unit * nxt.clicks_per_bar - 1e-6:
                    continue  # 弱起(次の拍子の 1 小節に満たない先頭区間)は刻まず、最初の小節頭から始める
            k = 0
            beat = m.beat
            while beat < end - 1e-6:
                out.append(Click(beat, k % per_bar == 0))
                k += 1
                beat = m.beat + k * unit
        return out


def _track_name(track: mido.MidiTrack) -> str:
    for msg in track:
        if msg.type == "track_name":
            return msg.name
    return ""


def load_midi(path: str | Path, exclude_tracks: tuple[str, ...] = DEFAULT_EXCLUDE_TRACKS) -> MidiScore:
    """MIDI を読み込み、名前が exclude_tracks(前方一致・小文字比較)に該当するトラックを除いた
    チャンネルイベントを拍順に並べて返す。テンポ変更は全トラックから拾う。"""
    mid = mido.MidiFile(str(path))
    ppq = mid.ticks_per_beat
    score = MidiScore()
    excluded = tuple(e.lower() for e in exclude_tracks)

    for track in mid.tracks:
        name = _track_name(track)
        score.track_names.append(name)
        play = not any(name.lower().startswith(e) for e in excluded)
        if play:
            score.played_tracks.append(name)
        tick = 0
        for msg in track:
            tick += msg.time
            beat = tick / ppq
            if msg.type == "set_tempo":
                score.tempos.append(TempoChange(beat, mido.tempo2bpm(msg.tempo)))
            elif msg.type == "time_signature":
                score.meters.append(Meter(beat, msg.numerator, msg.denominator))
            elif play and not msg.is_meta:
                score.events.append(MidiEvent(beat, msg))
            score.length_beats = max(score.length_beats, beat)

    score.events.sort(key=lambda e: e.beat)
    score.tempos.sort(key=lambda t: t.beat)
    # 同一拍のテンポ重複を除去(複数トラックに同じテンポが入ることがある)
    dedup: list[TempoChange] = []
    for t in score.tempos:
        if not dedup or dedup[-1].beat != t.beat or dedup[-1].bpm != t.bpm:
            dedup.append(t)
    score.tempos = dedup
    score.meters.sort(key=lambda m: m.beat)
    dedup_m: list[Meter] = []
    for m in score.meters:
        if dedup_m and dedup_m[-1].beat == m.beat:
            dedup_m[-1] = m
        elif not dedup_m or (dedup_m[-1].numerator, dedup_m[-1].denominator) != (m.numerator, m.denominator):
            dedup_m.append(m)
    score.meters = dedup_m
    return score
