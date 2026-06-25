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

dir /b /s "tools\parakeet\parakeet-cli.exe" >nul 2>nul
if errorlevel 1 (
  where parakeet-cli >nul 2>nul && (echo [OK] parakeet-cli v PATH) || (echo [!]  parakeet-cli nenalezen)
) else (echo [OK] parakeet-cli v tools\parakeet)

dir /b "models\*.gguf" >nul 2>nul
if errorlevel 1 (echo [!]  zadny .gguf v models\) else (echo [OK] ASR model .gguf v models\)

set PIPEROK=0
where piper >nul 2>nul && set PIPEROK=1
if "!PIPEROK!"=="0" if exist "tools\piper\piper.exe" set PIPEROK=1
if "!PIPEROK!"=="1" (echo [OK] Piper) else (echo [!]  Piper nenalezen)

dir /b "voices\*.onnx" >nul 2>nul
if errorlevel 1 (echo [!]  zadny Piper hlas v voices\) else (echo [OK] Piper hlas v voices\)

REM zapis do slozek
for %%D in (uploads outputs jobs logs work) do (
  >"%%D\.write_test" echo test 2>nul
  if exist "%%D\.write_test" ( del "%%D\.write_test" >nul 2>nul & echo [OK] zapis do %%D ) else ( echo [CHYBA] nelze zapsat do %%D )
)

netstat -ano | findstr ":8790" >nul 2>nul
if errorlevel 1 (echo [OK] port 8790 je volny) else (echo [!]  port 8790 je OBSAZENY)

REM Ollama (preklad)
curl -s -m 5 http://127.0.0.1:11434/api/tags >nul 2>nul
if errorlevel 1 (echo [!]  Ollama nebezi - preklad se preskoci ^(zustane puvodni jazyk^)) else (echo [OK] Ollama bezi - preklad aktivni)

REM PZ Voice Studio (volitelny TTS backend)
curl -s -m 5 http://127.0.0.1:7867/api/ping >nul 2>nul
if errorlevel 1 (echo [i]  PZ Voice Studio nebezi ^(jen pokud chces Chatterbox/klonovani^)) else (echo [OK] PZ Voice Studio bezi na 7867)

echo ==================================================
echo   Konec diagnostiky
echo ==================================================
pause
