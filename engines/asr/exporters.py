"""
Export přepisu do TXT / SRT / VTT / JSON.

Vstup je vždy "result" dict z asr_engine.transcribe_file():
  {
    "text": "...",
    "segments": [{"start": 0.0, "end": 2.5, "text": "..."}],
    ...
  }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional


def _format_ts(seconds: float, sep: str) -> str:
    """seconds -> HH:MM:SS<sep>mmm. sep="," pro SRT, "." pro VTT."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def format_srt_time(seconds: float) -> str:
    return _format_ts(seconds, ",")


def format_vtt_time(seconds: float) -> str:
    return _format_ts(seconds, ".")


def _full_text(result: dict) -> str:
    text = (result.get("text") or "").strip()
    if text:
        return text
    return " ".join(s.get("text", "").strip()
                    for s in result.get("segments", [])).strip()


def write_txt(result: dict, path: Path) -> Path:
    path.write_text(_full_text(result) + "\n", encoding="utf-8")
    return path


def write_srt(result: dict, path: Path) -> Path:
    segments = result.get("segments", [])
    lines: List[str] = []
    for i, seg in enumerate(segments, start=1):
        start = format_srt_time(seg.get("start", 0.0))
        end = format_srt_time(seg.get("end", 0.0))
        text = seg.get("text", "").strip()
        lines += [str(i), f"{start} --> {end}", text, ""]
    body = "\n".join(lines).strip()
    path.write_text((body + "\n") if body else "", encoding="utf-8")
    return path


def write_vtt(result: dict, path: Path) -> Path:
    segments = result.get("segments", [])
    lines: List[str] = ["WEBVTT", ""]
    for seg in segments:
        start = format_vtt_time(seg.get("start", 0.0))
        end = format_vtt_time(seg.get("end", 0.0))
        text = seg.get("text", "").strip()
        lines += [f"{start} --> {end}", text, ""]
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def write_json(result: dict, path: Path, meta: Optional[dict] = None) -> Path:
    payload = {
        "text": result.get("text", ""),
        "segments": result.get("segments", []),
    }
    if result.get("words"):
        payload["words"] = result["words"]
    if meta:
        payload["meta"] = meta
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def export_all(result: dict, out_dir: Path, base_name: str,
               formats, meta: Optional[dict] = None) -> dict:
    """Vytvoří vyžádané výstupní soubory. Vrací {format: cesta}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict = {}
    if "txt" in formats:
        paths["txt"] = str(write_txt(result, out_dir / f"{base_name}.txt"))
    if "srt" in formats:
        paths["srt"] = str(write_srt(result, out_dir / f"{base_name}.srt"))
    if "vtt" in formats:
        paths["vtt"] = str(write_vtt(result, out_dir / f"{base_name}.vtt"))
    if "json" in formats:
        paths["json"] = str(write_json(result, out_dir / f"{base_name}.json", meta))
    return paths
