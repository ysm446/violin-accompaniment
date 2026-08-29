@echo off
rem Launch core (Python) and ui (Vite) in separate windows, then open the browser.
rem Usage: start.bat [ui_port]   (default 5173)
rem NOTE: keep this file ASCII-only; cmd.exe mis-parses UTF-8 text.
setlocal
cd /d "%~dp0"

set MIDI=..\muse-score\vivaldi_spring_first_movement_20251102.mid
set UI_PORT=5173
if not "%~1"=="" set UI_PORT=%~1

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

start "violin core" /d "%~dp0core" cmd /k .venv\Scripts\python.exe -m violin_core --midi %MIDI%
start "violin ui" /d "%~dp0ui" cmd /k npx vite --port %UI_PORT% --strictPort

set /a tries=0
:wait
set /a tries+=1
netstat -an | findstr /r /c:":%UI_PORT% .*LISTENING" >nul
if not errorlevel 1 goto ready
if %tries% geq 30 (
  echo [start] ui did not start on port %UI_PORT%. Check the "violin ui" window.
  echo [start] If the port is in use, try another one: start.bat 5199
  pause
  exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto wait

:ready
start "" http://localhost:%UI_PORT%/
echo [start] core and ui are running. Close the "violin core" and "violin ui" windows to stop.
endlocal
