@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PZ AI DAB ALL - dabing z mobilu (Cloudflare)

echo ============================================================
echo   PZ AI DAB ALL - spousteni pro dabing z MOBILU
echo   Stranka:  https://pz-ai-dab-all.pages.dev   (heslo v telefonu)
echo   Nech toto okno OTEVRENE, dokud dabujes. Ctrl+C ukonci.
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
  echo [CHYBA] Chybi .venv - spust nejdriv INSTALL.bat
  pause
  exit /b 1
)

for %%D in (uploads outputs jobs logs work) do if not exist "%%D" mkdir "%%D" >nul 2>nul

REM --- 1) XTTS hlas (na pozadi; prvni start muze trvat par minut) ---
if exist ".venv_xtts\Scripts\python.exe" (
  start "PZ DAB - XTTS" /min cmd /c call "%~dp0tools\start\START_XTTS.bat"
  echo [OK] XTTS se nacita na pozadi.
) else (
  echo [i] XTTS neni nainstalovany - pouzije se rychly cesky Piper.
)

REM --- 2) Relay worker: vyzveda zakazky z Cloudflare a dabuje je lokalne ---
echo [OK] Spoustim worker (napojeny na Cloudflare dle worker_config.json)...
echo.
call ".venv\Scripts\activate.bat"
python -u dab_worker.py

echo.
echo Worker ukoncen.
pause
