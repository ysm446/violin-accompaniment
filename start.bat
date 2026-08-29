@echo off
rem Development launcher: build the ui and open the Electron app (core is started by Electron).
rem For the standalone build see README.md (npm run dist -> ui\release\).
rem NOTE: keep this file ASCII-only; cmd.exe mis-parses UTF-8 text.
setlocal
cd /d "%~dp0"

if not exist core\.venv\Scripts\python.exe (
  echo [start] core\.venv not found. See README.md for setup.
  pause
  exit /b 1
)
if not exist ui\node_modules (
  echo [start] ui\node_modules not found. Run: npm --prefix ui install
  pause
  exit /b 1
)

rem VS Code sets this and it makes Electron run as plain Node.
set ELECTRON_RUN_AS_NODE=

cd ui
call npm run app
endlocal
