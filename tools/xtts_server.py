"""
XTTS v2 lokĂˇlnĂ­ TTS server â€” kvalitnĂ­ neuronovĂ˝ hlas + klonovĂˇnĂ­ mluvÄŤĂ­ho.

BÄ›ĹľĂ­ v oddÄ›lenĂ©m prostĹ™edĂ­ `.venv_xtts` (torch + coqui-tts), aby netĂˇhl tÄ›ĹľkĂ©
GPU zĂˇvislosti do hlavnĂ­ho workeru. DAB ho volĂˇ pĹ™es HTTP (engine `xtts`),
stejnÄ› jako PZ Voice Studio.

SpuĹˇtÄ›nĂ­:  tools\start\START_XTTS.bat   (nebo: .venv_xtts/Scripts/python tools/xtts_server.py)
Endpoint:  POST /synth {text, language, speaker_wav, out_path, speed}
           GET  /health
Port:      XTTS_PORT (vĂ˝chozĂ­ 7868)
"""
from __future__ import annotations

import json
import os
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("COQUI_TOS_AGREED", "1")  # auto-akceptace licence modelu

# VeĹˇkerĂ© modely a cache drĹľĂ­me uvnitĹ™ aplikace. XTTS tak nenĂ­ zĂˇvislĂ© na
# uĹľivatelskĂ©m profilu, systĂ©movĂ©m Pythonu ani externĂ­ instalaci Voice Studia.
APP_DIR = Path(__file__).resolve().parents[1]
MODEL_HOME = APP_DIR / "models" / "tts"
HF_HOME = APP_DIR / "models" / "huggingface"
MODEL_HOME.mkdir(parents=True, exist_ok=True)
HF_HOME.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TTS_HOME", str(MODEL_HOME))
os.environ.setdefault("HF_HOME", str(HF_HOME))

PORT = int(os.environ.get("XTTS_PORT", "7868"))
MODEL = os.environ.get("XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")

# DAB locale (cs-CZ) -> XTTS jazykovĂ˝ kĂłd. XTTS v2 neumĂ­ uk/sk.
LANG_MAP = {
    "cs": "cs", "en": "en", "de": "de", "es": "es", "fr": "fr", "it": "it",
    "pl": "pl", "ru": "ru", "nl": "nl", "pt": "pt", "tr": "tr", "ar": "ar",
    "hu": "hu", "ko": "ko", "ja": "ja", "hi": "hi", "zh": "zh-cn",
}

_tts = None
_loading = False
_load_error = None
_lock = threading.Lock()       # XTTS nenĂ­ thread-safe â€” syntĂ©za serializovanĂˇ
_init_lock = threading.Lock()  # ochrana dvojitĂ© inicializace modelu (double-checked locking)


def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def to_xtts_lang(loc: "str | None") -> "str | None":
    code = (loc or "cs").split("-")[0].lower()
    return LANG_MAP.get(code)


def expand_numbers(text: str, lang: str) -> str:
    """PĹ™evede ÄŤĂ­slice na slova v danĂ©m jazyce JEĹ TÄš pĹ™ed XTTS.

    XTTS mĂˇ vlastnĂ­ expanzi ÄŤĂ­sel, ale `num2words` pro ÄŤeĹˇtinu nemĂˇ to_ordinal
    â†’ â€ž8 000." (ÄŤĂ­slo u teÄŤky) shodĂ­ syntĂ©zu (NotImplementedError). TĂ­m, Ĺľe
    ÄŤĂ­sla rozepĂ­Ĺˇeme sami (cardinal), se buggy vÄ›tev XTTS vĹŻbec nespustĂ­ a
    ÄŤĂ­sla se ÄŤtou sprĂˇvnÄ› (â€ž8 000" â†’ â€žosm tisĂ­c")."""
    try:
        from num2words import num2words
    except Exception:
        return text
    # sloĹľ oddÄ›lovaÄŤe tisĂ­cĹŻ (mezera / pevnĂˇ mezera): "8 000" â†’ "8000"
    t = re.sub(r"(?<=\d)[ Â ](?=\d{3}(?:\D|$))", "", text)

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
    global _tts, _loading, _load_error
    if _tts is not None:
        return _tts
    with _init_lock:
        if _tts is None:
            _loading = True
            _load_error = None
            try:
                import torch
                from TTS.api import TTS
                dev = "cuda" if torch.cuda.is_available() else "cpu"
                print(f"[xtts] naÄŤĂ­tĂˇm {MODEL} na {dev} â€¦", flush=True)
                print(f"[xtts] modely: {MODEL_HOME}", flush=True)
                _tts = TTS(MODEL).to(dev)
                print("[xtts] pĹ™ipraveno", flush=True)
            except Exception as exc:
                _load_error = str(exc)
                raise
            finally:
                _loading = False
    return _tts


