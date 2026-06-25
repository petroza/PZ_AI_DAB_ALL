"""
PZ AI DAB ALL — centrální konfigurace aplikace.

Sjednocuje tři světy:
  * ASR + překlad   — modely/nástroje hledá engines.asr.config (parakeet, ffmpeg,
                      model, Ollama, jazyková mapa, parametry titulků)
  * TTS             — Piper (offline) nebo PZ Voice Studio (HTTP)
  * Dabing          — režim zvuku, time-stretch, mux

ASR + překlad (parakeet, ffmpeg, model, Ollama, jazyková mapa) si nese vlastní
config engines.asr.config – tady ho neduplikujeme, jen voláme jeho find_* funkce.
Pořadí hledání nástrojů: proměnná prostředí → lokální tools/ → systémová PATH.
Cesty k modelům/nástrojům/hlasům jsou v kořeni projektu (mimo git, viz .gitignore).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# --- Cesty ----------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent                      # kořen projektu

FRONTEND_DIR = BASE_DIR / "frontend"
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
JOBS_DIR = BASE_DIR / "jobs"
LOGS_DIR = BASE_DIR / "logs"
VOICES_DIR = BASE_DIR / "voices"               # Piper .onnx hlasy
TOOLS_DIR = BASE_DIR / "tools"
PIPER_DIR = TOOLS_DIR / "piper"
RUBBERBAND_DIR = TOOLS_DIR / "rubberband"
WORK_DIR = BASE_DIR / "work"                   # dočasné klipy/segmenty jobu

APP_LOG = LOGS_DIR / "app.log"

# --- Síť ------------------------------------------------------------------
HOST = os.environ.get("DAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("DAB_PORT", "8790"))

# --- Vstup ----------------------------------------------------------------
SUPPORTED_VIDEO_EXT = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".mpg", ".mpeg",
}
# Dabovat lze i čisté audio (výstup pak bude jen zvuková stopa).
SUPPORTED_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
SUPPORTED_INPUT_EXT = SUPPORTED_VIDEO_EXT | SUPPORTED_AUDIO_EXT

# --- Jazyky ---------------------------------------------------------------
# Zdroj = co zvládne ASR (parakeet, "auto" = autodetekce). Cíl = překlad + TTS.
SOURCE_LANGUAGES = [
    "auto", "cs-CZ", "en-US", "uk-UA", "ru-RU", "de-DE",
    "pl-PL", "sk-SK", "es-ES", "fr-FR", "it-IT",
]
TARGET_LANGUAGES = [
    "cs-CZ", "en-US", "uk-UA", "de-DE", "pl-PL",
    "sk-SK", "es-ES", "fr-FR", "it-IT", "ru-RU",
]
DEFAULT_SOURCE = "auto"
DEFAULT_TARGET = "cs-CZ"          # priorita: čeština

# --- TTS ------------------------------------------------------------------
# "piper" = lokální offline (výchozí, drží slib CPU/offline),
# "voicestudio" = HTTP volání běžícího PZ Voice Studia (Chatterbox, klonování).
TTS_ENGINE = os.environ.get("DAB_TTS_ENGINE", "piper")

PIPER_EXE_NAMES = ["piper.exe", "piper"]
# Výchozí Piper hlas pro daný cílový jazyk. Stačí jméno (bez .onnx) – soubor
# se hledá ve voices/. Když chybí, /api/status to nahlásí. Uživatel může vložit
# libovolný .onnx z https://huggingface.co/rhasspy/piper-voices.
PIPER_VOICES = {
    "cs-CZ": "cs_CZ-jirka-medium",
    "en-US": "en_US-amy-medium",
    "uk-UA": "uk_UA-ukrainian_tts-medium",
    "de-DE": "de_DE-thorsten-medium",
    "pl-PL": "pl_PL-darkman-medium",
    "sk-SK": "sk_SK-lili-medium",
    "es-ES": "es_ES-davefx-medium",
    "fr-FR": "fr_FR-siwis-medium",
    "it-IT": "it_IT-riccardo-x_low",
    "ru-RU": "ru_RU-dmitri-medium",
}

# PZ Voice Studio (běží samostatně, výchozí port 7867).
VOICESTUDIO_URL = os.environ.get("DAB_VOICESTUDIO_URL", "http://127.0.0.1:7867").rstrip("/")
VOICESTUDIO_ENGINE = os.environ.get("DAB_VS_ENGINE", "Piper")  # nebo "Chatterbox 500M - quality"
VOICESTUDIO_TIMEOUT = int(os.environ.get("DAB_VS_TIMEOUT", "600"))

# --- Dabing (mix & mux) ---------------------------------------------------
# "replace"  = nahradit původní zvuk dabingem,
# "voiceover"= dabing přes ztlumený originál (zachová hudbu/ruchy).
AUDIO_MODE = os.environ.get("DAB_AUDIO_MODE", "replace")
DUCK_DB = float(os.environ.get("DAB_DUCK_DB", "-14"))     # ztlumení originálu ve voiceover
TTS_GAIN_DB = float(os.environ.get("DAB_TTS_GAIN_DB", "0"))

# Time-stretch klipu na délku slotu: "auto" (rubberband, fallback atempo),
# "rubberband", "atempo", nebo "off".
TIMESTRETCH = os.environ.get("DAB_TIMESTRETCH", "auto")
# Meze tempa (1.0 = beze změny). >1 = zrychlit (klip je delší než slot).
MAX_TEMPO = float(os.environ.get("DAB_MAX_TEMPO", "1.5"))
MIN_TEMPO = float(os.environ.get("DAB_MIN_TEMPO", "0.75"))

MIX_RATE = 48000          # vzorkování společné zvukové stopy
RUBBERBAND_EXE_NAMES = ["rubberband.exe", "rubberband"]


# --- Pomocné: hledání nástrojů -------------------------------------------
def ensure_dirs() -> None:
    for d in (UPLOADS_DIR, OUTPUTS_DIR, JOBS_DIR, LOGS_DIR,
              VOICES_DIR, PIPER_DIR, RUBBERBAND_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _find_exe(root: Path, names) -> "Path | None":
    if root.exists():
        for name in names:
            c = root / name
            if c.is_file():
                return c
        for name in names:
            for f in root.rglob(name):
                if f.is_file():
                    return f
    for name in names:
        w = shutil.which(name)
        if w:
            return Path(w)
    return None


def find_piper_exe() -> "Path | None":
    env = os.environ.get("DAB_PIPER_EXE")
    if env and Path(env).is_file():
        return Path(env)
    return _find_exe(PIPER_DIR, PIPER_EXE_NAMES)


def find_rubberband_exe() -> "Path | None":
    env = os.environ.get("DAB_RUBBERBAND_EXE")
    if env and Path(env).is_file():
        return Path(env)
    return _find_exe(RUBBERBAND_DIR, RUBBERBAND_EXE_NAMES)


def piper_voice_id(target_lang: str) -> str:
    return PIPER_VOICES.get(target_lang, PIPER_VOICES.get(DEFAULT_TARGET, ""))


def find_piper_voice(target_lang: str, voice: "str | None" = None) -> "Path | None":
    """Najde .onnx hlas pro cílový jazyk ve voices/.

    Priorita: explicitní `voice` → výchozí hlas jazyka (PIPER_VOICES) →
    první .onnx, jehož jméno začíná prefixem jazyka (cs_CZ, en_US, …).
    """
    if not VOICES_DIR.exists():
        return None
    wanted = voice or piper_voice_id(target_lang)
    if wanted:
        stem = wanted[:-5] if wanted.endswith(".onnx") else wanted
        direct = VOICES_DIR / f"{stem}.onnx"
        if direct.is_file():
            return direct
        for f in VOICES_DIR.rglob(f"{stem}.onnx"):
            if f.is_file():
                return f
    prefix = target_lang.replace("-", "_").split(".")[0]      # cs-CZ -> cs_CZ
    for f in sorted(VOICES_DIR.rglob("*.onnx")):
        if f.name.lower().startswith(prefix.lower()):
            return f
    return None
