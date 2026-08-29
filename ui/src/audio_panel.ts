/** 入力デバイス選択・レベルメータ・chroma 表示・記録ボタン(audiobar)。 */

export interface AudioStatus {
  source: string;
  level_db: number;
  chroma: number[];
  flux: number;
  latency_ms: number;
  frames: number;
  overruns: number;
  recording: boolean;
  recording_dir: string | null;
}

export interface InputDevice {
  id: number;
  name: string;
  hostapi: string;
  samplerate: number;
}

const PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export class AudioPanel {
  private inputSelect = document.getElementById("input") as HTMLSelectElement;
  private meterFill = document.getElementById("meter-fill") as HTMLDivElement;
  private meterDb = document.getElementById("meter-db") as HTMLSpanElement;
  private canvas = document.getElementById("chroma") as HTMLCanvasElement;
  private audioInfo = document.getElementById("audio-info") as HTMLSpanElement;
  private recordBtn = document.getElementById("btn-record") as HTMLButtonElement;
  private recordInfo = document.getElementById("record-info") as HTMLSpanElement;
  private recording = false;

  constructor(private send: (cmd: object) => void) {
    this.inputSelect.onchange = () => {
      const v = this.inputSelect.value;
      this.send({ cmd: "input", device: v === "" ? null : Number(v) });
    };
    this.recordBtn.onclick = () => this.send({ cmd: "record", on: !this.recording });
  }

  setDevices(devices: InputDevice[], current: number | null): void {
    this.inputSelect.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "(入力なし)";
    this.inputSelect.appendChild(none);
    for (const d of devices) {
      const opt = document.createElement("option");
      opt.value = String(d.id);
      opt.textContent = `${d.name} [${d.hostapi}]`;
      this.inputSelect.appendChild(opt);
    }
    this.inputSelect.value = current === null ? "" : String(current);
  }

  update(a: AudioStatus | undefined): void {
    if (!a) return;
    // レベル: -60 dB → 0%、0 dB → 100%
    const pct = Math.max(0, Math.min(100, (a.level_db + 60) / 60 * 100));
    this.meterFill.style.width = `${pct}%`;
    this.meterDb.textContent = a.level_db <= -99 ? "-∞ dB" : `${a.level_db.toFixed(0)} dB`;
    this.drawChroma(a.chroma);
    this.audioInfo.textContent = `遅延 ${a.latency_ms.toFixed(0)} ms  flux ${a.flux.toFixed(1)}${a.overruns ? `  取りこぼし ${a.overruns}` : ""}`;
    if (a.recording !== this.recording) {
      this.recording = a.recording;
      this.recordBtn.textContent = a.recording ? "■ 記録停止" : "● 記録開始";
      this.recordBtn.classList.toggle("on", a.recording);
    }
    this.recordInfo.textContent = a.recording && a.recording_dir ? `記録中: ${a.recording_dir}` : "";
  }

  private drawChroma(chroma: number[]): void {
    const ctx = this.canvas.getContext("2d");
    if (!ctx) return;
    const w = this.canvas.width;
    const h = this.canvas.height;
    ctx.clearRect(0, 0, w, h);
    const bw = w / 12;
    for (let i = 0; i < 12; i++) {
      const v = chroma[i] ?? 0;
      const bh = Math.round(v * (h - 12));
      ctx.fillStyle = v > 0.6 ? "#f8c34a" : "#2a7";
      ctx.fillRect(i * bw + 1, h - 12 - bh, bw - 2, bh);
      ctx.fillStyle = "#aaa";
      ctx.font = "9px system-ui";
      ctx.textAlign = "center";
      ctx.fillText(PITCH_CLASSES[i], i * bw + bw / 2, h - 2);
    }
  }
}
