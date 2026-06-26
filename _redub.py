import shutil, time, threading
from datetime import datetime
from pathlib import Path
from app.job_manager import JobManager
from app import config, pipeline

SRC = r"C:\Users\Petr\Downloads\1782454651411_original-62ca7f13-6121-400d-8282-b80ce41feea1.mp4"
PRESET = "classic"   # jako tvůj mobilní job; pro 9:16 je hezčí "reels"

jobs = JobManager()
config.ensure_dirs()
up = config.UPLOADS_DIR / "redub_fix.mp4"
shutil.copy(SRC, up)
job = jobs.create("opraveno.mp4", str(up), is_video=True)
jobs.try_queue(job.id, source_lang="auto", target_lang="cs-CZ", tts_engine="piper",
               audio_mode="replace", subs_preset=PRESET, burn_subs=True,
               llm_correct=False, started_at=datetime.now().isoformat(timespec="seconds"),
               error=None, finished_at=None)
t = threading.Thread(target=pipeline.run_dub, args=(jobs, job.id), daemon=True)
t.start()
last = -1
while t.is_alive():
    j = jobs.get(job.id)
    if j and j.progress != last:
        last = j.progress
        print(f"  {j.status} {j.progress}%", flush=True)
    time.sleep(3)
t.join(timeout=5)
j = jobs.get(job.id)
print("STATUS:", j.status, "| error:", j.error)
print("OUTPUT:", j.output_video)
if j.status == "done" and j.output_video:
    dst = r"C:\Users\Petr\Downloads\DABING_mobil_OPRAVENO.mp4"
    shutil.copy(j.output_video, dst)
    print("DELIVERED:", dst)
jobs.delete(job.id)
