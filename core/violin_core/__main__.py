"""使い方:
  core/.venv/Scripts/python -m violin_core --scores-dir ../scores
  core/.venv/Scripts/python -m violin_core --midi ../scores/<id>/score.mid   (単一曲)
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .midi_score import MidiScore, load_midi
from .player import MidiPlayer
from .server import StateServer
from .songs import Song, scan_songs


def main() -> None:
    parser = argparse.ArgumentParser(description="violin_core: MIDI 伴奏再生 + 位置配信")
    parser.add_argument("--scores-dir", type=Path, default=None, help="楽譜フォルダ(.mxl と .mid の組を曲として列挙)")
    parser.add_argument("--midi", type=Path, default=None, help="単一の伴奏 MIDI ファイル(--scores-dir の代わり)")
    parser.add_argument("--song", default=None, help="起動時に読み込む曲 id(省略時は一覧の先頭)")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket ポート")
    parser.add_argument("--midi-out", default=None, help="MIDI 出力ポート名(部分一致)。省略時は先頭")
    parser.add_argument("--exclude", default="Violin", help="鳴らさないトラック名(前方一致、カンマ区切り)")
    parser.add_argument("--list-ports", action="store_true", help="MIDI 出力ポートを表示して終了")
    args = parser.parse_args()

    if args.list_ports:
        import rtmidi

        for i, name in enumerate(rtmidi.MidiOut().get_ports()):
            print(f"{i}: {name}")
        return

    exclude = tuple(s.strip() for s in args.exclude.split(",") if s.strip())

    if args.scores_dir is not None:
        songs = scan_songs(args.scores_dir)
    elif args.midi is not None:
        folder = args.midi.resolve().parent
        songs = [Song(id=folder.name, name=folder.name, xml=args.midi.with_suffix(".mxl"), midi=args.midi)]
    else:
        parser.error("--scores-dir か --midi のどちらかを指定してください")
        return

    print(f"[core] songs: {[s.id for s in songs]}")
    current: str | None = None
    score = MidiScore()
    if songs:
        wanted = args.song or songs[0].id
        song = next((s for s in songs if s.id == wanted), songs[0])
        score = load_midi(song.midi, exclude_tracks=exclude)
        current = song.id
        print(f"[core] {song.midi.name}: tracks={score.track_names} play={score.played_tracks} "
              f"events={len(score.events)} length={score.length_beats:.1f} beats bpm={score.score_bpm:.1f}")
    else:
        print("[core] 曲が見つかりません(.mxl と .mid の組が必要)")

    player = MidiPlayer(score, port_name=args.midi_out)
    print(f"[core] MIDI out: {player.port_name}")
    server = StateServer(player, songs, current, exclude, port=args.port)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass
    finally:
        player.close()


if __name__ == "__main__":
    main()
