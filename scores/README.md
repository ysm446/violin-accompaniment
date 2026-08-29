# scores — 楽譜フォルダ

作成日時: 2026-08-30 06:10
更新日時: 2026-08-30 06:10

曲ごとに 1 フォルダ。フォルダ名が曲 id(ASCII、アプリ内部の識別子)になる。

```
scores/
  <song_id>/
    score.mscz   MuseScore のソース(唯一の編集元)
    score.mxl    MuseScore から書き出した MusicXML(圧縮)。譜面表示に使う
    score.mid    MuseScore から書き出した MIDI。伴奏に使う(Violin トラックは鳴らさない)
    song.json    表示名など  例: { "name": "ヴィヴァルディ「春」第1楽章", "composer": "Antonio Vivaldi" }
```

- `score.mxl` と `score.mid` の両方があるフォルダだけが曲一覧に出る(`.mscz` だけのフォルダは未書き出し扱い)。
- 書き出しは MuseScore 4 の「ファイル > 書き出し」または CLI(`MuseScore4.exe -o score.mxl score.mscz`)。
- バイオリンのパート名は `Violin` にする(追従対象の判定に使う)。
