@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo [CHYBA] Chybi virtualni prostredi .venv
  echo         Spust nejdriv INSTALL.bat
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"

echo ==================================================
echo   PZ AI DAB ALL bezi na http://127.0.0.1:8790
echo   (zastavis ho zde klavesami Ctrl+C)
echo ==================================================

REM Otevri prohlizec se zpozdenim, az server nabehne.
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8790'"

python -m uvicorn app.main:app --host 127.0.0.1 --port 8790

echo.
echo Server byl ukoncen.
pause
