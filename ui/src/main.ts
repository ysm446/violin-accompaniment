import { OpenSheetMusicDisplay } from "opensheetmusicdisplay";
import { buildCursorMap, interpolate, type CursorPoint } from "./cursor";

const SCORE_URL = "./vivaldi_spring_first_movement_20251102.mxl";
const WS_URL = "ws://127.0.0.1:8765";

interface State {
  position: number;
  tempo: number;
  confidence: number;
  playing: boolean;
  rate: number;
  length: number;
}

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const container = $<HTMLDivElement>("sheet-container");
const playhead = $<HTMLDivElement>("playhead");
const statusEl = $<HTMLSpanElement>("status");
const infoEl = $<HTMLSpanElement>("info");
const rateInput = $<HTMLInputElement>("rate");
const rateValue = $<HTMLSpanElement>("rate-value");

let cursorMap: CursorPoint[] = [];
let ws: WebSocket | null = null;
let latest: State | null = null;

async function loadScore(): Promise<void> {
  const osmd = new OpenSheetMusicDisplay($("sheet"), {
    autoResize: false,
    backend: "svg",
    drawTitle: false,
    drawPartNames: true,
    renderSingleHorizontalStaffline: true,
    followCursor: false,
  });
  await osmd.load(SCORE_URL);
  osmd.render();
  cursorMap = buildCursorMap(osmd);
  (window as any).__cursorMap = cursorMap; // デバッグ用
  console.log(`cursor map: ${cursorMap.length} points, last beat ${cursorMap[cursorMap.length - 1]?.beat}`);
  playhead.style.display = "block";
  draw(0);
}

function draw(beat: number, confidence = 1): void {
  const p = interpolate(cursorMap, beat);
  if (!p) return;
  playhead.style.left = `${p.left}px`;
  playhead.style.opacity = String(0.3 + 0.7 * confidence);
  const target = p.left - container.clientWidth / 3;
  container.scrollLeft = Math.max(0, target);
}

function send(cmd: object): void {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(cmd));
}

function connect(): void {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    statusEl.textContent = "core 接続中";
    statusEl.classList.add("connected");
  };
  ws.onclose = () => {
    statusEl.textContent = "core 未接続(再接続中)";
    statusEl.classList.remove("connected");
    setTimeout(connect, 1000);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") latest = msg as State;
  };
}

function tick(): void {
  if (latest) {
    draw(latest.position, latest.confidence);
    const measure = interpolate(cursorMap, latest.position)?.measure ?? 0;
    infoEl.textContent = `拍 ${latest.position.toFixed(2)} / ${latest.length.toFixed(0)}  小節 ${measure}  ♩=${latest.tempo.toFixed(1)}  ${latest.playing ? "再生中" : "停止"}`;
  }
  requestAnimationFrame(tick);
}

$("btn-play").onclick = () => send({ cmd: "play" });
$("btn-stop").onclick = () => send({ cmd: "stop" });
$("btn-reset").onclick = () => send({ cmd: "reset" });
rateInput.oninput = () => {
  const v = parseFloat(rateInput.value);
  rateValue.textContent = v.toFixed(2);
  send({ cmd: "rate", value: v });
};
container.onclick = (ev) => {
  // 譜面クリックで最も近い拍へシーク
  if (cursorMap.length === 0) return;
  const x = ev.clientX - container.getBoundingClientRect().left + container.scrollLeft;
  let best = cursorMap[0];
  for (const p of cursorMap) if (Math.abs(p.left - x) < Math.abs(best.left - x)) best = p;
  send({ cmd: "seek", beat: best.beat });
};

loadScore().catch((e) => {
  statusEl.textContent = `楽譜の読み込みに失敗: ${e}`;
  console.error(e);
});
connect();
requestAnimationFrame(tick);
