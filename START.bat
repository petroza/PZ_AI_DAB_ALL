@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [CHYBA] Chybi virtualni prostredi .venv. Spust nejdriv INSTALL.bat.
  pause
  exit /b 1
)
"%PYTHON%" -c "import fastapi,uvicorn" >nul 2>nul
if errorlevel 1 (
  echo [CHYBA] Python nebo zavislosti v .venv nejsou funkcni. Spust INSTALL.bat.
  pause
  exit /b 1
)

REM XTTS je součást aplikace: spustí se automaticky a nevyžaduje další BAT.
set "XTTS_PY=.venv_xtts\Scripts\python.exe"
if not defined XTTS_PORT set "XTTS_PORT=7868"
curl.exe -fsS --max-time 2 "http://127.0.0.1:!XTTS_PORT!/health" >nul 2>nul
if errorlevel 1 if exist "!XTTS_PY!" (
  "!XTTS_PY!" -c "import torch,TTS" >nul 2>nul
  if not errorlevel 1 (
    set "DAB_XTTS_INTERNAL=1"
    start "PZ DAB - interni XTTS" /min cmd /d /c call "%~dp0tools\start\START_XTTS.bat"
    echo [OK] Interni XTTS se automaticky spousti na GPU.
  ) else (
    echo [VAROVANI] Interni XTTS ma poskozene zavislosti. Podrobnosti: CHECK.bat
  )
)

if not defined DAB_PORT set "DAB_PORT=8790"
if not defined DAB_HOST set "DAB_HOST=0.0.0.0"

for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /R /C:"IPv4"') do (
  for /f "tokens=* delims= " %%j in ("%%i") do if not defined LANIP set "LANIP=%%j"
)
if not defined LANIP set "LANIP=127.0.0.1"

netstat -ano | findstr /R /C:":!DAB_PORT! .*LISTENING" >nul 2>nul
if not errorlevel 1 (
  echo [CHYBA] Port !DAB_PORT! uz pouziva jina aplikace.
  echo         Ukonci starou instanci, nebo nastav jiny port.
  pause
  exit /b 1
)

echo ============================================================
echo   PZ AI DAB ALL bezi
echo   Tento pocitac: http://127.0.0.1:!DAB_PORT!
echo   Mobil ve Wi-Fi: http://!LANIP!:!DAB_PORT!
echo   Ukonceni: Ctrl+C
echo ============================================================

start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:!DAB_PORT!'"
"%PYTHON%" -m uvicorn app.main:app --host !DAB_HOST! --port !DAB_PORT!
set "RC=!errorlevel!"
echo.
echo Server byl ukoncen (kod !RC!).
pause
exit /b !RC!
