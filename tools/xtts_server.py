"""
XTTS v2 lokální TTS server — kvalitní neuronový hlas + klonování mluvčího.

Běží v odděleném prostředí `.venv_xtts` (torch + coqui-tts), aby netáhl těžké
GPU závislosti do hlavního workeru. DAB ho volá přes HTTP (engine `xtts`),
stejně jako PZ Voice Studio.

Spuštění:  START_XTTS.bat   (nebo: .venv_xtts/Scripts/python tools/xtts_server.py)
Endpoint:  POST /synth {text, language, speaker_wav, out_path, speed}
           GET  /health
Port:      XTTS_PORT (výchozí 7868)
"""
from __future__ import annotations

import json
import os
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("COQUI_TOS_AGREED", "1")  # auto-akceptace licence modelu

PORT = int(os.environ.get("XTTS_PORT", "7868"))
MODEL = os.environ.get("XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")

# DAB locale (cs-CZ) -> XTTS jazykový kód. XTTS v2 neumí uk/sk.
LANG_MAP = {
    "cs": "cs", "en": "en", "de": "de", "es": "es", "fr": "fr", "it": "it",
    "pl": "pl", "ru": "ru", "nl": "nl", "pt": "pt", "tr": "tr", "ar": "ar",
    "hu": "hu", "ko": "ko", "ja": "ja", "hi": "hi", "zh": "zh-cn",
}

_tts = None
_lock = threading.Lock()       # XTTS není thread-safe — syntéza serializovaná
_init_lock = threading.Lock()  # ochrana dvojité inicializace modelu (double-checked locking)


def to_xtts_lang(loc: "str | None") -> "str | None":
    code = (loc or "cs").split("-")[0].lower()
    return LANG_MAP.get(code)


def expand_numbers(text: str, lang: str) -> str:
    """Převede číslice na slova v daném jazyce JEŠTĚ před XTTS.

    XTTS má vlastní expanzi čísel, ale `num2words` pro češtinu nemá to_ordinal
    → „8 000." (číslo u tečky) shodí syntézu (NotImplementedError). Tím, že
    čísla rozepíšeme sami (cardinal), se buggy větev XTTS vůbec nespustí a
    čísla se čtou správně („8 000" → „osm tisíc")."""
    try:
        from num2words import num2words
    except Exception:
        return text
    # slož oddělovače tisíců (mezera / pevná mezera): "8 000" → "8000"
    t = re.sub(r"(?<=\d)[  ](?=\d{3}(?:\D|$))", "", text)

    def _repl(m):
        try:
            return num2words(int(m.group(0)), lang=lang)
        except Exception:
            return m.group(0)

    try:
        return re.sub(r"\d+", _repl, t)
    except Exception:
        return text


def get_tts():
    global _tts
    if _tts is not None:
        return _tts
    with _init_lock:
        if _tts is None:
            import torch
            from TTS.api import TTS
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"[xtts] načítám {MODEL} na {dev} …", flush=True)
            _tts = TTS(MODEL).to(dev)
            print("[xtts] připraveno", flush=True)
    return _tts


def speaker_names() -> list:
    """Jména vestavěných studiových hlasů XTTS (mužské i ženské)."""
    try:
        tts = get_tts()
        spk = tts.synthesizer.tts_model.speaker_manager.speakers
        return sorted(spk.keys())
    except Exception:
        return []


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send(200, {"ok": True, "model": MODEL, "loaded": _tts is not None})
        elif self.path.startswith("/voices"):
            self._send(200, {"voices": speaker_names()})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/synth"):
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            ln = int(self.headers.get("Content-Length", "0") or 0)
            raw_body = self.rfile.read(ln) or b"{}"
            req = json.loads(raw_body.decode("utf-8", "replace"))
            text = (req.get("text") or "").strip()
            out = req.get("out_path")
            ref = req.get("speaker_wav")
            speaker = (req.get("speaker") or "").strip()  # vestavěný hlas (jméno)
            lang = to_xtts_lang(req.get("language"))
            speed = float(req.get("speed") or 1.0)
            if not text or not out:
                self._send(400, {"ok": False, "error": "text a out_path jsou povinné"})
                return
            if not lang:
                self._send(400, {"ok": False,
                                 "error": f"jazyk '{req.get('language')}' XTTS v2 nepodporuje"})
                return
            use_builtin = bool(speaker)
            if not use_builtin and (not ref or not os.path.isfile(ref)):
                self._send(400, {"ok": False,
                                 "error": "chybí hlas: buď 'speaker' (vestavěný), "
                                          "nebo 'speaker_wav' (klonování)"})
                return
            text = expand_numbers(text, lang)   # čísla → slova (obchází bug XTTS)
            with _lock:
                tts = get_tts()
                if use_builtin:
                    tts.tts_to_file(text=text, file_path=out, speaker=speaker,
                                    language=lang, speed=speed)
                else:
                    tts.tts_to_file(text=text, file_path=out, speaker_wav=ref,
                                    language=lang, speed=speed)
            self._send(200, {"ok": True, "out": out})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    try:
        get_tts()       # přednačtení modelu (jednorázové stažení ~1.8 GB)
    except Exception:
        traceback.print_exc()
    print(f"[xtts] server běží na http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
