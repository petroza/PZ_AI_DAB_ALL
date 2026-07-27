"""
XTTS v2 backend — kvalitní neuronový hlas + klonování mluvčího (GPU).

Mluví s lokálním XTTS serverem (tools/xtts_server.py, výchozí port 7868), který
běží v odděleném `.venv_xtts`. Reference hlasu (`voice` = cesta k WAV) se klonuje;
pipeline ji předává automaticky z původního zvuku videa.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional

from .base import TTSBackend, TTSError, TTSNotReady

XTTS_URL = os.environ.get("DAB_XTTS_URL", "http://127.0.0.1:7868").rstrip("/")

# --- Fonetický přepis cizích slov/značek POUZE pro TTS -----------------------
# XTTS čte cizí jména česky špatně (Porsche→„Poši", Enyaq→„anyhow"). Tady je
# přepíšeme tak, jak se v češtině vyslovují. Titulky zůstávají správně –
# nahrazuje se jen text posílaný do syntézy. Rozšiř v souboru
# `tts_phonetics.txt` v kořeni projektu (řádky `Originál = Fonetika`).
_PHON_BUILTIN = {
    "porsche": "Porše", "enyaq": "Enjak", "volkswagen": "Folksvágn",
    "peugeot": "Pežo", "renault": "Reno", "citroen": "Sitroen",
    "citroën": "Sitroen", "chevrolet": "Ševrolet", "hyundai": "Hjundaj",
    "nissan": "Nysan", "porsche911": "Porše", "iphone": "Ajfoun",
    "google": "Gůgl", "youtube": "Jůtjůb", "tesla": "Tesla",
    "enyaqu": "Enjaku", "enyaqem": "Enjakem",
}
_phon_cache: "dict | None" = None
_phon_mtime: float = -1.0   # mtime souboru při posledním načtení (-1 = nenačteno)


def _phonetics_map() -> dict:
    global _phon_cache, _phon_mtime
    try:
        from app import config
        f = config.BASE_DIR / "tts_phonetics.txt"
        mtime = f.stat().st_mtime if f.is_file() else 0.0
    except Exception:
        mtime = 0.0
    if _phon_cache is not None and mtime == _phon_mtime:
        return _phon_cache
    m = dict(_PHON_BUILTIN)
    try:
        from app import config
        f = config.BASE_DIR / "tts_phonetics.txt"
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip():
                    m[k.strip().lower()] = v.strip()
    except Exception:
        pass
    _phon_cache = m
    _phon_mtime = mtime
    return m


_WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÿ]+")


def _apply_phonetics(text: str) -> str:
    m = _phonetics_map()
    if not m or not text:
        return text
    return _WORD_RE.sub(lambda mo: m.get(mo.group(0).lower(), mo.group(0)), text)


class XttsBackend(TTSBackend):
    name = "xtts"
    needs_reference = True          # pipeline doplní referenční hlas z originálu
    supports_speed = True           # umí mluvit rychleji nativně (lepší než atempo)

    def is_ready(self) -> "tuple[bool, str]":
        try:
            import requests
            r = requests.get(XTTS_URL + "/health", timeout=3)
            if r.ok:
                data = r.json()
                if data.get("error"):
                    return False, f"Interní XTTS se nepodařilo načíst: {data['error']}"
                loaded = bool(data.get("loaded"))
                device = str(data.get("device") or "cpu").upper()
                return True, (f"Interní XTTS ({device})"
                              + (" – model připraven" if loaded
                                 else " – model se právě načítá"))
            return False, f"XTTS server {XTTS_URL} odpověděl HTTP {r.status_code}"
        except Exception:
            return False, "Interní XTTS se ještě spouští. Zkus to za chvíli znovu."

    def synth(self, text: str, out_wav: Path, voice: Optional[str] = None,
              lang: Optional[str] = None, speed: float = 1.0, log=None) -> Path:
        import requests
        text = (text or "").strip()
        out_wav = Path(out_wav)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        if not text:
            raise TTSError("Prázdný text pro syntézu.")
        ref = voice  # klon (cesta k WAV) NEBO jméno vestavěného hlasu
        if not ref:
            raise TTSNotReady(
                "XTTS: chybí hlas. Vyber vestavěný hlas, nebo nech prázdné "
                "pro klonování původního mluvčího (pipeline doplní referenci).")
        spoken = _apply_phonetics(text)        # cizí jména foneticky (jen pro TTS)
        if spoken != text and log:
            log(f"  fonetika: „{text[:30]}…“ → „{spoken[:30]}…“")
        text = spoken
        # XTTS halucinuje na konci klipu („domýšlí" slabiky), když text nekončí
        # interpunkcí. Doplň tečku → model ví, že věta skončila → čistší konec.
        if text and text[-1] not in ".!?…,;:":
            text = text + "."
        payload = {"text": text, "out_path": str(out_wav),
                   "language": lang or "cs-CZ", "speed": speed}
        # 'voice' = buď cesta k referenčnímu WAV (klonování), nebo jméno
        # vestavěného hlasu XTTS (např. „Andrew Chipper").
        if ref and Path(str(ref)).is_file():
            payload["speaker_wav"] = str(ref)
        else:
            payload["speaker"] = str(ref)
        try:
            r = requests.post(XTTS_URL + "/synth", json=payload, timeout=600)
        except Exception as e:
            raise TTSError(f"XTTS server nedostupný: {e}")
        if not r.ok:
            try:
                msg = r.json().get("error") or r.text
            except Exception:
                msg = r.text
            raise TTSError(f"XTTS syntéza selhala: {msg}")
        if not out_wav.is_file() or out_wav.stat().st_size == 0:
            raise TTSError("XTTS nevytvořil výstupní WAV (prázdný soubor).")
        if log:
            log(f"XTTS: „{text[:40]}…“ → {out_wav.name}")
        return out_wav

    # Doporučené hlasy (ověřeno podle výšky F0 – srozumitelné v češtině).
    # Zobrazí se první ve výběru. Ž = ženský, M = mužský.
    CURATED = [
        "Daisy Studious", "Alison Dietlinde", "Gracie Wise",      # Ž
        "Alexandra Hisakawa",                                     # Ž
        "Damien Black", "Aaron Dreschner", "Baldur Sanjin",       # M
        "Viktor Eka",                                             # M
    ]

    def list_voices(self) -> List[dict]:
        """Vestavěné hlasy XTTS, doporučené první (+ prázdný = klonovat)."""
        try:
            import requests
            r = requests.get(XTTS_URL + "/voices", timeout=5)
            if r.ok:
                names = r.json().get("voices") or []
                top = [n for n in self.CURATED if n in names]
                rest = [n for n in names if n not in top]
                return [{"id": n} for n in (top + rest)]
        except Exception:
            pass
        return []
