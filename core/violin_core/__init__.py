"""violin_core: 音声処理・追従・伴奏を担う Python 側プロセス。

Phase 0 では MIDI 固定テンポ再生と、再生位置の WebSocket 配信だけを持つ。
UI との共有インターフェースは {position, tempo, confidence} の 3 値のみ。
"""
