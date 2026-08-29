"""使い方:
  core/.venv/Scripts/python -m violin_core --midi ../muse-score/vivaldi_spring_first_movement_20251102.mid
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .midi_score import load_midi
from .player import MidiPlayer
from .server import StateServer


def main() -> None:
    parser = argparse.ArgumentParser(description="violin_core Phase 0: MIDI 固定テンポ再生 + 位置配信")
    parser.add_argument("--midi", required=True, type=Path, help="伴奏 MIDI ファイル")
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
    score = load_midi(args.midi, exclude_tracks=exclude)
    print(f"[core] {args.midi.name}: tracks={score.track_names} play={score.played_tracks} "
          f"events={len(score.events)} length={score.length_beats:.1f} beats bpm={score.score_bpm:.1f}")

    player = MidiPlayer(score, port_name=args.midi_out)
    print(f"[core] MIDI out: {player.port_name}")
    server = StateServer(player, port=args.port)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass
    finally:
        player.close()


if __name__ == "__main__":
    main()
