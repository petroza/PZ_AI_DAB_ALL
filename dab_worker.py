"""
PZ AI DAB ALL — relay worker (PC).

Připojí se k webu (PHP relay na Forpsi), vyzvedne čekající zakázku, stáhne
video, nadabuje ho LOKÁLNĚ stávající pipeline (app/pipeline.py) a nahraje
výsledek zpět. Díky tomu jde dabovat z mobilu odkudkoliv — web mluví jen
s Forpsi, tento worker si úkoly vyzvedává sám.

Spouštěj přes START_DABWORKER.bat (aktivuje .venv). Nastavení ve worker_config.json.
Pro XTTS hlas musí běžet i START_XTTS.bat.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests

from app import config as appcfg, pipeline
from app.job_manager import JobManager

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "worker_config.json").read_text(encoding="utf-8"))
BASE = (os.environ.get("DAB_RELAY_BASE") or CFG["base_url"]).rstrip("/") + "/"
API = BASE + "worker_api.php"
TOKEN = os.environ.get("DAB_RELAY_TOKEN") or CFG["worker_token"]
POLL = float(CFG.get("poll_interval_sec", 5))
HEAD = {"X-Worker-Token": TOKEN}

jobs = JobManager()


def claim():
    r = requests.get(API, params={"action": "worker_claim"}, headers=HEAD, timeout=30)
    r.raise_for_status()
    return (r.json() or {}).get("job")


def progress(rid, pct):
    try:
        requests.post(API, params={"action": "worker_progress"}, headers=HEAD,
                      data={"id": rid, "progress": int(pct)}, timeout=15)
    except Exception:
        pass


def fail(rid, msg):
    try:
        requests.post(API, params={"action": "worker_fail"}, headers=HEAD,
                      data={"id": rid, "error": str(msg)[:800]}, timeout=15)
    except Exception:
        pass


def download_source(rid, dest: Path):
    with requests.get(API, params={"action": "worker_source", "id": rid},
                      headers=HEAD, stream=True, timeout=900) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for c in r.iter_content(1 << 16):
                if c:
                    f.write(c)
    if dest.stat().st_size == 0:
        raise RuntimeError("stažený zdroj je prázdný")


def upload_result(rid, data, file_specs, timeout=900):
    last = None
    for attempt in range(3):
        fhs = []
        try:
            files = {}
            for field, (fname, path) in file_specs.items():
                fh = open(path, "rb")
                fhs.append(fh)
                files[field] = (fname, fh)
            d = dict(data)
            d["id"] = rid
            r = requests.post(API, params={"action": "worker_result"}, headers=HEAD,
                              data=d, files=files, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            print(f"[upload] pokus {attempt + 1}/3 selhal: {e}")
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
        finally:
            for fh in fhs:
                try:
                    fh.close()
                except Exception:
                    pass
    raise last


def process(job):
    rid = job["id"]
    ext = (job.get("ext") or "mp4").lower()
    is_video = bool(job.get("is_video", True))
    appcfg.ensure_dirs()

    # 1) stáhni zdroj
    progress(rid, 5)
    src = appcfg.UPLOADS_DIR / f"relay_{rid}.{ext}"
    download_source(rid, src)

    # 2) lokální job + spuštění stávající pipeline
    lj = jobs.create(job.get("filename") or f"{rid}.{ext}", str(src), is_video=is_video)
    now = datetime.now().isoformat(timespec="seconds")
    upd = {
        "source_lang": job.get("source_lang") or "auto",
        "target_lang": job.get("target_lang") or "cs-CZ",
        "tts_engine": job.get("tts_engine") or "piper",
        "voice": (job.get("voice") or None),
        "audio_mode": job.get("audio_mode") or "replace",
        "burn_subs": bool(job.get("burn_subs")),
        "llm_correct": bool(job.get("llm_correct", True)),
        "error": None, "started_at": now, "finished_at": None,
    }
    jobs.try_queue(lj.id, **upd)
    t = threading.Thread(target=pipeline.run_dub, args=(jobs, lj.id), daemon=True)
    t.start()

    # 3) přeposílej průběh do relay, dokud pipeline běží
    last = -1
    while t.is_alive():
        lo = jobs.get(lj.id)
        if lo and lo.progress != last:
            last = lo.progress
            progress(rid, max(5, min(98, lo.progress)))
        time.sleep(2)
    t.join(timeout=5)

    lo = jobs.get(lj.id)
    if not lo or lo.status == "error":
        msg = (lo.error if lo else "neznámá chyba") or "dabing selhal"
        jobs.delete(lj.id)
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(msg)

    # 4) nahraj výsledky zpět na web
    specs = {}
    if lo.output_video and Path(lo.output_video).is_file():
        specs["video"] = (f"{rid}.mp4", lo.output_video)
    if lo.output_audio and Path(lo.output_audio).is_file():
        specs["audio"] = (f"{rid}.mp3", lo.output_audio)
    if lo.output_srt_tgt and Path(lo.output_srt_tgt).is_file():
        specs["srt_tgt"] = (f"{rid}.srt", lo.output_srt_tgt)
    if lo.output_srt_src and Path(lo.output_srt_src).is_file():
        specs["srt_src"] = (f"{rid}.src.srt", lo.output_srt_src)
    if not specs:
        raise RuntimeError("pipeline nevytvořila žádný výstup")
    upload_result(rid, {"duration": lo.duration or 0,
                        "text_preview": (lo.text_preview or "")[:8000]}, specs)

    # 5) úklid lokálně (web má výsledky)
    jobs.delete(lj.id)
    try:
        src.unlink(missing_ok=True)
    except Exception:
        pass
    print(f"[OK] {rid} ({job.get('filename')}) hotovo")


def main():
    print("=" * 56)
    print(" PZ AI DAB ALL — relay worker")
    print(" relay:", BASE)
    print(" (dabuje z mobilu/webu; pro XTTS spusť i START_XTTS.bat)")
    print("=" * 56)
    print("Čekám na zakázky (Ctrl+C ukončí)…")
    while True:
        try:
            job = claim()
        except Exception as e:
            print("[poll] relay nedostupný:", e)
            time.sleep(max(POLL, 5))
            continue
        if not job:
            time.sleep(POLL)
            continue
        rid = job.get("id")
        if not rid:
            time.sleep(POLL)
            continue
        print(f"[JOB] {rid} | {job.get('filename')}")
        try:
            process(job)
        except Exception as e:
            traceback.print_exc()
            fail(rid, str(e))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nWorker ukončen.")
