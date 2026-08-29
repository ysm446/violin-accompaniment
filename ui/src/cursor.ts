import type { OpenSheetMusicDisplay } from "opensheetmusicdisplay";

/** 拍位置(四分音符 = 1.0)と描画座標の対応点。 */
export interface CursorPoint {
  beat: number;
  left: number;
  top: number;
  height: number;
  /** 楽譜上の小節番号(MusicXML の number 属性。弱起は 0 になりうる) */
  measure: number;
}

/**
 * OSMD の Cursor を先頭から末尾まで進め、各音符位置の (拍, 座標) 表を作る。
 * 実行時はこの表を補間して連続値の拍位置から座標を得る。
 * OSMD のタイムスタンプは全音符 = 1.0 なので、拍に直すために 4 倍する。
 */
export function buildCursorMap(osmd: OpenSheetMusicDisplay): CursorPoint[] {
  const cursor = osmd.cursor;
  cursor.show();
  cursor.reset();
  const points: CursorPoint[] = [];
  const it = cursor.Iterator;
  let guard = 0;
  while (!it.EndReached && guard++ < 100000) {
    const el = cursor.cursorElement;
    const beat = it.currentTimeStamp.RealValue * 4;
    const left = parseFloat(el.style.left) || 0;
    const top = parseFloat(el.style.top) || 0;
    const height = parseFloat(el.style.height) || el.height || 0;
    const measure = osmd.Sheet.SourceMeasures[it.CurrentMeasureIndex]?.MeasureNumber ?? it.CurrentMeasureIndex + 1;
    if (points.length === 0 || points[points.length - 1].beat !== beat) {
      points.push({ beat, left, top, height, measure });
    }
    cursor.next();
  }
  // 末尾: 最後の音符の後に「曲の終わり」を 1 拍先として置く(補間の終端用)
  if (points.length > 0) {
    const last = points[points.length - 1];
    points.push({ ...last, beat: last.beat + 1, left: last.left + 40 });
  }
  cursor.hide();
  cursor.reset();
  return points;
}

export function interpolate(points: CursorPoint[], beat: number): CursorPoint | null {
  if (points.length === 0) return null;
  if (beat <= points[0].beat) return points[0];
  const last = points[points.length - 1];
  if (beat >= last.beat) return last;
  let lo = 0;
  let hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid].beat <= beat) lo = mid;
    else hi = mid;
  }
  const a = points[lo];
  const b = points[hi];
  const t = (beat - a.beat) / (b.beat - a.beat);
  return { beat, left: a.left + (b.left - a.left) * t, top: a.top, height: a.height, measure: a.measure };
}
