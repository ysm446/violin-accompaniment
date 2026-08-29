/** 演奏後フィードバック: 記録セッションの選択・解析・譜面へのオーバーレイ。 */

import { interpolate, type CursorPoint } from "./cursor";

export interface SessionInfo {
  id: string;
  song: string | null;
  started_at: string | null;
  duration_sec: number;
  analyzed: boolean;
}

export interface NoteResult {
  index: number;
  beat: number;
  duration: number;
  midi: number;
  played: boolean;
  onset_time?: number;
  timing_ms?: number | null;
  cents?: number | null;
  f0?: number | null;
  local_bpm?: number | null;
}

export interface Analysis {
  session: string;
  song: string | null;
  status?: string;
  error?: string;
  summary?: {
    notes_total: number;
    notes_played: number;
    first_beat: number | null;
    last_beat: number | null;
    median_abs_cents: number | null;
    mean_cents: number | null;
    median_abs_timing_ms: number | null;
    dtw_cost: number;
    elapsed_sec: number;
  };
  notes?: NoteResult[];
}

const CENTS_OK = 15;
const CENTS_WARN = 35;
const TIMING_SCALE = 0.15; // px / ms

function centsColor(c: number | null | undefined): string {
  if (c === null || c === undefined) return "#888";
  const a = Math.abs(c);
  if (a <= CENTS_OK) return "#2a7";
  if (a <= CENTS_WARN) return "#dc3";
  return "#d33";
}

export class FeedbackPanel {
  private select = document.getElementById("session") as HTMLSelectElement;
  private analyzeBtn = document.getElementById("btn-analyze") as HTMLButtonElement;
  private clearBtn = document.getElementById("btn-clear-feedback") as HTMLButtonElement;
  private summaryEl = document.getElementById("feedback-summary") as HTMLSpanElement;
  private layer = document.getElementById("feedback-layer") as HTMLDivElement;
  private sessions: SessionInfo[] = [];
  private analysis: Analysis | null = null;

  constructor(
    private send: (cmd: object) => void,
    private currentSong: () => string | null,
    private selectSong: (id: string) => void,
    private cursorMap: () => CursorPoint[],
  ) {
    this.analyzeBtn.onclick = () => {
      const id = this.select.value;
      if (!id) return;
      this.summaryEl.textContent = "解析中…";
      this.send({ cmd: "analyze", session: id });
    };
    this.clearBtn.onclick = () => this.clear();
  }

  setSessions(list: SessionInfo[]): void {
    this.sessions = list;
    const prev = this.select.value;
    this.select.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = list.length ? "記録を選択…" : "(記録なし)";
    this.select.appendChild(none);
    for (const s of list) {
      const opt = document.createElement("option");
      opt.value = s.id;
      const when = s.started_at ? s.started_at.replace("T", " ") : s.id;
      opt.textContent = `${when}  ${s.song ?? "?"}  ${s.duration_sec.toFixed(0)}s${s.analyzed ? " ✓" : ""}`;
      this.select.appendChild(opt);
    }
    if (prev && list.some((s) => s.id === prev)) this.select.value = prev;
  }

  onAnalysis(a: Analysis): void {
    if (a.status === "running") {
      this.summaryEl.textContent = "解析中…";
      return;
    }
    if (a.error) {
      this.summaryEl.textContent = `解析エラー: ${a.error}`;
      return;
    }
    this.analysis = a;
    const s = a.summary!;
    this.summaryEl.textContent =
      `${s.notes_played}/${s.notes_total} 音  拍 ${s.first_beat ?? "?"}〜${s.last_beat ?? "?"}  ` +
      `音程 |中央値| ${s.median_abs_cents ?? "-"}c(平均 ${s.mean_cents ?? "-"}c)  タイミング |中央値| ${s.median_abs_timing_ms ?? "-"}ms`;
    // 曲が違えば切り替えてから描く(描画完了後に redraw が呼ばれる)
    if (a.song && a.song !== this.currentSong()) this.selectSong(a.song);
    else this.redraw();
  }

  clear(): void {
    this.analysis = null;
    this.layer.innerHTML = "";
    this.summaryEl.textContent = "";
  }

  /** 譜面を描き直した後に呼ぶ。 */
  redraw(): void {
    this.layer.innerHTML = "";
    const a = this.analysis;
    const map = this.cursorMap();
    if (!a || !a.notes || map.length === 0 || a.song !== this.currentSong()) return;
    const frag = document.createDocumentFragment();
    for (const n of a.notes) {
      if (!n.played) continue;
      const p = interpolate(map, n.beat);
      if (!p) continue;
      const el = document.createElement("div");
      el.className = "fb-note";
      el.style.left = `${p.left - 5}px`;
      el.style.top = `${Math.max(0, p.top - 18)}px`;
      el.style.background = centsColor(n.cents);
      const timing = n.timing_ms ?? 0;
      if (n.timing_ms !== null && n.timing_ms !== undefined && Math.abs(timing) > 30) {
        const bar = document.createElement("div");
        bar.className = "fb-timing";
        const w = Math.min(40, Math.abs(timing) * TIMING_SCALE);
        bar.style.width = `${w}px`;
        bar.style.left = timing < 0 ? `${5 - w}px` : "5px";
        bar.style.background = timing < 0 ? "#39c" : "#e83";
        el.appendChild(bar);
      }
      el.title =
        `音符 #${n.index}  拍 ${n.beat}\n` +
        `音程: ${n.cents === null || n.cents === undefined ? "?" : `${n.cents > 0 ? "+" : ""}${n.cents} セント`}\n` +
        `タイミング: ${n.timing_ms === null || n.timing_ms === undefined ? "?" : `${n.timing_ms > 0 ? "+" : ""}${n.timing_ms} ms(+ は遅れ)`}\n` +
        `発音 ${n.onset_time}s  局所テンポ ♩=${n.local_bpm ?? "?"}`;
      frag.appendChild(el);
    }
    this.layer.appendChild(frag);
  }
}
