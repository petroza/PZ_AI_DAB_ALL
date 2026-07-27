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

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


# --- Síť ------------------------------------------------------------------
HOST = os.environ.get("DAB_HOST", "127.0.0.1")
PORT = _env_int("DAB_PORT", 8790)

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
# Interní XTTS s klonováním původního hlasu je výchozí; Piper zůstává rychlá
# alternativa pro počítače bez podporované GPU.
TTS_ENGINE = os.environ.get("DAB_TTS_ENGINE", "xtts")

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
VOICESTUDIO_TIMEOUT = _env_int("DAB_VS_TIMEOUT", 600)

# --- Dabing (mix & mux) ---------------------------------------------------
# "replace"  = nahradit původní zvuk dabingem,
# "voiceover"= dabing přes ztlumený originál (zachová hudbu/ruchy).
AUDIO_MODE = os.environ.get("DAB_AUDIO_MODE", "voiceover")
BURN_SUBS = os.environ.get("DAB_BURN_SUBS", "1").strip().lower() not in ("0", "false", "no", "off")
DUCK_DB = _env_float("DAB_DUCK_DB", -14.0)        # ztlumení originálu ve voiceover
TTS_GAIN_DB = _env_float("DAB_TTS_GAIN_DB", 0.0)

# Time-stretch klipu na délku slotu: "auto" (rubberband, fallback atempo),
# "rubberband", "atempo", nebo "off".
TIMESTRETCH = os.environ.get("DAB_TIMESTRETCH", "auto")
# Meze tempa (1.0 = beze změny). >1 = zrychlit (klip je delší než slot).
MAX_TEMPO = _env_float("DAB_MAX_TEMPO", 1.5)
MIN_TEMPO = _env_float("DAB_MIN_TEMPO", 0.75)

MIX_RATE = 48000          # vzorkování společné zvukové stopy
RUBBERBAND_EXE_NAMES = ["rubberband.exe", "rubberband"]

# --- Rozpočet délky překladu (kolik se toho dá vyslovit) ------------------
# Kolik ZNAKŮ české řeči se reálně vejde do jedné sekundy slotu. Podle toho se
# instruuje překladač, jak moc smí být překlad dlouhý.
# MĚŘENO (XTTS v2, cs, klonovaný hlas): nativní tempo ~7,9 zn/s; při povoleném
# zrychlení na 1,3× ~10,0 zn/s. Původní hodnota 14 byla skoro dvojnásobek
# reality → překlad se do slotu nevešel a rubberband ho musel drtit (běžně
# 1,7–2,2×), což je hlavní příčina „uspěchaného“ dabingu. 9.5 nechává malou
# rezervu pod stropem 1,3× a udrží řeč v přirozeném tempu.
DUB_CHARS_PER_SEC = _env_float("DAB_DUB_CPS", 9.5)

# --- Využití pauz mezi replikami -----------------------------------------
# Řeč smí přetéci konec svého slotu do ticha, které po ní ve videu následuje
# (dabingová praxe – mluví se „do pauzy“, místo aby se věta stlačila).
# Slot se prodlouží nejvýš o DUB_SLOT_EXTEND_MAX sekund a vždy zůstane
# DUB_SLOT_GUARD sekund rezervy před začátkem další repliky.
DUB_SLOT_EXTEND_MAX = _env_float("DAB_SLOT_EXTEND", 1.5)
DUB_SLOT_GUARD = _env_float("DAB_SLOT_GUARD", 0.12)

# Ořez balastu, který XTTS přilepí za konec repliky (dozvuk, nádech, občas i
# halucinované slovo navíc). Změřeno až 57 % délky krátkého klipu — kvůli němu
# se replika „nevejde“ do slotu a zbytečně ji zdrtí time-stretch. Ořez stojí
# jeden ASR průchod na klip; vypnout lze DAB_TRIM_TAIL=0.
DUB_TRIM_TAIL = os.environ.get("DAB_TRIM_TAIL", "1").strip().lower() not in ("0", "false", "no", "off")

# Od jakého přetečení sahat po NATIVNÍM zrychlení TTS (XTTS umí mluvit rychleji
# už při syntéze). Měřeno srozumitelností (přepis dabingu × předloha): do ~1,3×
# je na tom kvalitní rubberband stejně nebo líp (88 % vs 81 % shody), protože
# nativní zrychlení mění artikulaci modelu. Nad tímto prahem už rubberband sám
# řeč drtí, takže se práce rozdělí: TTS zrychlí do 1,3× a zbytek dojede stretch.
DUB_NATIVE_SPEED_FROM = _env_float("DAB_NATIVE_SPEED_FROM", 1.30)


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
