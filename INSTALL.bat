@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo   PZ AI DAB ALL - INSTALACE
echo ==================================================
echo.

REM pracovni slozky
for %%D in (uploads outputs jobs logs work models voices tools\parakeet tools\ffmpeg tools\piper tools\rubberband) do (
  if not exist "%%D" mkdir "%%D"
)

REM 1) Python
where python >nul 2>nul
if errorlevel 1 (
  echo [CHYBA] Python nebyl nalezen v PATH.
  echo         Nainstaluj Python 3.11+ z https://www.python.org/downloads/
  echo         a pri instalaci zaskrtni "Add python.exe to PATH".
  pause
  exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python !PYVER!

REM 2) virtualni prostredi
if not exist ".venv\Scripts\python.exe" (
  echo [..] Vytvarim virtualni prostredi .venv ...
  python -m venv .venv
  if errorlevel 1 ( echo [CHYBA] Nepodarilo se vytvorit .venv & pause & exit /b 1 )
)
call ".venv\Scripts\activate.bat"
echo [OK] virtualni prostredi .venv

REM 3) zavislosti
echo [..] Instaluji zavislosti...
python -m pip install --upgrade pip >nul 2>nul
pip install -r requirements.txt
if errorlevel 1 ( echo [CHYBA] Instalace zavislosti selhala. & pause & exit /b 1 )
echo [OK] Zavislosti nainstalovany.
echo.

echo --------------------------------------------------
echo   KONTROLA EXTERNICH NASTROJU
echo --------------------------------------------------

REM 4) ffmpeg
set FFOK=0
where ffmpeg >nul 2>nul && set FFOK=1
if "!FFOK!"=="0" if exist "tools\ffmpeg\ffmpeg.exe" set FFOK=1
if "!FFOK!"=="1" ( echo [OK] ffmpeg nalezen ) else (
  echo [!]  ffmpeg NENALEZEN - stahni z https://www.gyan.dev/ffmpeg/builds/
  echo      a dej ffmpeg.exe + ffprobe.exe do  %CD%\tools\ffmpeg\
)

REM 5) parakeet.cpp (ASR)
dir /b /s "tools\parakeet\parakeet-cli.exe" >nul 2>nul
if errorlevel 1 (
  where parakeet-cli >nul 2>nul && ( echo [OK] parakeet-cli v PATH ) || (
    echo [!]  parakeet-cli.exe NENALEZEN - https://github.com/mudler/parakeet.cpp/releases
    echo      dej parakeet-cli.exe do  %CD%\tools\parakeet\ )
) else ( echo [OK] parakeet-cli nalezen v tools\parakeet )

REM 6) ASR model
dir /b "models\*.gguf" >nul 2>nul
if errorlevel 1 (
  echo [!]  Zadny .gguf model ve  models\
  echo      Doporuceno: tdt-0.6b-v3-q8_0.gguf z https://huggingface.co/mudler/parakeet-cpp-gguf
) else ( echo [OK] ASR model .gguf nalezen v models\ )

REM 7) Piper (TTS) + cesky hlas
set PIPEROK=0
where piper >nul 2>nul && set PIPEROK=1
if "!PIPEROK!"=="0" if exist "tools\piper\piper.exe" set PIPEROK=1
if "!PIPEROK!"=="1" ( echo [OK] Piper nalezen ) else (
  echo [!]  Piper NENALEZEN - https://github.com/rhasspy/piper/releases
  echo      dej piper.exe do  %CD%\tools\piper\   (nebo: pip install piper-tts)
)
dir /b "voices\cs_CZ*.onnx" >nul 2>nul
if errorlevel 1 (
  echo [!]  Chybi cesky Piper hlas. Stahni cs_CZ-jirka-medium.onnx ^(+ .onnx.json^)
  echo      z https://huggingface.co/rhasspy/piper-voices  do  %CD%\voices\
) else ( echo [OK] cesky Piper hlas nalezen v voices\ )

echo.
echo ==================================================
echo   Hotovo. Aplikaci spustis pres  START.bat
echo   (volitelne: Ollama pro preklad, PZ Voice Studio pro Chatterbox)
echo ==================================================
pause
