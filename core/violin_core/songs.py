"""楽譜フォルダ(scores/)を走査して曲の一覧を作る。

scores/<song_id>/ に score.mxl(または .musicxml / .xml)と score.mid があるフォルダを曲として扱う。
表示名は同じフォルダの song.json の "name"(無ければフォルダ名)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

XML_CANDIDATES = ("score.mxl", "score.musicxml", "score.xml")
MIDI_NAME = "score.mid"
META_NAME = "song.json"


@dataclass(frozen=True)
class Song:
    id: str
    name: str
    xml: Path
    midi: Path

    def to_dict(self) -> dict:
        # xml は scores/ からの相対パス(UI が静的ファイルとして読む)
        return {"id": self.id, "name": self.name, "xml": f"{self.id}/{self.xml.name}"}


def scan_songs(scores_dir: str | Path) -> list[Song]:
    scores_dir = Path(scores_dir)
    songs: list[Song] = []
    if not scores_dir.is_dir():
        return songs
    for folder in sorted(p for p in scores_dir.iterdir() if p.is_dir()):
        midi = folder / MIDI_NAME
        xml = next((folder / c for c in XML_CANDIDATES if (folder / c).exists()), None)
        if xml is None or not midi.exists():
            continue
        songs.append(Song(id=folder.name, name=_display_name(folder), xml=xml, midi=midi))
    return songs


def _display_name(folder: Path) -> str:
    meta = folder / META_NAME
    if meta.exists():
        try:
            name = json.loads(meta.read_text(encoding="utf-8")).get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except (OSError, ValueError):
            pass
    return folder.name
