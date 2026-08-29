// Electron メインプロセス。
// - core(Python)を子プロセスとして起動し、終了時に止める
// - 開発時: VITE_DEV_SERVER_URL があればそこを、無ければ dist/index.html を表示
// - パッケージ時: resources/core/violin_core.exe と resources/scores/ を使う
const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

const CORE_PORT = 8765;

let coreProcess = null;
let mainWindow = null;

function repoRoot() {
  // ui/electron/main.cjs → リポジトリ直下
  return path.resolve(__dirname, "..", "..");
}

/** core の起動コマンドを返す。パッケージ時は同梱 exe、開発時は venv の python。 */
function resolveCoreLaunch() {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, "core", "violin_core.exe");
    const scores = path.join(process.resourcesPath, "scores");
    return { cmd: exe, args: ["--scores-dir", scores, "--port", String(CORE_PORT)], cwd: path.dirname(exe) };
  }
  const root = repoRoot();
  const python = path.join(root, "core", ".venv", "Scripts", "python.exe");
  const scores = path.join(root, "scores");
  return { cmd: python, args: ["-m", "violin_core", "--scores-dir", scores, "--port", String(CORE_PORT)], cwd: path.join(root, "core") };
}

function startCore() {
  const { cmd, args, cwd } = resolveCoreLaunch();
  if (!fs.existsSync(cmd)) {
    dialog.showErrorBox("core が見つかりません", `${cmd}\n\n開発時は core/.venv を作成してください(README.md 参照)。`);
    return;
  }
  coreProcess = spawn(cmd, args, { cwd, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
  coreProcess.stdout.on("data", (d) => process.stdout.write(`[core] ${d}`));
  coreProcess.stderr.on("data", (d) => process.stderr.write(`[core] ${d}`));
  coreProcess.on("exit", (code) => {
    console.log(`[core] exited with ${code}`);
    coreProcess = null;
  });
}

function stopCore() {
  if (coreProcess && !coreProcess.killed) {
    coreProcess.kill();
  }
  coreProcess = null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 600,
    title: "violin-accompaniment",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  mainWindow.setMenuBarVisibility(false);
  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    mainWindow.loadURL(devUrl);
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  startCore();
  createWindow();
});

app.on("window-all-closed", () => {
  stopCore();
  app.quit();
});

app.on("before-quit", stopCore);
