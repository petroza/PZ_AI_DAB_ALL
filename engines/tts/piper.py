"""
Piper TTS backend — lokální, offline, CPU. Výchozí cesta pro dabing.

Podporuje dva způsoby spuštění:
  1. Binárka piper(.exe) v tools/piper/ — spuštění jako subprocess (stávající chování)
  2. piper-tts Python balíček — `pip install piper-tts` (čistě Python, žádná binárka)

Hlas = dvojice souborů HLAS.onnx + HLAS.onnx.json ve voices/. Chybí-li hlas,
stáhne se automaticky z https://huggingface.co/rhasspy/piper-voices.
České hlasy: cs_CZ-jirka-{low,medium}
"""
from __future__ import annotations

import subprocess
import urllib.request
import wave
from pathlib import Path
from typing import Optional

from app import config
from .base import TTSBackend, TTSError, TTSNotReady, popen_kwargs


def _log(log, msg: str) -> None:
    if log:
        log(msg)


def _pip_piper_available() -> bool:
    """True pokud je piper-tts Python balíček nainstalovaný."""
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def _download_voice(voice_id: str, voices_dir: Path, log=None) -> "Path | None":
    """Stáhne .onnx + .onnx.json z HuggingFace rhasspy/piper-voices.
    voice_id: např. cs_CZ-jirka-medium"""
    parts = voice_id.split("-")
    if len(parts) < 3:
        return None
    lang_full = parts[0]                          # cs_CZ
    lang = lang_full.split("_")[0]               # cs
    name = "-".join(parts[1:-1])                 # jirka
    quality = parts[-1]                          # medium
    base = (f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            f"{lang}/{lang_full}/{name}/{quality}/{voice_id}")
    voices_dir.mkdir(parents=True, exist_ok=True)
    onnx = voices_dir / f"{voice_id}.onnx"
    json_ = voices_dir / f"{voice_id}.onnx.json"
    try:
        if not onnx.is_file():
            _log(log, f"Stahuji hlas {voice_id}.onnx z HuggingFace…")
            urllib.request.urlretrieve(f"{base}.onnx", onnx)
        if not json_.is_file():
            _log(log, f"Stahuji konfiguraci {voice_id}.onnx.json…")
            urllib.request.urlretrieve(f"{base}.onnx.json", json_)
        return onnx
    except Exception as e:
        _log(log, f"Stažení hlasu selhalo: {e}")
        for p in (onnx, json_):
            if p.is_file():
                p.unlink(missing_ok=True)
        return None


def _ensure_voice(lang: str, voice: "str | None", log=None) -> "Path | None":
    """Najde hlas ve voices/; pokud chybí, pokusí se stáhnout výchozí hlas."""
    model = config.find_piper_voice(lang, voice)
    if model:
        return model
    vid = voice or config.piper_voice_id(lang)
    if not vid:
        return None
    _log(log, f"Hlas '{vid}' nenalezen, stahuji z HuggingFace…")
    return _download_voice(vid, config.VOICES_DIR, log)


def _synth_pip(text: str, out_wav: Path, model: Path,
               speed: float, log=None) -> None:
    """Syntéza přes piper-tts Python API (bez binárky)."""
    from piper import PiperVoice
    cfg = Path(str(model) + ".json")
    _log(log, f"PIPER (pip): model={model.name}, length_scale={1.0/speed:.3f}")
    pv = PiperVoice.load(str(model),
                         config_path=str(cfg) if cfg.is_file() else None)
    length_scale = (1.0 / speed) if speed and speed > 0 else 1.0
    with wave.open(str(out_wav), "wb") as wf:
        pv.synthesize(text, wf, length_scale=length_scale)


class PiperBackend(TTSBackend):
    name = "piper"

    def is_ready(self) -> "tuple[bool, str]":
        exe = config.find_piper_exe()
        if exe:
            return True, str(exe)
        if _pip_piper_available():
            return True, "piper-tts (pip)"
        return False, ("Piper nebyl nalezen. Vlož piper(.exe) do tools/piper/ "
                       "nebo spusť: pip install piper-tts")

    def synth(self, text: str, out_wav: Path, voice: Optional[str] = None,
              lang: Optional[str] = None, speed: float = 1.0, log=None) -> Path:
        text = (text or "").strip()
        out_wav = Path(out_wav)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        if not text:
            raise TTSError("Prázdný text pro syntézu.")

        target = lang or config.DEFAULT_TARGET
        model = _ensure_voice(target, voice, log)
        if not model:
            vid = voice or config.piper_voice_id(target)
            raise TTSNotReady(
                f"Piper hlas '{vid}' pro jazyk {target} nenalezen a stažení selhalo. "
                f"Vlož .onnx (+ .onnx.json) ručně do voices/ z "
                f"huggingface.co/rhasspy/piper-voices."
            )

        exe = config.find_piper_exe()
        if exe:
            # Cesta 1: subprocess (binárka)
            cmd = [str(exe), "--model", str(model), "--output_file", str(out_wav)]
            cfg = Path(str(model) + ".json")
            if cfg.is_file():
                cmd += ["--config", str(cfg)]
            if speed and speed > 0 and abs(speed - 1.0) > 1e-3:
                cmd += ["--length_scale", f"{1.0 / speed:.4f}"]
            _log(log, "PIPER (bin): " + " ".join(cmd))
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
        elif _pip_piper_available():
            # Cesta 2: Python API (piper-tts pip balíček)
            try:
                _synth_pip(text, out_wav, model, speed, log)
            except Exception as e:
                raise TTSError(f"Piper pip syntéza selhala: {e}")
        else:
            raise TTSNotReady(
                "Piper není dostupný. Spusť: pip install piper-tts"
            )

        if not out_wav.is_file() or out_wav.stat().st_size == 0:
            raise TTSError("Piper nevytvořil výstupní WAV (prázdný soubor).")
        return out_wav

    def list_voices(self) -> list:
        if not config.VOICES_DIR.exists():
            return []
        return [{"id": p.stem, "path": str(p)}
                for p in sorted(config.VOICES_DIR.rglob("*.onnx"))]
