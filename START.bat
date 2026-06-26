@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo [CHYBA] Chybi virtualni prostredi .venv
  echo         Spust nejdriv INSTALL.bat
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"

if not defined DAB_PORT set DAB_PORT=8790
REM 0.0.0.0 = dostupne i z mobilu/jinych zarizeni na stejne WiFi (LAN).
REM Pro pouze-lokalni (bezpecnejsi) nastav:  set DAB_HOST=127.0.0.1
if not defined DAB_HOST set DAB_HOST=0.0.0.0

REM Zjisti LAN IP (pro mobil) – prvni 10.x / 192.168.x / 172.x adresa.
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /R /C:"IPv4"') do (
  for /f "tokens=* delims= " %%j in ("%%i") do if not defined LANIP set LANIP=%%j
)
if not defined LANIP set LANIP=127.0.0.1

netstat -ano | findstr ":!DAB_PORT! " >nul 2>nul
if not errorlevel 1 (
  echo [CHYBA] Port !DAB_PORT! je jiz obsazen.
  echo         Zastav predchozi instanci nebo nastav jiny port:
  echo         set DAB_PORT=8791  ^&^&  START.bat
  pause
  exit /b 1
)

echo ==================================================
echo   PZ AI DAB ALL bezi
echo   Na tomto PC:  http://127.0.0.1:!DAB_PORT!
echo   Z mobilu/WiFi: http://!LANIP!:!DAB_PORT!
echo   (zastavis ho zde klavesami Ctrl+C)
echo ==================================================

REM Otevri prohlizec se zpozdenim, az server nabehne (lokalne 127.0.0.1).
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:!DAB_PORT!'"

python -m uvicorn app.main:app --host !DAB_HOST! --port !DAB_PORT!

echo.
echo Server byl ukoncen.
pause
