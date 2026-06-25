"""
Časové zarovnání klipu na délku titulkového slotu (se zachováním výšky hlasu).

Když je vygenerovaná řeč delší než slot ve videu, mírně se zrychlí; když kratší,
nechá se být (ticho dorovná mixer). Preferuje se rubberband (kvalita), fallback
je ffmpeg `atempo`. Tempo se omezí mezemi z configu, aby řeč nezněla jako veverka.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app import config
from . import _ffmpeg as ff


def _rubberband(src: Path, out: Path, ratio: float, log=None) -> None:
    """ratio = výsledná_délka / vstupní_délka (rubberband --time)."""
    exe = config.find_rubberband_exe()
    if not exe:
        raise ff.DubError("rubberband není k dispozici.")
    cmd = [str(exe), "--time", f"{ratio:.5f}", "--pitch", "0",
           str(src), str(out)]
    ff._log(log, "RUBBERBAND: " + " ".join(cmd))
    import subprocess
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300,
                              **ff._popen_kwargs())
    except FileNotFoundError:
        raise ff.DubError(f"rubberband binárka nenalezena: {exe}")
    except subprocess.TimeoutExpired:
        raise ff.DubError("rubberband překročil timeout (300 s).")
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-10:]
        ff._log(log, "RUBBERBAND chyba:\n" + "\n".join(tail))
        raise ff.DubError(f"rubberband selhal (kód {proc.returncode}).")


def _atempo(src: Path, out: Path, tempo: float, rate: int, log=None) -> None:
    # atempo platí 0.5–2.0; naše tempo je už v [MIN_TEMPO, MAX_TEMPO] uvnitř.
    ff.run(["-i", str(src), "-filter:a", f"atempo={tempo:.5f}",
            "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", str(out)], log=log)


def fit_clip(in_wav, out_wav, target_dur: float, mode: Optional[str] = None,
             log=None) -> float:
    """Zarovná `in_wav` na `target_dur` do `out_wav`. Vrací výslednou délku [s]."""
    in_wav = Path(in_wav)
    out_wav = Path(out_wav)
    rate = config.MIX_RATE
    method = (mode or config.TIMESTRETCH or "auto").lower()

    natural = ff.duration(in_wav)
    # bez cíle / bez délky / vypnuto -> jen kanonický formát (beze změny tempa)
    if method == "off" or target_dur <= 0 or natural <= 0:
        ff.to_canonical(in_wav, out_wav, rate, log=log)
        return ff.duration(out_wav)

    tempo = natural / target_dur          # >1 => klip je delší než slot => zrychlit
    tempo = max(config.MIN_TEMPO, min(config.MAX_TEMPO, tempo))
    if abs(tempo - 1.0) < 0.02:           # rozdíl zanedbatelný
        ff.to_canonical(in_wav, out_wav, rate, log=log)
        return ff.duration(out_wav)

    ratio = 1.0 / tempo                    # výsledná/vstupní délka
    if method in ("auto", "rubberband") and config.find_rubberband_exe():
        try:
            _rubberband(in_wav, out_wav, ratio, log=log)
            return ff.duration(out_wav)
        except ff.DubError:
            if method == "rubberband":
                raise
            ff._log(log, "rubberband selhal, fallback na atempo.")
    _atempo(in_wav, out_wav, tempo, rate, log=log)
    return ff.duration(out_wav)
