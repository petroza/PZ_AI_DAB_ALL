"""
PZ ASR Studio - centrální konfigurace.

Všechny cesty, porty a parametry ASR backendu se načítají odsud.
Nikde jinde v kódu se nepíší natvrdo cesty k nástrojům ani k modelu.

Pořadí hledání nástrojů/modelu:
  1. proměnná prostředí (PZ_PARAKEET_EXE / PZ_FFMPEG_EXE / PZ_MODEL_PATH)
  2. lokální složka v projektu (tools/parakeet, tools/ffmpeg, models)
  3. systémová PATH (jen ffmpeg / parakeet-cli)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# --- Základní cesty -------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent
# V PZ AI DAB ALL je tento modul vendorovaný v engines/asr/, takže kořen
# projektu (kde leží models/, tools/, corrections.txt) je o dvě úrovně výš.
BASE_DIR = BACKEND_DIR.parent.parent

FRONTEND_DIR = BASE_DIR / "frontend"
MODELS_DIR = BASE_DIR / "models"
TOOLS_DIR = BASE_DIR / "tools"
PARAKEET_DIR = TOOLS_DIR / "parakeet"
FFMPEG_DIR = TOOLS_DIR / "ffmpeg"
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
JOBS_DIR = BASE_DIR / "jobs"
LOGS_DIR = BASE_DIR / "logs"

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
HOST = os.environ.get("PZ_HOST", "127.0.0.1")
PORT = _env_int("PZ_PORT", 8787)

# --- Audio pipeline -------------------------------------------------------
# Výstup do ASR musí být VŽDY 16 kHz / mono / PCM s16le.
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
TARGET_CODEC = "pcm_s16le"

SUPPORTED_INPUT_EXTENSIONS = {
    ".wav", ".mp3", ".mp4", ".mov", ".m4a", ".mkv",
    ".aac", ".flac", ".ogg", ".opus", ".webm", ".avi",
}

# --- Jazyky ---------------------------------------------------------------
# UI locale -> kód jazyka pro parakeet (auto = nechat model rozhodnout).
LANGUAGE_MAP = {
    "auto": None,
    "cs-CZ": "cs-CZ",
    "en-US": "en-US",
    "uk-UA": "uk-UA",
    "ru-RU": "ru-RU",
}
SUPPORTED_LANGUAGES = list(LANGUAGE_MAP.keys())
DEFAULT_LANGUAGE = "cs-CZ"   # priorita: čeština

# --- parakeet.cpp CLI -----------------------------------------------------
# Název spustitelného souboru parakeet-cli (Windows i ostatní platformy).
PARAKEET_EXE_NAMES = ["parakeet-cli.exe", "parakeet-cli"]

# Doporučený model pro češtinu/vícejazyčnost. Stačí mít *jeden* .gguf ve
# složce models/ - vybere se automaticky podle této priority.
# Pro OFFLINE přepis souborů je nejpřesnější tdt-0.6b-v3 (25 evropských
# jazyků vč. češtiny). nemotron-3.5-asr-streaming je optimalizovaný na
# nízkou latenci (živý mikrofon) - bývá u souborů méně přesný, proto je níž.
PREFERRED_MODEL_HINTS = [
    "tdt-0.6b-v3",                 # DOPORUČENO - nejpřesnější offline (cs/EU)
    "tdt-0.6b-v2",
    "tdt-0.6b",
    "tdt-1.1b",
    "nemotron-3.5-asr-streaming",  # streaming - spíš pro budoucí živý mikrofon
    "nemotron",
    "v3",
]

# Volitelné parametry CLI - používá je asr_engine.build_transcribe_command().
PARAKEET_DECODER = os.environ.get("PZ_PARAKEET_DECODER") or None     # "ctc" | "tdt" | None(auto)
PARAKEET_THREADS = os.environ.get("PZ_PARAKEET_THREADS") or None     # např. "4"; None = výchozí
PARAKEET_STREAM = os.environ.get("PZ_PARAKEET_STREAM", "0") == "1"   # offline přepis souboru = False
PARAKEET_LANG_FLAG = os.environ.get("PZ_PARAKEET_LANG_FLAG", "--lang")
# Posílat jazykový flag? Nemotron umí auto-detekci; flag je u některých
# buildů jen na serveru, ne u 'transcribe'. Když ho binárka odmítne,
# asr_engine to pozná a zopakuje příkaz bez něj.
PARAKEET_SEND_LANG_FLAG = os.environ.get("PZ_PARAKEET_SEND_LANG", "1") == "1"

# Timeout přepisu v sekundách. 0 = bez limitu.
PARAKEET_TIMEOUT = _env_int("PZ_PARAKEET_TIMEOUT", 0)

# --- Titulkování ----------------------------------------------------------
SUBTITLE_MAX_CHARS = 64       # max délka řádku titulku (znaky)
SUBTITLE_MAX_DURATION = 6.0   # max délka jednoho titulku [s]
SUBTITLE_MAX_GAP = 1.0        # mezera mezi slovy, po které se titulek zalomí [s]

OUTPUT_FORMATS = ["txt", "srt", "vtt", "json"]

# Slovník oprav po přepisu (vlastní jména, značky, anglická slova).
# Soubor corrections.txt v kořeni projektu. Formát řádku:  chybně = správně
# (case-insensitive, celá slova). Řádky začínající # jsou komentáře.
CORRECTIONS_FILE = BASE_DIR / "corrections.txt"

# --- Automatická oprava cizích slov/značek lokálním LLM (Ollama) ----------
# Po přepisu pošle text do lokálního LLM (Ollama) s přísnou instrukcí "oprav
# jen foneticky špatně napsaná cizí slova/značky/jména na správný pravopis,
# češtinu nech být". Plně offline, automatické, BEZ slovníku. Ověřeno: gemma4
# opraví porše->Porsche i Škoda Inijak->Enyaq a české věty nechá být.
# Když Ollama neběží nebo selže, vrátí se původní text (job nikdy nespadne).
LLM_CORRECT = os.environ.get("PZ_LLM_CORRECT", "1") == "1"
OLLAMA_URL = os.environ.get("PZ_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.environ.get("PZ_OLLAMA_MODEL", "gemma4:latest")
LLM_TIMEOUT = _env_int("PZ_LLM_TIMEOUT", 300)  # vyšší kvůli studenému startu velkého modelu

# --- Detekce anglických slov dvojprůchodem (EXPERIMENT, default vyp) -------
# Plně automatické, bez slovníku: udělá se i druhý průchod v angličtině,
# slova se zarovnají podle časů a tam, kde je anglický model VÝRAZNĚ jistější
# (a české slovo vypadá jako fonetický patvar), použije se anglický pravopis
# (Porsche místo "porše"). Cena: ~2x delší přepis.
# Default VYPNUTO: empiricky se ukázalo, že anglický průchod halucinuje
# angličtinu i nad českými slovy (čeština se rozbíjí) a český model je u
# správného slova jistější -> nespolehlivé. Spolehlivé řešení = corrections.txt.
CODESWITCH_EN = os.environ.get("PZ_CODESWITCH", "0") == "1"
CODESWITCH_EN_MIN_CONF = _env_float("PZ_CS_EN_CONF", 0.55)  # min. jistota EN slova
CODESWITCH_CONF_DELTA = _env_float("PZ_CS_DELTA", 0.10)     # o kolik musí EN > CZ
CODESWITCH_MIN_LEN = _env_int("PZ_CS_MINLEN", 3)            # min. délka EN slova


# --- Pomocné funkce -------------------------------------------------------
def ensure_dirs() -> None:
    for d in (MODELS_DIR, PARAKEET_DIR, FFMPEG_DIR, UPLOADS_DIR,
              OUTPUTS_DIR, JOBS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _find_in_dir(root: Path, names) -> "Path | None":
    if not root.exists():
        return None
    # přímo v rootu
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    # rekurzivně (binárky bývají v podsložce build/ apod.)
    for name in names:
        for found in root.rglob(name):
            if found.is_file():
                return found
    return None


def find_parakeet_exe() -> "Path | None":
    env = os.environ.get("PZ_PARAKEET_EXE")
    if env and Path(env).is_file():
        return Path(env)
    local = _find_in_dir(PARAKEET_DIR, PARAKEET_EXE_NAMES)
    if local:
        return local
    for name in PARAKEET_EXE_NAMES:
        which = shutil.which(name)
        if which:
            return Path(which)
    return None


def find_ffmpeg() -> "Path | None":
    env = os.environ.get("PZ_FFMPEG_EXE")
    if env and Path(env).is_file():
        return Path(env)
    local = _find_in_dir(FFMPEG_DIR, ["ffmpeg.exe", "ffmpeg"])
    if local:
        return local
    which = shutil.which("ffmpeg")
    return Path(which) if which else None


def find_ffprobe() -> "Path | None":
    env = os.environ.get("PZ_FFPROBE_EXE")
    if env and Path(env).is_file():
        return Path(env)
    local = _find_in_dir(FFMPEG_DIR, ["ffprobe.exe", "ffprobe"])
    if local:
        return local
    which = shutil.which("ffprobe")
    return Path(which) if which else None


def find_model() -> "Path | None":
    env = os.environ.get("PZ_MODEL_PATH")
    if env and Path(env).is_file():
        return Path(env)
    if not MODELS_DIR.exists():
        return None
    ggufs = sorted(MODELS_DIR.rglob("*.gguf"))
    if not ggufs:
        return None
    # preferuj podle nápovědy (nemotron > v3 > cokoliv)
    for hint in PREFERRED_MODEL_HINTS:
        for g in ggufs:
            if hint in g.name.lower():
                return g
    return ggufs[0]
