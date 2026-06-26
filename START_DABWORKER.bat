@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo [CHYBA] Chybi .venv - spust nejdriv INSTALL.bat
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"

echo ======================================================
echo   PZ AI DAB ALL - relay worker (dabing z mobilu/webu)
echo   Web:  https://www.appcrate.cloud/ALLDUB/  (login PetrZ)
echo   Pro kvalitni cesky hlas (XTTS) spust take START_XTTS.bat
echo   (zastavis ho zde klavesami Ctrl+C)
echo ======================================================

python dab_worker.py

echo.
echo Worker ukoncen.
pause
