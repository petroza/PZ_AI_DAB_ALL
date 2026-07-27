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
    slot_ext: float = 0.0              # slot prodloužený do následující pauzy
    words: list = field(default_factory=list)   # slova s časy UVNITŘ klipu (z ASR)
    real_start: float = 0.0            # kdy klip opravdu zazní v hotové stopě

    @property
    def slot(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def fit_slot(self) -> float:
        """Čas, který má řeč reálně k dispozici — slot + ticho za ním.

        Když po replice následuje ve videu pauza, je přirozenější domluvit do ní
        než větu stlačit. Mixer je sekvenční, takže přesah do vlastní pauzy
        neposune následující repliku.
        """
        return max(self.slot, self.slot_ext)


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
