"""
Mux dabingové zvukové stopy do videa.

Video se kopíruje beze ztrát (`-c:v copy`), mění se jen zvuk → rychlé a bez
ztráty kvality obrazu. Délka se ořízne podle kratšího proudu (`-shortest`),
aby nevznikl audio/video drift na konci.
"""
from __future__ import annotations

from pathlib import Path

from . import _ffmpeg as ff


def mux_video(video_in, audio_wav, out_video, log=None) -> Path:
    """Nahradí zvuk videa dabingovou stopou. Vrací cestu k výslednému videu."""
    out_video = Path(out_video)
    ff.run(["-i", str(video_in), "-i", str(audio_wav),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest", str(out_video)], log=log)
    if not out_video.is_file() or out_video.stat().st_size == 0:
        raise ff.DubError("Mux nevytvořil výstupní video (prázdný soubor).")
    return out_video


def export_audio(audio_wav, out_path, log=None) -> Path:
    """Pro audio-only vstup: zabalí dabingovou stopu do MP3/WAV výstupu."""
    out_path = Path(out_path)
    if out_path.suffix.lower() == ".mp3":
        ff.run(["-i", str(audio_wav), "-c:a", "libmp3lame", "-b:a", "192k",
                str(out_path)], log=log)
    else:
        ff.run(["-i", str(audio_wav), "-c:a", "pcm_s16le", str(out_path)], log=log)
    return out_path
