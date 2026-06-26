"""
Správa dabingových jobů. Žádná databáze – každý job je JSON v jobs/{id}.json.
Vlákno-bezpečné (pipeline běží v samostatném vlákně). Navazuje na model
z AutoSRT, rozšířený o pole dabingu.
"""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import config

# Stavy pipeline dabingu (pro UI i progres).
VALID_STATUSES = [
    "queued", "extracting_audio", "transcribing", "translating",
    "synthesizing", "aligning", "mixing", "muxing", "burning",
    "done", "error",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class DubJob:
    id: str
    filename: str
    upload_path: str
    is_video: bool = True
    source_lang: str = config.DEFAULT_SOURCE
    target_lang: str = config.DEFAULT_TARGET
    tts_engine: str = config.TTS_ENGINE
    voice: Optional[str] = None
    audio_mode: str = config.AUDIO_MODE          # replace | voiceover
    burn_subs: bool = False
    llm_correct: bool = True

    status: str = "queued"
    progress: int = 0
    duration: float = 0.0
    segments_count: int = 0
    text_preview: str = ""

    output_video: Optional[str] = None
    output_audio: Optional[str] = None
    output_srt_src: Optional[str] = None
    output_srt_tgt: Optional[str] = None
    output_json: Optional[str] = None

    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    log_path: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


_RUNNING_STATUSES = {
    "queued", "extracting_audio", "transcribing", "translating",
    "synthesizing", "aligning", "mixing", "muxing", "burning",
}


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        config.ensure_dirs()
        self._recover_stale()

    def _recover_stale(self) -> None:
        """Při startu označí joby přerušené restartem serveru jako chybu."""
        for f in config.JOBS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                status = data.get("status")
                if status not in _RUNNING_STATUSES:
                    continue
                # queued + started_at=None → jen nahráno, ještě nezahájeno → ponech
                if status == "queued" and not data.get("started_at"):
                    continue
                data["status"] = "error"
                data["progress"] = 0
                data["error"] = "Job byl přerušen restartem serveru."
                data["finished_at"] = _now()
                f.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            except Exception:
                pass

    def _job_file(self, job_id: str) -> Path:
        return config.JOBS_DIR / f"{job_id}.json"

    def create(self, filename: str, upload_path: str, **kw) -> DubJob:
        job_id = uuid.uuid4().hex[:12]
        job = DubJob(
            id=job_id, filename=filename, upload_path=upload_path,
            log_path=str(config.LOGS_DIR / f"{job_id}.log"),
            **{k: v for k, v in kw.items() if k in DubJob.__dataclass_fields__},
        )
        self.save(job)
        return job

    def save(self, job: DubJob) -> None:
        with self._lock:
            self._job_file(job.id).write_text(
                json.dumps(job.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def get(self, job_id: str) -> Optional[DubJob]:
        path = self._job_file(job_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return DubJob(**{k: v for k, v in data.items()
                             if k in DubJob.__dataclass_fields__})
        except Exception:
            return None

    def update(self, job_id: str, **fields) -> Optional[DubJob]:
        with self._lock:
            job = self.get(job_id)
            if not job:
                return None
            for k, v in fields.items():
                if hasattr(job, k):
                    setattr(job, k, v)
            self.save(job)
            return job

    def set_status(self, job_id: str, status: str,
                   progress: Optional[int] = None) -> Optional[DubJob]:
        fields: dict = {"status": status}
        if progress is not None:
            fields["progress"] = progress
        if status in ("done", "error"):
            fields["finished_at"] = _now()
        return self.update(job_id, **fields)

    def try_queue(self, job_id: str, **fields) -> bool:
        """Atomically check that the job is startable and mark it as queued.

        Returns True if the job was successfully transitioned to 'queued'.
        Returns False if the job is already running (or doesn't exist),
        preventing a duplicate pipeline start under concurrent requests.

        A fresh "queued" job (started_at=None, never dispatched) is startable.
        A "queued" job with started_at already set means a pipeline was already
        dispatched for it — block to prevent double-start.
        """
        with self._lock:
            job = self.get(job_id)
            if not job:
                return False
            if job.status == "queued" and job.started_at is not None:
                return False
            if job.status not in ("queued", "done", "error"):
                return False
            for k, v in fields.items():
                if hasattr(job, k):
                    setattr(job, k, v)
            job.status = "queued"
            job.progress = 2
            self.save(job)
            return True

    def list(self) -> List[dict]:
        with self._lock:
            jobs: List[dict] = []
            for f in config.JOBS_DIR.glob("*.json"):
                try:
                    jobs.append(json.loads(f.read_text(encoding="utf-8")))
                except Exception:
                    continue
        jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return jobs

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self.get(job_id)
            if not job:
                return False
            for p in (job.upload_path, job.log_path, job.output_video,
                      job.output_audio, job.output_srt_src,
                      job.output_srt_tgt, job.output_json):
                if p:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except Exception:
                        pass
            # smaž dočasný work adresář (TTS klipy, zarovnané WAV, apod.)
            work_dir = config.WORK_DIR / job_id
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass
            try:
                self._job_file(job_id).unlink(missing_ok=True)
            except Exception:
                pass
            return True

    # --- logy ------------------------------------------------------------
    _LOG_MAX_BYTES = 512 * 1024   # 512 KB na log

    def append_log(self, job: Optional[DubJob], msg: str) -> None:
        if not job or not job.log_path:
            return
        try:
            p = Path(job.log_path)
            if p.is_file() and p.stat().st_size > self._LOG_MAX_BYTES:
                # Uřízni první třetinu, zachovej konec (nové záznamy)
                text = p.read_text(encoding="utf-8", errors="replace")
                p.write_text(text[len(text) // 3:], encoding="utf-8")
            with open(job.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{_now()}] {msg}\n")
        except Exception:
            pass

    def read_log(self, job_id: str) -> str:
        job = self.get(job_id)
        if not job or not job.log_path:
            return ""
        p = Path(job.log_path)
        return p.read_text(encoding="utf-8") if p.is_file() else ""
