"""
Faster-Whisper ASR backend — čistě Python, bez binárky.

    pip install faster-whisper

Model se stáhne automaticky z HuggingFace při prvním použití.
Doporučené modely pro češtinu (CPU):
  - large-v3   → nejpřesnější (~3 GB)
  - medium     → dobrý kompromis (~1.5 GB, výchozí)
  - small      → rychlý start (~500 MB)

Env. proměnné:
  PZ_WHISPER_MODEL   = medium   (tiny|base|small|medium|large-v2|large-v3)
  PZ_WHISPER_DEVICE  = cpu      (cpu|cuda|auto)
  PZ_WHISPER_COMPUTE = int8     (int8|float16|float32)
"""
from __future__ import annotations

import os
import re
import threading
from typing import Callable, List, Optional

LogFn = Optional[Callable[[str], None]]

_model_cache: dict = {}   # (model_name, device, compute) -> WhisperModel
_model_lock = threading.Lock()

# Mapování PZ locale → Whisper jazyk
_LANG_MAP: dict = {
    "auto":  None,
    "cs-CZ": "cs",
    "en-US": "en",
    "uk-UA": "uk",
    "ru-RU": "ru",
    "de-DE": "de",
    "pl-PL": "pl",
    "sk-SK": "sk",
    "es-ES": "es",
    "fr-FR": "fr",
    "it-IT": "it",
}

_TAG_RE = re.compile(r"<[^>\s]{1,32}>")


def _clean(s: str) -> str:
    s = _TAG_RE.sub(" ", s or "")
    s = re.sub(r"\s+([,.;:!?…])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def is_available() -> bool:
    """True pokud je faster-whisper nainstalovaný."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def get_model_name() -> str:
    return os.environ.get("PZ_WHISPER_MODEL", "medium")


def _group_words(words: List[dict]) -> List[dict]:
    """Slova {w, start, end, conf} → titulkové segmenty."""
    MAX_CHARS, MAX_DUR, MAX_GAP = 64, 6.0, 1.0
    segments: List[dict] = []
    cur: List[dict] = []
    cur_start: Optional[float] = None
    cur_end: float = 0.0
    last_end: Optional[float] = None

    def flush() -> None:
        nonlocal cur, cur_start
        if cur:
            text = _clean(" ".join(t["w"] for t in cur))
            if text:
                segments.append({
                    "start": round(cur_start or 0.0, 3),
                    "end": round(cur_end, 3),
                    "text": text,
                    "tokens": cur[:],
                })
        cur.clear()
        cur_start = None

    for w in words:
        word = (w.get("w") or "").strip()
        if not word:
            continue
        start = float(w.get("start", cur_end))
        end = float(w.get("end", start))
        gap = (start - last_end) if last_end is not None else 0.0
        candidate = " ".join([t["w"] for t in cur] + [word]).strip()
        if cur and (len(candidate) > MAX_CHARS
                    or (cur_start is not None and end - cur_start > MAX_DUR)
                    or gap > MAX_GAP):
            flush()
        if cur_start is None:
            cur_start = start
        cur.append({"w": word, "conf": round(float(w.get("conf", 1.0)), 3),
                    "start": round(start, 3), "end": round(end, 3)})
        cur_end = end
        last_end = end
        if word.endswith((".", "!", "?", "…")) and len(candidate) >= MAX_CHARS // 2:
            flush()
    flush()
    return segments


def _fallback_segments(text: str, duration: float) -> List[dict]:
    text = (text or "").strip()
    if not text:
        return []
    dur = duration or max(2.0, len(text) / 14.0)
    sents = [s.strip() for s in re.findall(r"[^.!?…]+[.!?…]?", text) if s.strip()]
    if not sents:
        sents = [text]
    total_c = sum(len(s) for s in sents) or 1
    cursor, segs = 0.0, []
    for s in sents:
        seg_dur = max(1.0, dur * len(s) / total_c)
        segs.append({"start": round(cursor, 3),
                     "end": round(min(dur, cursor + seg_dur), 3), "text": s})
        cursor += seg_dur
    if segs:
        segs[-1]["end"] = round(max(segs[-1]["end"], dur), 3)
    return segs


def transcribe(wav_path: str, language: str, duration: float = 0.0,
               log: LogFn = None) -> dict:
    """
    Přepíše WAV pomocí faster-whisper.

    Vrací dict kompatibilní s parakeet enginem:
      {"text": ..., "segments": [...], "words": [...],
       "backend": "faster-whisper", "model": "<name>"}

    Modely se stáhnou automaticky z HuggingFace při prvním použití.
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper není nainstalovaný. Spusť: pip install faster-whisper"
        )

    model_name = os.environ.get("PZ_WHISPER_MODEL", "medium")
    device = os.environ.get("PZ_WHISPER_DEVICE", "cpu")
    compute = os.environ.get("PZ_WHISPER_COMPUTE", "int8")
    lang = _LANG_MAP.get(language)

    cache_key = (model_name, device, compute)
    with _model_lock:
        if cache_key not in _model_cache:
            _log(f"Whisper ASR: model={model_name}, device={device}, "
                 f"compute={compute}, jazyk={lang or 'auto'}")
            _log("Whisper: načítám model (první start = stahování z HuggingFace)…")
            try:
                _model_cache[cache_key] = WhisperModel(
                    model_name, device=device, compute_type=compute)
            except Exception as e:
                raise RuntimeError(
                    f"faster-whisper nepodařilo načíst model '{model_name}': {e}. "
                    "Zkontroluj připojení k internetu nebo zvol jiný model "
                    "(PZ_WHISPER_MODEL=small)."
                ) from e
        else:
            _log(f"Whisper ASR: model={model_name} (cache), jazyk={lang or 'auto'}")
    model = _model_cache[cache_key]

    _log("Whisper: přepisuji…")
    segments_gen, info = model.transcribe(
        wav_path,
        language=lang,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )

    detected = getattr(info, "language", None) or lang or "?"
    _log(f"Whisper: detekovaný jazyk={detected}")

    words: List[dict] = []
    seg_texts: List[str] = []
    for seg in segments_gen:
        for w in (seg.words or []):
            word = (w.word or "").strip()
            if word:
                words.append({
                    "w": word,
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "conf": round(float(w.probability), 3),
                })
        seg_texts.append(seg.text.strip())

    text = _clean(" ".join(seg_texts))
    segments = _group_words(words) if words else _fallback_segments(text, duration)

    _log(f"Whisper hotovo: {len(text)} znaků, {len(segments)} segmentů")
    # detected_language: kód Whisperu (cs/en/uk…) pro zpětné mapování v pipeline
    result: dict = {
        "text": text,
        "segments": segments,
        "words": words,
        "backend": "faster-whisper",
        "model": model_name,
    }
    if detected and detected != "?":
        result["detected_language"] = detected
    return result
