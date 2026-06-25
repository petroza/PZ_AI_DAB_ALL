"""
Tenká vrstva nad ffmpeg/ffprobe pro dabing. Hledání binárek a délku audia
přebírá od ASR enginu (engines.asr) – jeden zdroj pravdy, žádné duplicitní cesty.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from engines.asr import config as asr_config
from engines.asr import ffmpeg_tools


class DubError(RuntimeError):
    pass


def _popen_kwargs() -> dict:
    kwargs: dict = {}
    if hasattr(subprocess, "STARTUPINFO"):       # Windows -> skryj konzoli
        kwargs["creationflags"] = 0x08000000
    return kwargs


def _log(log, msg: str) -> None:
    if log:
        log(msg)


def ffmpeg_exe() -> Path:
    exe = asr_config.find_ffmpeg()
    if not exe:
        raise DubError("ffmpeg nebyl nalezen (tools/ffmpeg/ nebo PATH).")
    return exe


def run(args: List[str], log=None, timeout: int = 3600) -> None:
    """Spustí ffmpeg s danými argumenty (přidá -y a tiché logování)."""
    cmd = [str(ffmpeg_exe()), "-y", "-hide_banner", "-loglevel", "error", *args]
    _log(log, "FFMPEG: " + " ".join(str(c) for c in cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, **_popen_kwargs())
    except subprocess.TimeoutExpired:
        raise DubError("ffmpeg překročil timeout.")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-15:]
        _log(log, "FFMPEG chyba:\n" + "\n".join(tail))
        raise DubError(f"ffmpeg skončil s kódem {proc.returncode}. Detail v logu.")


def duration(path) -> float:
    return ffmpeg_tools.get_audio_duration(path)


def has_video(path) -> bool:
    """True, pokud soubor obsahuje video stopu (přes ffprobe)."""
    ffprobe = asr_config.find_ffprobe()
    if not ffprobe:
        return False
    try:
        out = subprocess.run(
            [str(ffprobe), "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, **_popen_kwargs(),
        )
        return "video" in (out.stdout or "")
    except Exception:
        return False


def make_silence(out_wav: Path, dur: float, rate: int, log=None) -> Path:
    dur = max(0.001, float(dur))
    run(["-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono",
         "-t", f"{dur:.3f}", "-ar", str(rate), "-ac", "1",
         "-c:a", "pcm_s16le", str(out_wav)], log=log)
    return out_wav


def to_canonical(src, out_wav: Path, rate: int, gain_db: float = 0.0,
                 log=None) -> Path:
    """Převede libovolné audio na společný formát mixu: mono PCM s16 @rate."""
    args = ["-i", str(src)]
    if abs(gain_db) > 1e-3:
        args += ["-filter:a", f"volume={gain_db:.2f}dB"]
    args += ["-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)]
    run(args, log=log)
    return out_wav


def extract_audio(src, out_wav: Path, rate: int, log=None) -> Path:
    """Vytáhne zvukovou stopu videa (pro 'voiceover' podklad) na mono @rate."""
    run(["-i", str(src), "-vn", "-ar", str(rate), "-ac", "1",
         "-c:a", "pcm_s16le", str(out_wav)], log=log)
    return out_wav
