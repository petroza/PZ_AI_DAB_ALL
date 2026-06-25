"""
PZ Voice Studio backend — HTTP klient k samostatně běžící appce PZ_AI_voice
(výchozí http://127.0.0.1:7867). Umožní dabovat hlasy a enginy ze Studia
(Piper i Chatterbox, klonování hlasu) bez kopírování jejich kódu.

Tok (dle API Studia):
  POST /api/queue/add-start  {text, engine, voice, language, speed, ...}
  GET  /api/jobs             -> pole jobů se stavem a URL hotového audia
  GET  /outputs/<soubor>     -> stažení WAV

POZN.: tvary odpovědí se mezi verzemi Studia mohou lišit, proto klient čte
pole defenzivně (zkouší víc názvů). Když něco nesedí, je vidět v logu jobu.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from app import config
from .base import TTSBackend, TTSError, TTSNotReady


def _log(log, msg: str) -> None:
    if log:
        log(msg)


_DONE = {"done", "finished", "ok", "complete", "completed", "success"}
_FAIL = {"error", "failed", "stopped", "cancelled", "canceled"}
_URL_FIELDS = ("audio_url", "audio", "url", "download", "download_url",
               "out_url", "wav", "wav_url", "output", "file")


def _jobs_list(payload) -> list:
    if isinstance(payload, dict):
        for k in ("jobs", "items", "data", "queue"):
            if isinstance(payload.get(k), list):
                return payload[k]
        return []
    return payload if isinstance(payload, list) else []


def _find_url(job: dict) -> Optional[str]:
    for k in _URL_FIELDS:
        v = job.get(k)
        if isinstance(v, str) and v and (v.endswith((".wav", ".mp3", ".flac"))
                                         or "/outputs/" in v):
            return v
    return None


class VoiceStudioBackend(TTSBackend):
    name = "voicestudio"

    def __init__(self, base_url: Optional[str] = None,
                 engine: Optional[str] = None):
        self.base = (base_url or config.VOICESTUDIO_URL).rstrip("/")
        self.engine = engine or config.VOICESTUDIO_ENGINE
        self.timeout = config.VOICESTUDIO_TIMEOUT

    def _requests(self):
        try:
            import requests
            return requests
        except Exception:
            raise TTSNotReady("Chybí balíček 'requests' (pip install requests).")

    def is_ready(self) -> "tuple[bool, str]":
        try:
            requests = self._requests()
            r = requests.get(self.base + "/api/ping", timeout=1)
            r.raise_for_status()
            return True, self.base
        except Exception as e:
            return False, f"PZ Voice Studio neodpovídá na {self.base} ({e})."

    def synth(self, text: str, out_wav: Path, voice: Optional[str] = None,
              lang: Optional[str] = None, speed: float = 1.0, log=None) -> Path:
        text = (text or "").strip()
        if not text:
            raise TTSError("Prázdný text pro syntézu.")
        out_wav = Path(out_wav)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        requests = self._requests()

        # 1) snímek existujících jobů (ať poznáme ten nový)
        before = set()
        try:
            r = requests.get(self.base + "/api/jobs", timeout=15)
            before = {j.get("id") for j in _jobs_list(r.json()) if j.get("id")}
        except Exception:
            pass

        # 2) zařadit + spustit
        body = {"text": text, "engine": self.engine, "speed": speed,
                "export_mp3": False, "postprocess": "none"}
        if voice:
            body["voice"] = voice
        if lang:
            body["language"] = lang
        try:
            r = requests.post(self.base + "/api/queue/add-start", json=body,
                              timeout=30)
            r.raise_for_status()
            start_data = r.json() if r.content else {}
        except Exception as e:
            raise TTSError(f"PZ Voice Studio /api/queue/add-start selhalo: {e}")

        # Zkus vytáhnout ID nového jobu přímo z odpovědi start endpointu
        new_job_id = None
        if isinstance(start_data, dict):
            new_job_id = start_data.get("id") or start_data.get("job_id")

        # 3) počkat na hotový job a získat URL audia
        deadline = time.time() + self.timeout
        target_url = None
        poll_sleep = 1.0
        while time.time() < deadline:
            time.sleep(poll_sleep)
            poll_sleep = min(poll_sleep * 1.25, 5.0)  # mírný backoff do 5 s
            try:
                r = requests.get(self.base + "/api/jobs", timeout=15)
                jobs = _jobs_list(r.json())
            except Exception:
                continue
            # Priorita: přesné ID > nové joby > nejnovější (fallback)
            if new_job_id:
                candidates = [j for j in jobs if j.get("id") == new_job_id]
            else:
                candidates = [j for j in jobs if j.get("id") not in before]
            if not candidates:
                candidates = sorted(jobs, key=lambda x: str(x.get("id")), reverse=True)[:1]
            for j in candidates:
                st = str(j.get("status", "")).lower()
                url = _find_url(j)
                if url and (st in _DONE or st == ""):
                    target_url = url
                    break
                if st in _FAIL:
                    raise TTSError(f"PZ Voice Studio job selhal: {j.get('error') or st}")
            if target_url:
                break
        if not target_url:
            raise TTSError("PZ Voice Studio nevrátilo hotové audio v limitu.")

        # 4) stáhnout
        full = target_url if target_url.startswith("http") else self.base + (
            target_url if target_url.startswith("/") else "/" + target_url)
        try:
            with requests.get(full, stream=True, timeout=120) as rr:
                rr.raise_for_status()
                with open(out_wav, "wb") as f:
                    for chunk in rr.iter_content(1 << 16):
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            raise TTSError(f"Stažení audia z Studia selhalo ({full}): {e}")
        if not out_wav.is_file() or out_wav.stat().st_size == 0:
            raise TTSError("Stažené audio z Studia je prázdné.")
        _log(log, f"PZ Voice Studio: {full} -> {out_wav.name}")
        return out_wav

    def list_voices(self) -> list:
        try:
            requests = self._requests()
            r = requests.get(self.base + "/api/config", timeout=10)
            data = r.json()
            v = data.get("voices") or []
            return v if isinstance(v, list) else []
        except Exception:
            return []
