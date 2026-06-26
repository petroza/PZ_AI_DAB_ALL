"""TTS enginy: Piper (offline, výchozí) + PZ Voice Studio (HTTP klient)."""
from __future__ import annotations

from .base import TTSBackend, TTSError, TTSNotReady, VoiceClip
from .piper import PiperBackend
from .voicestudio import VoiceStudioBackend
from .xtts import XttsBackend

_VOICESTUDIO_ALIASES = {"voicestudio", "studio", "pz_voice", "pzvoice", "chatterbox"}
_XTTS_ALIASES = {"xtts", "coqui", "xtts_v2", "xttsv2"}


def get_backend(name: "str | None" = None, **kwargs) -> TTSBackend:
    """Vrátí instanci TTS backendu podle jména ('piper' | 'voicestudio' | 'xtts')."""
    key = (name or "piper").strip().lower()
    if key in _XTTS_ALIASES:
        return XttsBackend()
    if key in _VOICESTUDIO_ALIASES:
        return VoiceStudioBackend(**kwargs)
    return PiperBackend()


__all__ = [
    "TTSBackend", "TTSError", "TTSNotReady", "VoiceClip",
    "PiperBackend", "VoiceStudioBackend", "XttsBackend", "get_backend",
]
