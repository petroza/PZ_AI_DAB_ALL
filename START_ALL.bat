@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title PZ AI DAB ALL - hlavni spoustec
echo ============================================================
echo   PZ AI DAB ALL - START ALL
echo   Spusti lokalni web, XTTS a internetovy relay worker.
echo ============================================================

set "MAIN_PY=.venv\Scripts\python.exe"
set "XTTS_PY=.venv_xtts\Scripts\python.exe"

if not exist "%MAIN_PY%" goto :missing_main
"%MAIN_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
if errorlevel 1 goto :broken_main
"%MAIN_PY%" -c "import fastapi, uvicorn, requests, faster_whisper, argostranslate, piper" >nul 2>nul
if errorlevel 1 goto :broken_deps

for %%D in (uploads outputs jobs logs work) do if not exist "%%D" mkdir "%%D" >nul 2>nul

if not defined XTTS_PORT set "XTTS_PORT=7868"
curl.exe -fsS --max-time 2 "http://127.0.0.1:!XTTS_PORT!/health" >nul 2>nul
if not errorlevel 1 (
  echo [OK] XTTS uz bezi na portu !XTTS_PORT!.
) else if exist "%XTTS_PY%" (
  "%XTTS_PY%" -c "import torch, TTS" >nul 2>nul
  if errorlevel 1 (
    echo [VAROVANI] XTTS balicky nejsou funkcni. Pouzije se rychly cesky Piper.
  ) else (
    start "PZ DAB - XTTS" /min cmd /d /c call "%~dp0tools\start\START_XTTS.bat"
    echo [OK] XTTS se nacita na pozadi. Prvni start muze trvat nekolik minut.
  )
) else (
  echo [VAROVANI] XTTS neni nainstalovany. Pouzije se rychly cesky Piper.
)

REM Relay worker obsluhuje zakazky zadane pres vzdaleny web. Nespoustej druhou kopii.
tasklist /fi "WINDOWTITLE eq PZ DAB - relay worker*" 2>nul | find /i "cmd.exe" >nul
if errorlevel 1 (
  start "PZ DAB - relay worker" /min cmd /d /c call "%~dp0tools\start\START_DABWORKER.bat"
  echo [OK] Internetovy relay worker byl spusten na pozadi.
) else (
  echo [OK] Internetovy relay worker uz bezi.
)

echo [OK] Spoustim lokalni aplikaci: http://127.0.0.1:8790
echo.
call "%~dp0START.bat"
exit /b %errorlevel%

:missing_main
echo [CHYBA] Chybi hlavni prostredi .venv.
echo         Spust INSTALL.bat a potom znovu START_ALL.bat.
pause
exit /b 1

:broken_main
echo [CHYBA] Python v .venv je poskozeny nebo starsi nez 3.11.
echo         Spust INSTALL.bat a potom znovu START_ALL.bat.
pause
exit /b 1

:broken_deps
echo [CHYBA] V .venv chybi nektere povinne balicky.
echo         Spust INSTALL.bat a potom znovu START_ALL.bat.
pause
exit /b 1
