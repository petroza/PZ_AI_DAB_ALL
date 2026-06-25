"""
Piper TTS backend — lokální, offline, CPU. Výchozí cesta pro dabing.

Volání (ověřené chování rhasspy/piper):
    echo "text" | piper --model HLAS.onnx --output_file OUT.wav

Hlas = dvojice souborů HLAS.onnx + HLAS.onnx.json ve voices/. Rychlost se
řídí --length_scale (větší = pomalejší), takže speed=1/length_scale.
České hlasy: cs_CZ-jirka-{low,medium} z https://huggingface.co/rhasspy/piper-voices
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from app import config
from .base import TTSBackend, TTSError, TTSNotReady, popen_kwargs


def _log(log, msg: str) -> None:
    if log:
        log(msg)


class PiperBackend(TTSBackend):
    name = "piper"

    def is_ready(self) -> "tuple[bool, str]":
        exe = config.find_piper_exe()
        if not exe:
            return False, ("Piper nebyl nalezen. Vlož piper(.exe) do tools/piper/ "
                           "nebo `pip install piper-tts`.")
        return True, str(exe)

    def synth(self, text: str, out_wav: Path, voice: Optional[str] = None,
              lang: Optional[str] = None, speed: float = 1.0, log=None) -> Path:
        text = (text or "").strip()
        out_wav = Path(out_wav)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        if not text:
            raise TTSError("Prázdný text pro syntézu.")

        exe = config.find_piper_exe()
        if not exe:
            raise TTSNotReady(
                "Piper nebyl nalezen. Vlož piper(.exe) do tools/piper/ nebo "
                "nainstaluj `pip install piper-tts`."
            )
        model = config.find_piper_voice(lang or config.DEFAULT_TARGET, voice)
        if not model:
            vid = voice or config.piper_voice_id(lang or config.DEFAULT_TARGET)
            raise TTSNotReady(
                f"Piper hlas '{vid}' pro jazyk {lang} nenalezen ve voices/. "
                f"Stáhni .onnx (+ .onnx.json) z huggingface.co/rhasspy/piper-voices."
            )

        cmd = [str(exe), "--model", str(model), "--output_file", str(out_wav)]
        cfg = Path(str(model) + ".json")
        if cfg.is_file():
            cmd += ["--config", str(cfg)]
        # speed -> length_scale (Piper: vyšší length_scale = pomalejší řeč)
        if speed and speed > 0 and abs(speed - 1.0) > 1e-3:
            cmd += ["--length_scale", f"{1.0 / speed:.4f}"]

        _log(log, "PIPER: " + " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd, input=text, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300, **popen_kwargs(),
            )
        except subprocess.TimeoutExpired:
            raise TTSError("Piper překročil timeout (300 s).")
        except FileNotFoundError:
            raise TTSNotReady(f"Nelze spustit Piper: {exe}")

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
            _log(log, "PIPER chyba:\n" + "\n".join(tail))
            raise TTSError(f"Piper skončil s kódem {proc.returncode}. Detail v logu.")
        if not out_wav.is_file() or out_wav.stat().st_size == 0:
            raise TTSError("Piper nevytvořil výstupní WAV (prázdný soubor).")
        return out_wav

    def list_voices(self) -> list:
        if not config.VOICES_DIR.exists():
            return []
        return [{"id": p.stem, "path": str(p)}
                for p in sorted(config.VOICES_DIR.rglob("*.onnx"))]