def speaker_names() -> list:
    """JmĂ©na vestavÄ›nĂ˝ch studiovĂ˝ch hlasĹŻ XTTS (muĹľskĂ© i ĹľenskĂ©)."""
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
            self._send(200, {"ok": _load_error is None, "model": MODEL,
                             "loaded": _tts is not None, "loading": _loading,
                             "device": "cuda" if _cuda_available() else "cpu",
                             "error": _load_error, "model_home": str(MODEL_HOME)})
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
            speaker = (req.get("speaker") or "").strip()  # vestavÄ›nĂ˝ hlas (jmĂ©no)
            lang = to_xtts_lang(req.get("language"))
            speed = float(req.get("speed") or 1.0)
            seed = int(req.get("seed", 77) or 77)
            if not text or not out:
                self._send(400, {"ok": False, "error": "text a out_path jsou povinnĂ©"})
                return
            if not lang:
                self._send(400, {"ok": False,
                                 "error": f"jazyk '{req.get('language')}' XTTS v2 nepodporuje"})
                return
            use_builtin = bool(speaker)
            if not use_builtin and (not ref or not os.path.isfile(ref)):
                self._send(400, {"ok": False,
                                 "error": "chybĂ­ hlas: buÄŹ 'speaker' (vestavÄ›nĂ˝), "
                                          "nebo 'speaker_wav' (klonovĂˇnĂ­)"})
                return
            text = expand_numbers(text, lang)   # ÄŤĂ­sla â†’ slova (obchĂˇzĂ­ bug XTTS)
            with _lock:
                tts = get_tts()
                # ReprodukovatelnĂ˝ vĂ˝sledek: bez seedu mĹŻĹľe stejnĂˇ vÄ›ta pĹ™i
                # kaĹľdĂ©m bÄ›hu dopadnout jinak a nĂˇhodnÄ› pĹ™idĂˇvat slabiky.
                try:
                    import torch
                    torch.manual_seed(seed)
                    if torch.cuda.is_available():
                        torch.cuda.manual_seed_all(seed)
                except Exception:
                    pass
                if use_builtin:
                    tts.tts_to_file(text=text, file_path=out, speaker=speaker,
                                    language=lang, speed=speed, split_sentences=True)
                else:
                    tts.tts_to_file(text=text, file_path=out, speaker_wav=ref,
                                    language=lang, speed=speed, split_sentences=True)
            self._send(200, {"ok": True, "out": out})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"[xtts] server bÄ›ĹľĂ­ na http://127.0.0.1:{PORT}", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)

    # HTTP zaÄŤne odpovĂ­dat okamĹľitÄ›; tÄ›ĹľkĂ˝ model se naÄŤĂ­tĂˇ na pozadĂ­. Web tak
    # mĹŻĹľe zobrazit stav â€žnaÄŤĂ­tĂˇ seâ€ś a nemusĂ­ hlĂˇsit, Ĺľe server nebÄ›ĹľĂ­.
    def _preload():
        try:
            get_tts()
        except Exception:
            traceback.print_exc()

    threading.Thread(target=_preload, name="xtts-preload", daemon=True).start()
    server.serve_forever()

