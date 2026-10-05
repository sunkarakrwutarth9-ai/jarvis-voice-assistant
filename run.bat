@echo off
rem  Double-click to start J.A.R.V.I.S.  (first time: sets everything up automatically)
rem  run.bat console   -> run with a visible log window (for troubleshooting)
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  call setup.bat || exit /b 1
)
if /i "%~1"=="console" (
  ".venv\Scripts\python.exe" jarvis.py
  pause
  exit /b
)
start "" ".venv\Scripts\pythonw.exe" jarvis.py
echo.
echo   J.A.R.V.I.S. is starting... look for the island at the top of your screen.
echo   Say "OK Jarvis", or open the command center: http://localhost:7777
echo.
timeout /t 6 >nul
