@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv_xtts\Scripts\python.exe" (
  echo [CHYBA] Chybi prostredi .venv_xtts (torch + coqui-tts).
  echo         Nainstaluj: python -m venv .venv_xtts ^&^& .venv_xtts\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124 ^&^& .venv_xtts\Scripts\python -m pip install coqui-tts
  pause
  exit /b 1
)

if not defined XTTS_PORT set XTTS_PORT=7868

echo ==================================================
echo   XTTS v2 server (kvalitni cesky hlas + klonovani)
echo   http://127.0.0.1:%XTTS_PORT%   (Ctrl+C ukonci)
echo   Prvni spusteni stahne model (~1.8 GB).
echo ==================================================

set COQUI_TOS_AGREED=1
".venv_xtts\Scripts\python.exe" tools\xtts_server.py

echo.
echo Server byl ukoncen.
pause
