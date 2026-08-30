@echo off
rem Development launcher: build the ui and open the Electron app (core is started by Electron).
rem For the standalone build see README.md (npm run dist -> ui\release\).
rem NOTE: keep this file ASCII-only; cmd.exe mis-parses UTF-8 text.
setlocal
cd /d "%~dp0"

if not exist core\.venv\Scripts\python.exe (
  call :setup_venv
  if errorlevel 1 exit /b 1
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
exit /b 0

rem ---------------------------------------------------------------
rem Create core\.venv and install core\requirements.txt into it.
:setup_venv
echo [start] core\.venv not found. Creating it...
set "PYCMD="
where py >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto :setup_venv_run
where python >nul 2>nul
if not errorlevel 1 set "PYCMD=python"

:setup_venv_run
if not defined PYCMD (
  echo [start] Python 3 not found on PATH. Install it from https://www.python.org/ and run start.bat again.
  pause
  exit /b 1
)
%PYCMD% -m venv core\.venv
if errorlevel 1 goto :setup_venv_failed
echo [start] Installing core requirements...
core\.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r core\requirements.txt
if errorlevel 1 goto :setup_venv_failed
echo [start] core\.venv is ready.
exit /b 0

:setup_venv_failed
echo [start] Failed to set up core\.venv. See the messages above.
rem Remove the half-built venv so the next run starts clean.
if exist core\.venv rmdir /s /q core\.venv
pause
exit /b 1
