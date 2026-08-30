import { OpenSheetMusicDisplay } from "opensheetmusicdisplay";
import { buildCursorMap, interpolate, type CursorPoint } from "./cursor";
import { AudioPanel, type AudioStatus, type InputDevice } from "./audio_panel";
import { FeedbackPanel, type Analysis, type SessionInfo } from "./feedback";

const WS_URL = "ws://127.0.0.1:8765";
const LAST_SONG_KEY = "violin-accompaniment:lastSong";

interface State {
  position: number;
  tempo: number;
  confidence: number;
  playing: boolean;
  rate: number;
  length: number;
  song: string | null;
  metronome?: boolean;
  volume?: number;
  metronome_volume?: number;
  audio?: AudioStatus;
  follow?: {
    position: number; tempo: number; confidence: number; raw_position: number;
    active: boolean; lost: boolean; in_rest: boolean; enabled: boolean; mode: string;
    uniqueness: number; candidates: number;
  };
}

/** core が送る曲情報。xml は scores/ からの相対パス(例: "vivaldi_spring_1/score.mxl") */
interface Song {
  id: string;
  name: string;
  xml: string;
}

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const container = $<HTMLDivElement>("sheet-container");
const playhead = $<HTMLDivElement>("playhead");
const statusEl = $<HTMLSpanElement>("status");
const infoEl = $<HTMLSpanElement>("info");
const rateInput = $<HTMLInputElement>("rate");
const rateValue = $<HTMLSpanElement>("rate-value");
const songSelect = $<HTMLSelectElement>("song");

const osmd = new OpenSheetMusicDisplay($("sheet"), {
  autoResize: false,
  backend: "svg",
  drawTitle: false,
  drawPartNames: true,
  renderSingleHorizontalStaffline: true,
  followCursor: false,
});

let songs: Song[] = [];
let cursorMap: CursorPoint[] = [];
let displayedSong: string | null = null; // 現在描画している曲 id
let loading: Promise<void> | null = null;
let ws: WebSocket | null = null;
let latest: State | null = null;
const audioPanel = new AudioPanel(send);
const feedback = new FeedbackPanel(send, () => displayedSong, (id) => selectSong(id), () => cursorMap);

function showScore(song: Song): Promise<void> {
  if (displayedSong === song.id) return loading ?? Promise.resolve();
  displayedSong = song.id;
  playhead.style.display = "none";
  cursorMap = [];
  statusEl.textContent = `楽譜を読み込み中: ${song.name}`;
  loading = (async () => {
    await osmd.load(`./${song.xml}`);
    osmd.render();
    cursorMap = buildCursorMap(osmd);
    (window as any).__cursorMap = cursorMap; // デバッグ用
    console.log(`cursor map: ${cursorMap.length} points, last beat ${cursorMap[cursorMap.length - 1]?.beat}`);
    playhead.style.display = "block";
    container.scrollLeft = 0;
    draw(0);
    updateStatus();
    feedback.redraw();
  })().catch((e) => {
    statusEl.textContent = `楽譜の読み込みに失敗: ${e}`;
    console.error(e);
  });
  return loading;
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

function updateStatus(): void {
  const connected = ws?.readyState === WebSocket.OPEN;
  statusEl.textContent = connected ? "core 接続中" : "core 未接続(再接続中)";
  statusEl.classList.toggle("connected", connected);
}

function rememberSong(id: string): void {
  try {
    localStorage.setItem(LAST_SONG_KEY, id);
  } catch {
    /* localStorage が使えない環境では無視 */
  }
}

function recallSong(): string | null {
  try {
    return localStorage.getItem(LAST_SONG_KEY);
  } catch {
    return null;
  }
}

/** core から曲一覧を受け取ったとき。前回の曲があれば core にも切り替えを頼む。 */
function onSongs(list: Song[], current: string | null): void {
  songs = list;
  songSelect.innerHTML = "";
  for (const s of songs) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.name;
    songSelect.appendChild(opt);
  }
  sendVolumes(); // 接続(再接続)のたびに前回の音量を core に反映する
  let wanted = current;
  const last = recallSong();
  if (last && songs.some((s) => s.id === last) && last !== current) {
    wanted = last;
    send({ cmd: "load", song: last });
  }
  const song = songs.find((s) => s.id === wanted) ?? songs[0];
  if (song) {
    songSelect.value = song.id;
    showScore(song);
  }
}

/** プルダウンで曲を選んだとき。 */
function selectSong(id: string): void {
  const song = songs.find((s) => s.id === id);
  if (!song) return;
  rememberSong(id);
  songSelect.value = id;
  send({ cmd: "load", song: id });
  showScore(song);
}

