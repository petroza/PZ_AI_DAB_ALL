"""
PZ AI DAB ALL — relay worker (PC).

Připojí se k webu (PHP relay na Forpsi), vyzvedne čekající zakázku, stáhne
video, nadabuje ho LOKÁLNĚ stávající pipeline (app/pipeline.py) a nahraje
výsledek zpět. Díky tomu jde dabovat z mobilu odkudkoliv — web mluví jen
s Forpsi, tento worker si úkoly vyzvedává sám.

Spouštěj přes tools\start\START_DABWORKER.bat (aktivuje .venv). Nastavení ve worker_config.json.
Pro XTTS hlas musí běžet i tools\start\START_XTTS.bat.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests

# Windows konzole (cp1250) jinak spadne na unicode v print() (např. „→", „ů").
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Zajisti import balíku `app` i pod embeddable Pythonem (runtime), který
# adresář skriptu do sys.path automaticky nepřidává.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config as appcfg, pipeline
from app.job_manager import JobManager

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "worker_config.json").read_text(encoding="utf-8"))
BASE = (os.environ.get("DAB_RELAY_BASE") or CFG["base_url"]).rstrip("/") + "/"
API = BASE + "worker_api.php"
TOKEN = os.environ.get("DAB_RELAY_TOKEN") or CFG["worker_token"]
POLL = float(CFG.get("poll_interval_sec", 5))
HEAD = {"X-Worker-Token": TOKEN}

# DeepL API klíč (volitelný) z worker_config.json → env, ať ho vidí překladač.
if CFG.get("deepl_key") and not os.environ.get("DEEPL_API_KEY"):
    os.environ["DEEPL_API_KEY"] = str(CFG["deepl_key"])

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


def download_output(rid, kind, dest: Path):
    """Stáhne JIŽ hotový výstup (video/srt) z relay – pro dodatečné zapečení titulků."""
    with requests.get(API, params={"action": "worker_output", "id": rid, "kind": kind},
                      headers=HEAD, stream=True, timeout=900) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for c in r.iter_content(1 << 16):
                if c:
                    f.write(c)
    if dest.stat().st_size == 0:
        raise RuntimeError(f"stažený výstup ({kind}) je prázdný")


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


def post_draft(rid, segments, duration, text, src_srt):
    """Fáze 1 → nahraj návrh segmentů (k editaci) na relay; job přejde do review."""
    import io
    seg_blob = json.dumps({"segments": segments}, ensure_ascii=False).encode("utf-8")
    fhs = []
    try:
        files = {"segments": ("segments.json", io.BytesIO(seg_blob))}
        if src_srt and Path(src_srt).is_file():
            fh = open(src_srt, "rb"); fhs.append(fh)
            files["src_srt"] = (f"{rid}.src.srt", fh)
        r = requests.post(API, params={"action": "worker_draft"}, headers=HEAD,
                          data={"id": rid, "duration": duration,
                                "text_preview": (text or "")[:8000]},
                          files=files, timeout=300)
        r.raise_for_status()
    finally:
        for fh in fhs:
            try: fh.close()
            except Exception: pass


def fetch_segments(rid):
    """Fáze 2 → stáhni uživatelem upravené segmenty z relay."""
    r = requests.get(API, params={"action": "worker_segments", "id": rid},
                     headers=HEAD, timeout=60)
    r.raise_for_status()
    return (r.json() or {}).get("segments") or []


def _make_local_job(job, src):
    """Lokální job se stejným nastavením jako relay job."""
    is_video = bool(job.get("is_video", True))
    lj = jobs.create(job.get("filename") or f"{job['id']}.{job.get('ext','mp4')}",
                     str(src), is_video=is_video)
    now = datetime.now().isoformat(timespec="seconds")
    jobs.try_queue(lj.id, **{
        "source_lang": job.get("source_lang") or "auto",
        "target_lang": job.get("target_lang") or "cs-CZ",
        "tts_engine": job.get("tts_engine") or "piper",
        "voice": (job.get("voice") or None),
        "audio_mode": job.get("audio_mode") or "replace",
        "subs_preset": job.get("subs_preset") or "classic",
        "translator": job.get("translator") or "local",
        "subs_chars": int(job.get("subs_chars") or 0),
        "subs_maxlines": int(job.get("subs_maxlines") or 2),
        "subs_size": str(job.get("subs_size") or ""),
        "burn_subs": bool(job.get("burn_subs")),
        "llm_correct": bool(job.get("llm_correct", True)),
        "error": None, "started_at": now, "finished_at": None,
    })
    return lj


def _forward_until_done(t, lj_id, rid):
    """Přeposílej průběh lokální pipeline do relay, dokud běží vlákno."""
    last = -1
    while t.is_alive():
        lo = jobs.get(lj_id)
        if lo and lo.progress - last >= 3:        # méně častý progress (rate-limit)
            last = lo.progress
            progress(rid, max(5, min(98, lo.progress)))
        time.sleep(6)
    t.join(timeout=5)


def _retranslate(job):
    """Re-překlad uložených segmentů jiným překladačem – bez ASR a bez videa."""
    from engines.asr import asr_engine
    rid = job["id"]
    tr = job.get("translator") or "local"
    target = job.get("target_lang") or "cs-CZ"
    src_lang = job.get("source_lang") or "auto"
    progress(rid, 10)
    segs = fetch_segments(rid)
    new, n = [], (len(segs) or 1)
    for i, s in enumerate(segs):
        src = (s.get("src") or s.get("text") or "").strip()
        txt = (asr_engine.translate_text(src, target, source=src_lang, translator=tr)
               if src else (s.get("text") or ""))
        new.append({"start": s.get("start"), "end": s.get("end"),
                    "text": txt, "src": s.get("src") or src})
        progress(rid, 10 + int((i + 1) / n * 80))
    text = " ".join(x["text"] for x in new if x["text"])[:8000]
    post_draft(rid, new, 0, text, None)
    print(f"[RETRANS] {rid} -> review ({tr}, {len(new)} segmentu)")


def _reburn(job):
    """Dodatečně zapéct titulky do JIŽ hotového videa – stáhne výstup + SRT z relay,
    zapeče titulky, nahraje zpět. Bez nového nahrávání/dabingu (uživatel zapomněl
    zaškrtnout „Zapéct titulky")."""
    rid = job["id"]
    appcfg.ensure_dirs()
    progress(rid, 10)
    vid = appcfg.UPLOADS_DIR / f"reburn_{rid}.mp4"
    srt = appcfg.OUTPUTS_DIR / f"reburn_{rid}.srt"
    out = appcfg.OUTPUTS_DIR / f"reburn_out_{rid}.mp4"
    try:
        download_output(rid, "video", vid)
        download_output(rid, "srt_tgt", srt)
        progress(rid, 40)
        pipeline.burn_existing_video(
            str(vid), str(srt), str(out),
            preset=job.get("subs_preset") or "classic",
            subs_chars=int(job.get("subs_chars") or 0),
            subs_maxlines=int(job.get("subs_maxlines") or 0),
            subs_size=str(job.get("subs_size") or ""))
        progress(rid, 90)
        if not out.is_file() or out.stat().st_size == 0:
            raise RuntimeError("zapékání nevytvořilo výstup")
        upload_result(rid, {}, {"video": (f"{rid}.mp4", str(out))})
        print(f"[REBURN] {rid} hotovo (titulky zapečeny do videa)")
    finally:
        for p in (vid, srt, out):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


def process(job):
    rid = job["id"]
    ext = (job.get("ext") or "mp4").lower()
    phase = job.get("phase") or "full"
    appcfg.ensure_dirs()

    if phase == "retranslate":           # jen přeložit znovu, bez videa/ASR
        _retranslate(job)
        return

    if phase == "burn":                  # jen dodatečně zapéct titulky do videa
        _reburn(job)
        return

    progress(rid, 5)
    src = appcfg.UPLOADS_DIR / f"relay_{rid}.{ext}"
    download_source(rid, src)
    lj = _make_local_job(job, src)

    def _cleanup_local():
        jobs.delete(lj.id)
        try: src.unlink(missing_ok=True)
        except Exception: pass

    try:
        # ---- FÁZE 1: jen příprava textu (ASR+překlad) → review ----
        if phase == "prepare":
            holder = {}
            t = threading.Thread(
                target=lambda: holder.__setitem__("r", pipeline.prepare_segments(jobs, lj.id)),
                daemon=True)
            t.start()
            _forward_until_done(t, lj.id, rid)
            res = holder.get("r")
            lo = jobs.get(lj.id)
            if not res:
                raise RuntimeError((lo.error if lo else None) or "příprava textu selhala")
            post_draft(rid, res["segments"], res.get("duration", 0),
                       res.get("text", ""), lo.output_srt_src if lo else None)
            print(f"[DRAFT] {rid} -> review ({len(res['segments'])} segmentu k uprave)")
            return

        # ---- FÁZE 2 / plný běh: TTS + mux ----
        segments = fetch_segments(rid) if phase == "dub" else None
        t = threading.Thread(target=pipeline.run_dub, args=(jobs, lj.id),
                             kwargs={"segments": segments}, daemon=True)
        t.start()
        _forward_until_done(t, lj.id, rid)

        lo = jobs.get(lj.id)
        if not lo or lo.status == "error":
            raise RuntimeError((lo.error if lo else "neznámá chyba") or "dabing selhal")

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
        print(f"[OK] {rid} ({job.get('filename')}) hotovo")
    finally:
        _cleanup_local()


def main():
    print("=" * 56)
    print(" PZ AI DAB ALL — relay worker")
    print(" relay:", BASE)
    print(" (dabuje z mobilu/webu; pro XTTS spusť i tools\start\START_XTTS.bat)")
    print("=" * 56)
    print("Čekám na zakázky (Ctrl+C ukončí)…")
    while True:
        try:
            job = claim()
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", 0)
            wait = 90 if code == 429 else max(POLL, 10)   # Forpsi rate-limit → delší pauza
            print(f"[poll] relay HTTP {code} – čekám {wait}s")
            time.sleep(wait)
            continue
        except Exception as e:
            print("[poll] relay nedostupný:", e)
            time.sleep(max(POLL, 10))
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
