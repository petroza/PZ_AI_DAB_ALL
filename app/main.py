"""
PZ AI DAB ALL — FastAPI backend (sjednocené UI + API pro dabing).

Spuštění (z kořene projektu):
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8790
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engines.asr import asr_engine, ffmpeg_tools
from engines.tts import get_backend

from . import config, pipeline
from .job_manager import JobManager

config.ensure_dirs()

app = FastAPI(title="PZ AI DAB ALL", version="1.0.0")

# --- CORS + Private Network Access ---------------------------------------
# Worker běží lokálně (127.0.0.1:8790), ale frontend je servírovaný z Forpsi
# (https://www.appcrate.cloud/ALLDUB). Aby prohlížeč pustil volání z HTTPS
# stránky na lokální worker, musí worker:
#   1) vracet CORS hlavičky pro povolené originy (appcrate.cloud – BEZ „e"!),
#   2) odpovědět na PNA preflight hlavičkou Access-Control-Allow-Private-Network.
# Originy lze přepsat přes DAB_CORS_ORIGINS (čárkou oddělený seznam).
_DEFAULT_ORIGINS = (
    "https://www.appcrate.cloud,https://appcrate.cloud,"
    "https://www.appcreate.cloud,https://appcreate.cloud,"
    "http://127.0.0.1:8790,http://localhost:8790"
)
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DAB_CORS_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=600,
)


@app.middleware("http")
async def _cors_preflight_pna(request, call_next):
    """Preflight (OPTIONS) z Forpsi na lokální worker zpracujeme SAMI.

    Starlette 1.3 vrací u Private Network Access preflightu (hlavička
    Access-Control-Request-Private-Network: true) chybně 400 → Chrome pak
    zablokuje nahrávání a další POST. Tady na povolený origin vrátíme 200 se
    správnými CORS+PNA hlavičkami; běžné požadavky obslouží CORSMiddleware."""
    origin = request.headers.get("origin", "")
    if (request.method == "OPTIONS"
            and "access-control-request-method" in request.headers
            and origin in ALLOWED_ORIGINS):
        req_headers = request.headers.get("access-control-request-headers") or "*"
        return Response(status_code=200, headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": req_headers,
            "Access-Control-Allow-Private-Network": "true",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        })
    return await call_next(request)


jobs = JobManager()

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _valid_job_id(job_id: str) -> bool:
    return bool(_JOB_ID_RE.match(job_id))


class DubRequest(BaseModel):
    source_lang: "str | None" = None
    target_lang: "str | None" = None
    tts_engine: "str | None" = None
    voice: "str | None" = None
    audio_mode: "str | None" = None       # replace | voiceover | subtitles
    burn_subs: "bool | None" = None
    llm_correct: "bool | None" = None


def _ollama_status() -> dict:
    """Lehká kontrola, jestli běží Ollama (pro překlad). Neblokuje dlouho."""
    from engines.asr import config as asr
    try:
        import requests
        base = asr.OLLAMA_URL.rsplit("/api/", 1)[0]
        r = requests.get(base + "/api/tags", timeout=2)
        return {"ok": r.ok, "url": asr.OLLAMA_URL, "model": asr.OLLAMA_MODEL}
    except Exception:
        return {"ok": False, "url": asr.OLLAMA_URL, "model": asr.OLLAMA_MODEL}


@app.get("/api/status")
def api_status() -> dict:
    ff = ffmpeg_tools.check_ffmpeg()
    eng = asr_engine.engine_status()
    ollama = _ollama_status()
    piper_ready, piper_info = get_backend("piper").is_ready()
    xtts_ready, xtts_info = get_backend("xtts").is_ready()
    vs_ready, vs_info = get_backend("voicestudio").is_ready()
    cs_voice = config.find_piper_voice("cs-CZ")
    translate_ok = ollama["ok"] or eng["argostranslate_ok"]
    return {
        "app": "PZ AI DAB ALL",
        "version": app.version,
        "python": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 11),
        "platform": platform.platform(),
        "ffmpeg": ff,
        "parakeet": {"ok": eng["parakeet_ok"], "exe": eng["parakeet_exe"]},
        "model": {"ok": eng["model_ok"], "name": eng["model_name"]},
        "whisper": {"ok": eng["whisper_ok"], "model": eng.get("whisper_model")},
        "asr": {"ok": eng["asr_ok"]},
        "ollama": ollama,
        "argostranslate": {"ok": eng["argostranslate_ok"],
                           "langs": eng["argostranslate_langs"]},
        "translate": {"ok": translate_ok},
        "tts": {
            "default": config.TTS_ENGINE,
            "piper": {"ok": piper_ready, "info": piper_info,
                      "cs_voice": bool(cs_voice)},
            "xtts": {"ok": xtts_ready, "info": xtts_info},
            "voicestudio": {"ok": vs_ready, "info": vs_info,
                            "url": config.VOICESTUDIO_URL},
        },
        "source_languages": config.SOURCE_LANGUAGES,
        "target_languages": config.TARGET_LANGUAGES,
        "defaults": {"source": config.DEFAULT_SOURCE,
                     "target": config.DEFAULT_TARGET,
                     "audio_mode": config.AUDIO_MODE,
                     "burn_subs": config.BURN_SUBS},
        "ready": ff["ok"] and eng["asr_ok"] and piper_ready,
    }


@app.get("/api/voices")
def api_voices() -> dict:
    return {"piper": get_backend("piper").list_voices(),
            "voicestudio": get_backend("voicestudio").list_voices(),
            "xtts": get_backend("xtts").list_voices()}


@app.get("/api/voice_preview")
def api_voice_preview(voice: str = "") -> FileResponse:
    """Krátká ukázka vybraného vestavěného hlasu XTTS (pro poslech ve webu)."""
    voice = (voice or "").strip()
    if not voice:
        raise HTTPException(400, "Chybí jméno hlasu.")
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", voice)[:60]
    pdir = config.WORK_DIR / "previews"
    pdir.mkdir(parents=True, exist_ok=True)
    out = pdir / f"{safe}.wav"
    if not out.is_file() or out.stat().st_size == 0:
        try:
            get_backend("xtts").synth(
                "Dobrý den, vítejte u zpráv. Toto je ukázka tohoto hlasu.",
                out, voice=voice, lang="cs-CZ")
        except Exception as e:
            raise HTTPException(503, f"Náhled hlasu selhal: {e}")
    return FileResponse(out, media_type="audio/wav", filename=f"{safe}.wav")


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> dict:
    filename = Path(file.filename or "video").name
    ext = Path(filename).suffix.lower()
    if ext not in config.SUPPORTED_INPUT_EXT:
        raise HTTPException(
            400, f"Nepodporovaný formát '{ext}'. Povolené: "
            f"{', '.join(sorted(config.SUPPORTED_INPUT_EXT))}")
    job = jobs.create(filename, "")
    upload_path = config.UPLOADS_DIR / f"{job.id}{ext}"
    try:
        with open(upload_path, "wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        jobs.delete(job.id)
        try:
            upload_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(500, f"Nahrání souboru selhalo: {e}")
    if not upload_path.is_file() or upload_path.stat().st_size == 0:
        jobs.delete(job.id)
        try:
            upload_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(500, "Nahrání selhalo: prázdný soubor.")
    jobs.update(job.id, upload_path=str(upload_path),
                is_video=(ext in config.SUPPORTED_VIDEO_EXT))
    jobs.append_log(jobs.get(job.id), f"Nahráno: {filename} -> {upload_path.name}")
    return {"job_id": job.id, "job": jobs.get(job.id).to_dict()}


@app.post("/api/dub/{job_id}")
def api_dub(job_id: str, req: "DubRequest | None" = Body(default=None)) -> dict:
    if not _valid_job_id(job_id):
        raise HTTPException(400, "Neplatné job_id.")
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job nenalezen.")
    if not job.upload_path or not Path(job.upload_path).is_file():
        raise HTTPException(400, "Chybí nahraný soubor pro tento job.")

    queued_at = datetime.now().isoformat(timespec="seconds")
    upd: dict = {"error": None, "started_at": queued_at, "finished_at": None,
                 "duration": 0.0, "segments_count": 0, "text_preview": ""}
    if req:
        for k in ("source_lang", "target_lang", "tts_engine", "voice",
                  "audio_mode", "burn_subs", "llm_correct"):
            v = getattr(req, k)
            if v is not None:
                upd[k] = v
    if upd.get("audio_mode", job.audio_mode) not in ("replace", "voiceover", "subtitles"):
        raise HTTPException(400, "Neplatný režim zvuku.")
    if upd.get("audio_mode", job.audio_mode) == "subtitles":
        upd["burn_subs"] = True
    if not jobs.try_queue(job_id, **upd):
        raise HTTPException(409, "Job už běží.")
    threading.Thread(target=pipeline.run_dub, args=(jobs, job_id),
                     daemon=True).start()
    return {"job_id": job_id, "status": "started"}


@app.get("/api/jobs")
def api_jobs() -> dict:
    return {"jobs": jobs.list()}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    if not _valid_job_id(job_id):
        raise HTTPException(400, "Neplatné job_id.")
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job nenalezen.")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/log")
def api_job_log(job_id: str) -> PlainTextResponse:
    if not _valid_job_id(job_id):
        raise HTTPException(400, "Neplatné job_id.")
    if not jobs.get(job_id):
        raise HTTPException(404, "Job nenalezen.")
    return PlainTextResponse(jobs.read_log(job_id))


@app.get("/api/download/{job_id}/{kind}")
def api_download(job_id: str, kind: str) -> FileResponse:
    if not _valid_job_id(job_id):
        raise HTTPException(400, "Neplatné job_id.")
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job nenalezen.")
    mapping = {
        "video": (job.output_video, "video/mp4", "mp4"),
        "audio": (job.output_audio, "audio/mpeg", "mp3"),
        "srt_src": (job.output_srt_src, "application/x-subrip", "src.srt"),
        "srt_tgt": (job.output_srt_tgt, "application/x-subrip", "srt"),
        "json": (job.output_json, "application/json", "json"),
    }
    entry = mapping.get(kind)
    if not entry or not entry[0] or not Path(entry[0]).is_file():
        raise HTTPException(404, f"Výstup '{kind}' pro tento job neexistuje.")
    path, media, ext = entry
    stem = Path(job.filename).stem
    # MP4 se zapečenými titulky a samostatné SRT nesmějí mít stejný název:
    # VLC a další přehrávače by SRT automaticky načetly a zobrazily titulky 2×.
    if kind == "video":
        suffix = "s-ceskymi-titulky.mp4" if job.burn_subs else "dabing.mp4"
        name = f"{stem}.{suffix}"
    elif kind == "srt_tgt":
        name = f"{stem}.samostatne-ceske-titulky.srt"
    else:
        name = f"{stem}.{ext}"
    return FileResponse(path, media_type=media, filename=name)


@app.post("/api/jobs/clear")
def api_clear(scope: str = "finished") -> dict:
    """Hromadně promaže frontu. scope=finished (jen hotové/chybné) | all
    (i čekající nespuštěné). Aktivně běžící zakázka zůstává."""
    if scope not in ("finished", "all"):
        raise HTTPException(400, "Neplatný scope (finished | all).")
    return jobs.clear(scope)


@app.delete("/api/jobs/{job_id}")
def api_delete(job_id: str) -> dict:
    if not _valid_job_id(job_id):
        raise HTTPException(400, "Neplatné job_id.")
    if not jobs.delete(job_id):
        raise HTTPException(404, "Job nenalezen.")
    return {"deleted": job_id}


# frontend (mount NAKONEC, ať /api/* má přednost)
app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True),
          name="static")