function connect(): void {
  ws = new WebSocket(WS_URL);
  ws.onopen = updateStatus;
  ws.onclose = () => {
    updateStatus();
    setTimeout(connect, 1000);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") {
      latest = msg as State;
      // core 側の曲と表示がずれていたら合わせる(再接続などに備える)
      if (latest.song && latest.song !== displayedSong) {
        const song = songs.find((s) => s.id === latest!.song);
        if (song) {
          songSelect.value = song.id;
          showScore(song);
        }
      }
    } else if (msg.type === "songs") {
      onSongs(msg.songs as Song[], msg.current ?? null);
    } else if (msg.type === "devices") {
      audioPanel.setDevices(msg.devices as InputDevice[], msg.current ?? null);
    } else if (msg.type === "sessions") {
      feedback.setSessions(msg.sessions as SessionInfo[]);
    } else if (msg.type === "analysis") {
      feedback.onAnalysis(msg as Analysis);
    }
  };
}

const followBtn = $<HTMLButtonElement>("btn-follow");
let followOn = false;
const metronomeBtn = $<HTMLButtonElement>("btn-metronome");
let metronomeOn = false;
const volumeInput = $<HTMLInputElement>("volume");
const metronomeVolumeInput = $<HTMLInputElement>("metronome-volume");
const VOLUME_KEY = "violin-accompaniment.volume";
const METRONOME_VOLUME_KEY = "violin-accompaniment.metronome_volume";

function recallNumber(key: string, fallback: number): number {
  try {
    const v = parseFloat(localStorage.getItem(key) ?? "");
    return Number.isFinite(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

function rememberNumber(key: string, value: number): void {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    /* localStorage が使えない環境では無視 */
  }
}

function sendVolumes(): void {
  send({ cmd: "volume", value: parseFloat(volumeInput.value) });
  send({ cmd: "metronome_volume", value: parseFloat(metronomeVolumeInput.value) });
}
volumeInput.value = String(recallNumber(VOLUME_KEY, 1));
metronomeVolumeInput.value = String(recallNumber(METRONOME_VOLUME_KEY, 0.8));

function tick(): void {
  if (latest) audioPanel.update(latest.audio);
  if (latest && cursorMap.length > 0) {
    const f = latest.follow;
    const enabled = !!f?.enabled;
    if (enabled !== followOn) {
      followOn = enabled;
      followBtn.textContent = enabled ? "◎ 追従 ON" : "◎ 追従 OFF";
      followBtn.classList.toggle("on", enabled);
      playhead.classList.toggle("follow", enabled);
    }
    const metro = !!latest.metronome;
    if (metro !== metronomeOn) {
      metronomeOn = metro;
      metronomeBtn.textContent = metro ? "♩ メトロノーム ON" : "♩ メトロノーム OFF";
      metronomeBtn.classList.toggle("on", metro);
    }
    // 追従モードでは追従器の位置でカーソルを動かす(確信度は不透明度に)
    const pos = enabled && f ? f.position : latest.position;
    const conf = enabled && f ? f.confidence : latest.confidence;
    draw(pos, conf);
    const measure = interpolate(cursorMap, pos)?.measure ?? 0;
    let modeText = "";
    if (f?.mode === "waiting") {
      if (f.lost) modeText = f.candidates > 1 ? `聞いています(候補 ${f.candidates} 箇所)` : "待機中(音を待っています)";
      else modeText = "位置を確認中";
    } else if (f?.mode === "playing") modeText = "追従中";
    const followText = f && enabled ? `  ${modeText} ${f.position.toFixed(1)} (♩=${f.tempo.toFixed(0)}, 確信 ${Math.round(f.confidence * 100)}% 一意 ${Math.round(f.uniqueness * 100)}%)` : "";
    infoEl.textContent = `拍 ${latest.position.toFixed(2)} / ${latest.length.toFixed(0)}  小節 ${measure}  ♩=${latest.tempo.toFixed(1)}  ${latest.playing ? "再生中" : "停止"}${followText}`;
  }
  requestAnimationFrame(tick);
}

followBtn.onclick = () => send({ cmd: "follow", on: !followOn });
metronomeBtn.onclick = () => send({ cmd: "metronome", on: !metronomeOn });
volumeInput.oninput = () => {
  const v = parseFloat(volumeInput.value);
  rememberNumber(VOLUME_KEY, v);
  send({ cmd: "volume", value: v });
};
metronomeVolumeInput.oninput = () => {
  const v = parseFloat(metronomeVolumeInput.value);
  rememberNumber(METRONOME_VOLUME_KEY, v);
  send({ cmd: "metronome_volume", value: v });
};

$("btn-play").onclick = () => send({ cmd: "play" });
$("btn-stop").onclick = () => send({ cmd: "stop" });
$("btn-reset").onclick = () => send({ cmd: followOn ? "follow_reset" : "reset" });
songSelect.onchange = () => selectSong(songSelect.value);
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

connect();
requestAnimationFrame(tick);
