@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==================================================
echo   PZ AI DAB ALL - spoustim VSE
echo   1) XTTS server (kvalitni cesky hlas, GPU)
echo   2) Worker (web na http://127.0.0.1:8790)
echo ==================================================

REM XTTS server v samostatnem okne (pokud je nainstalovany)
if exist ".venv_xtts\Scripts\python.exe" (
  start "PZ DAB - XTTS hlas" cmd /c "START_XTTS.bat"
  echo [OK] XTTS server se spousti v samostatnem okne (prvni start nacita model ~15s).
) else (
  echo [!]  XTTS neni nainstalovany - dabing pojede jen na Piperu.
  echo      Kvalitni cesky hlas: viz README (sekce XTTS).
)

REM chvili pockej, at XTTS nabehne driv nez worker otevre prohlizec
timeout /t 4 /nobreak >nul

REM Worker (tohle okno)
call START.bat
