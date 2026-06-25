@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo   PZ AI DAB ALL - DIAGNOSTIKA
echo ==================================================

for %%D in (uploads outputs jobs logs work models voices tools\parakeet tools\ffmpeg tools\piper) do (
  if not exist "%%D" mkdir "%%D"
)

where python >nul 2>nul && (
  for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
  echo [OK] Python !PYVER!
) || echo [CHYBA] Python neni v PATH

if exist ".venv\Scripts\python.exe" (echo [OK] virtualenv .venv) else (echo [!]  .venv chybi - spust INSTALL.bat)

set FFOK=0
where ffmpeg >nul 2>nul && set FFOK=1
if "!FFOK!"=="0" if exist "tools\ffmpeg\ffmpeg.exe" set FFOK=1
if "!FFOK!"=="1" (echo [OK] ffmpeg) else (echo [!]  ffmpeg nenalezen)

set ASROK=0
dir /b /s "tools\parakeet\parakeet-cli.exe" >nul 2>nul && set ASROK=1
if "!ASROK!"=="0" where parakeet-cli >nul 2>nul && set ASROK=1
if "!ASROK!"=="1" (
  echo [OK] parakeet-cli
  dir /b "models\*.gguf" >nul 2>nul
  if errorlevel 1 (echo [!]  zadny .gguf model v models\) else (echo [OK] ASR model .gguf v models\)
) else (
  .venv\Scripts\python.exe -c "import faster_whisper" >nul 2>nul
  if errorlevel 1 (echo [!]  ASR neni - nainstaluj parakeet-cli nebo: pip install faster-whisper) else (echo [OK] faster-whisper (Python ASR))
)

set PIPEROK=0
where piper >nul 2>nul && set PIPEROK=1
if "!PIPEROK!"=="0" if exist "tools\piper\piper.exe" set PIPEROK=1
if "!PIPEROK!"=="0" .venv\Scripts\python.exe -c "import piper" >nul 2>nul && set PIPEROK=1
if "!PIPEROK!"=="1" (echo [OK] Piper) else (echo [!]  Piper nenalezen - pip install piper-tts)

dir /b "voices\*.onnx" >nul 2>nul
if errorlevel 1 (echo [i]  zadny Piper hlas v voices\ - stahne se automaticky pri dabingu) else (echo [OK] Piper hlas v voices\)

REM zapis do slozek
for %%D in (uploads outputs jobs logs work) do (
  >"%%D\.write_test" echo test 2>nul
  if exist "%%D\.write_test" ( del "%%D\.write_test" >nul 2>nul & echo [OK] zapis do %%D ) else ( echo [CHYBA] nelze zapsat do %%D )
)

if not defined DAB_PORT set DAB_PORT=8790
netstat -ano | findstr ":!DAB_PORT! " >nul 2>nul
if errorlevel 1 (echo [OK] port !DAB_PORT! je volny) else (echo [!]  port !DAB_PORT! je OBSAZENY)

REM Preklad — Ollama nebo argostranslate
curl -s -m 5 http://127.0.0.1:11434/api/tags >nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python.exe -c "import argostranslate" >nul 2>nul
  if errorlevel 1 (echo [!]  Prekladac neni - spust Ollama nebo: pip install argostranslate) else (echo [OK] argostranslate nalezen (offline preklad))
) else (echo [OK] Ollama bezi - preklad aktivni)

REM PZ Voice Studio (volitelny TTS backend)
curl -s -m 5 http://127.0.0.1:7867/api/ping >nul 2>nul
if errorlevel 1 (echo [i]  PZ Voice Studio nebezi ^(jen pokud chces Chatterbox/klonovani^)) else (echo [OK] PZ Voice Studio bezi na 7867)

echo ==================================================
echo   Konec diagnostiky
echo ==================================================
pause
