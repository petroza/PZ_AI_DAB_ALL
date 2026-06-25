"""
Sestavení souvislé dabingové zvukové stopy z jednotlivých klipů.

Klipy jsou seřazené podle času; staví se sekvenčně: ticho do startu klipu →
klip → ticho → … Když klip kvůli omezení tempa přeteče svůj slot, další klip ho
těsně navazuje (drobný posun místo překryvu – pro voice-over přijatelné).

Režimy:
  replace   – stopa je čistě dabing
  voiceover – dabing přes ztlumený původní zvuk (zachová hudbu/ruchy)
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from app import config
from engines.tts.base import VoiceClip
from . import _ffmpeg as ff


def _concat(pieces: List[Path], out_wav: Path, rate: int, work: Path,
            log=None) -> Path:
    """Spojí kanonické WAV kusy (stejný formát) do jednoho přes concat demuxer."""
    listing = work / "concat.txt"
    listing.write_text(
        "".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in pieces),
        encoding="utf-8",
    )
    ff.run(["-f", "concat", "-safe", "0", "-i", str(listing),
            "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
           log=log)
    return out_wav


def _duck_mix(original_wav: Path, vo_wav: Path, out_wav: Path, rate: int,
              log=None) -> Path:
    """Ztlumí originál o DUCK_DB a smíchá s voice-overem."""
    fc = (f"[0:a]volume={config.DUCK_DB:.2f}dB[d];"
          f"[d][1:a]amix=inputs=2:normalize=0:duration=longest[a]")
    ff.run(["-i", str(original_wav), "-i", str(vo_wav),
            "-filter_complex", fc, "-map", "[a]",
            "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
           log=log)
    return out_wav


def build_track(clips: List[VoiceClip], total_dur: float, out_wav,
                work_dir, mode: str = "replace",
                original_wav: Optional[str] = None, log=None) -> Path:
    """Sestaví dabingovou stopu. Vrací cestu k výsledku (`out_wav`)."""
    out_wav = Path(out_wav)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    rate = config.MIX_RATE

    pieces: List[Path] = []
    cursor = 0.0
    usable = sorted([c for c in clips if (c.fitted_wav or c.raw_wav)],
                    key=lambda c: c.start)
    for c in usable:
        src = c.fitted_wav or c.raw_wav
        gap = c.start - cursor
        if gap > 0.02:
            sil = work / f"sil_{c.index:05d}.wav"
            ff.make_silence(sil, gap, rate, log=log)
            pieces.append(sil)
            cursor += gap
        can = work / f"clip_{c.index:05d}.can.wav"
        ff.to_canonical(src, can, rate, gain_db=config.TTS_GAIN_DB, log=log)
        d = ff.duration(can)
        pieces.append(can)
        cursor += d

    if total_dur and cursor < total_dur - 0.02:
        sil = work / "sil_tail.wav"
        ff.make_silence(sil, total_dur - cursor, rate, log=log)
        pieces.append(sil)

    if not pieces:                       # nic k namixování -> ticho délky videa
        return ff.make_silence(out_wav, max(0.5, total_dur or 0.5), rate, log=log)

    if mode == "voiceover" and original_wav:
        bed = work / "vo_bed.wav"
        _concat(pieces, bed, rate, work, log=log)
        return _duck_mix(Path(original_wav), bed, out_wav, rate, log=log)

    return _concat(pieces, out_wav, rate, work, log=log)
