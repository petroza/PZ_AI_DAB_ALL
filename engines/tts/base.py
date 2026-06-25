"""
Společné rozhraní TTS backendů + datové typy dabingu.

Každý backend (Piper, PZ Voice Studio) umí jen jednu věc: z textu udělat WAV
klip. Časování, time-stretch a mux řeší modul dub/ – TTS o nich nic neví.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class TTSError(RuntimeError):
    """Obecná chyba syntézy řeči."""


class TTSNotReady(TTSError):
    """Backend není připravený (chybí binárka, hlas nebo neběží služba)."""


@dataclass
class VoiceClip:
    """Jeden přeložený titulkový segment na cestě k nadabovanému zvuku."""
    index: int
    start: float                       # čas ve videu [s]
    end: float
    text: str
    raw_wav: Optional[str] = None      # surový TTS výstup
    fitted_wav: Optional[str] = None   # po time-stretchi na délku slotu
    natural_dur: float = 0.0           # délka surového klipu
    fitted_dur: float = 0.0            # délka po zarovnání

    @property
    def slot(self) -> float:
        return max(0.0, self.end - self.start)


def popen_kwargs() -> dict:
    """Na Windows skryj okno konzole spouštěného procesu (CREATE_NO_WINDOW)."""
    kwargs: dict = {}
    if hasattr(subprocess, "STARTUPINFO"):
        kwargs["creationflags"] = 0x08000000
    return kwargs


class TTSBackend:
    """Rozhraní backendu. Potomci přepisují is_ready() a synth()."""
    name = "base"

    def is_ready(self) -> "tuple[bool, str]":
        return False, "neimplementováno"

    def synth(self, text: str, out_wav: Path, voice: Optional[str] = None,
              lang: Optional[str] = None, speed: float = 1.0,
              log=None) -> Path:
        raise NotImplementedError

    def list_voices(self) -> List[dict]:
        return []
