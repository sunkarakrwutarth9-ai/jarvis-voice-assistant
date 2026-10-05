@echo off
setlocal EnableDelayedExpansion
title J.A.R.V.I.S. setup
cd /d "%~dp0"
echo.
echo   =====================================================
echo      J.A.R.V.I.S.  -  one-time setup  (5-15 minutes)
echo   =====================================================
echo.

rem ---- 1. find a suitable Python (3.10 - 3.12)
set "PY="
for %%V in (3.11 3.12 3.10) do (
  if not defined PY (
    py -%%V --version >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [!] Python 3.10-3.12 was not found.
  choice /c YN /m "    Install Python 3.11 now with winget"
  if errorlevel 2 (
    echo     Please install Python 3.11 from https://www.python.org/downloads/ and run this again.
    pause & exit /b 1
  )
  winget install --id Python.Python.3.11 -e --accept-source-agreements --accept-package-agreements
  set "PY=py -3.11"
  !PY! --version >nul 2>&1 || (
    echo     Python was installed - please close this window and run run.bat again.
    pause & exit /b 1
  )
)
echo [1/5] Using !PY!
!PY! --version

rem ---- 2. private environment (does not touch your other Python packages)
if not exist ".venv\Scripts\python.exe" (
  echo [2/5] Creating private environment .venv ...
  !PY! -m venv .venv || (echo [!] Could not create the environment. & pause & exit /b 1)
) else (
  echo [2/5] Private environment already exists.
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip --quiet

rem ---- 3. PyTorch: GPU build if an NVIDIA card is present, otherwise CPU build
where nvidia-smi >nul 2>&1
if !errorlevel! == 0 (
  echo [3/5] NVIDIA GPU found - installing PyTorch with CUDA (about 2.5 GB^)...
  "%VPY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu128 --quiet
) else (
  echo [3/5] No NVIDIA GPU - installing the CPU build of PyTorch...
  "%VPY%" -m pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet
)

rem ---- 4. everything else
echo [4/5] Installing Jarvis's packages...
"%VPY%" -m pip install -r requirements.txt --quiet || (
  echo [!] Some packages failed to install. Check your internet connection and run setup.bat again.
  pause & exit /b 1
)

rem ---- 5. models that are too big for GitHub
echo [5/5] Downloading models...
"%VPY%" setup_assets.py || (echo [!] Model download failed - run setup.bat again later. & pause & exit /b 1)

echo.
echo   Setup complete.  Start Jarvis with  run.bat
echo   (On first start it asks for a free Gemini API key: https://aistudio.google.com/apikey )
echo.
endlocal
exit /b 0
