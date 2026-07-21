@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON=.venv_xtts\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [CHYBA] Chybi prostredi .venv_xtts.
  pause
  exit /b 1
)
"%PYTHON%" -c "import torch, TTS" >nul 2>nul
if errorlevel 1 (
  echo [CHYBA] XTTS prostredi je poskozene nebo nema potrebne balicky.
  pause
  exit /b 1
)

if not defined XTTS_PORT set "XTTS_PORT=7868"
set "COQUI_TOS_AGREED=1"
set "TTS_HOME=%~dp0models\tts"
set "HF_HOME=%~dp0models\huggingface"
title PZ DAB - XTTS hlas
echo ============================================================
echo   XTTS v2 - kvalitni cesky hlas a klonovani
echo   http://127.0.0.1:%XTTS_PORT%/health
echo   Prvni nacteni modelu muze trvat nekolik minut.
echo ============================================================
"%PYTHON%" -u tools\xtts_server.py
set "RC=%errorlevel%"
echo XTTS byl ukoncen (kod %RC%).
if not defined DAB_XTTS_INTERNAL pause
exit /b %RC%
