"""
Dabingová pipeline — orchestrátor celého řetězu.

  video → [ffmpeg] WAV 16k → [parakeet] segmenty s časy → [LLM] překlad
        → [Piper/Studio] TTS klipy → [rubberband/atempo] zarovnání
        → [mixer] zvuková stopa → [ffmpeg] mux do videa (+ volitelně burn-in)

Znovupoužívá ASR/překlad z engines.asr, hlas z engines.tts a jádro z dub/.
Běží v samostatném vlákně; stav a progres hlásí přes JobManager.
"""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

from dub import _ffmpeg as ff
from dub import align, mixer, mux
from engines.asr import asr_engine, exporters, ffmpeg_tools
from engines.tts import TTSNotReady, get_backend
from engines.tts.base import VoiceClip

from . import config

_BURN_OPTS = {"font": "Arial", "size": 22, "align": 2, "marginv": 40,
              "chars": 0, "maxlines": 2, "bg": "none"}


def run_dub(jobs, job_id: str) -> None:
    job = jobs.get(job_id)
    if not job:
        return

    def log(msg: str) -> None:
        jobs.append_log(jobs.get(job_id), msg)

    def prog(status, pct):
        jobs.set_status(job_id, status, int(pct))

    work = config.WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    upload = Path(job.upload_path)
    target = job.target_lang

    jobs.update(job_id, started_at=datetime.now().isoformat(timespec="seconds"))

    try:
        is_video = job.is_video and ff.has_video(upload)

        # 1) extrakce zvuku pro ASR (16 kHz mono)
        prog("extracting_audio", 8)
        wav16 = work / "asr16k.wav"
        ffmpeg_tools.convert_to_wav(upload, wav16, log=log)
        dur = ffmpeg_tools.get_audio_duration(wav16, log=log)
        jobs.update(job_id, duration=dur)

        # 2) ASR — přepis se slovními časy (+ volitelná LLM korekce)
        prog("transcribing", 22)
        result = asr_engine.transcribe_file(
            str(wav16), job.source_lang, job_id, duration=dur, log=log,
            llm_correct=job.llm_correct)
        segs = result.get("segments", [])
        jobs.update(job_id, segments_count=len(segs))
        src_srt = config.OUTPUTS_DIR / f"{job_id}.src.srt"
        exporters.write_srt(result, src_srt)
        log(f"ASR: {len(segs)} segmentů, {dur:.1f}s")
        if not segs:
            log("Varování: ASR nenašel žádné mluvené segmenty (ticho nebo nerozpoznaná řeč).")

        # 3) překlad segment po segmentu (zachová časování)
        prog("translating", 40)
        # Když zdrojový jazyk je "auto", zkus použít detekovaný jazyk z ASR
        # (Whisper ho vrátí; parakeet auto-detekuje bez explicitního kódu).
        # Argostranslate potřebuje konkrétní kód — bez něj vrátí původní text.
        _WHISPER_LOCALE = {
            "cs": "cs-CZ", "en": "en-US", "uk": "uk-UA", "ru": "ru-RU",
            "de": "de-DE", "pl": "pl-PL", "sk": "sk-SK", "es": "es-ES",
            "fr": "fr-FR", "it": "it-IT",
        }
        effective_src = job.source_lang
        if effective_src == "auto":
            det = result.get("detected_language")
            if det:
                effective_src = _WHISPER_LOCALE.get(det, "auto")
                if effective_src != "auto":
                    log(f"Detekovaný jazyk: {det} → použiji {effective_src} pro překlad")
        n = len(segs) or 1
        out_segs = []
        for i, s in enumerate(segs):
            txt = (s.get("text") or "").strip()
            start = float(s.get("start") or 0.0)
            end   = float(s.get("end")   or 0.0)
            if start >= end:
                log(f"Přeskakuji segment {i} s neplatným časováním ({start}–{end})")
                continue
            tr = asr_engine.llm_translate(txt, target, log,
                                           source=effective_src) if txt else txt
            if tr and job.llm_correct:
                tr = asr_engine.correct_text(tr, target)
            out_segs.append({"start": start, "end": end, "text": tr})
            prog("translating", 40 + (i + 1) / n * 16)
        tgt_result = {
            "text": " ".join(x["text"] for x in out_segs if x["text"]).strip(),
            "segments": out_segs,
        }
        tgt_srt = config.OUTPUTS_DIR / f"{job_id}.{target}.srt"
        exporters.write_srt(tgt_result, tgt_srt)
        out_json = config.OUTPUTS_DIR / f"{job_id}.json"
        exporters.write_json(tgt_result, out_json,
                             {"job_id": job_id, "source_lang": job.source_lang,
                              "target_lang": target, "filename": job.filename})
        jobs.update(job_id, text_preview=tgt_result["text"][:8000],
                    output_srt_src=str(src_srt), output_srt_tgt=str(tgt_srt),
                    output_json=str(out_json))

        # 4) TTS — z každého přeloženého segmentu jeden klip
        prog("synthesizing", 58)
        backend = get_backend(job.tts_engine)
        ready, info = backend.is_ready()
        if not ready:
            raise TTSNotReady(info)
        log(f"TTS backend: {backend.name} ({info})")
        clips = []
        n_tts = len(out_segs) or 1
        for i, s in enumerate(out_segs):
            text = (s["text"] or "").strip()
            c = VoiceClip(index=i, start=float(s["start"] or 0.0),
                          end=float(s["end"] or 0.0), text=text)
            if text:
                raw = work / f"tts_{i:05d}.wav"
                backend.synth(text, raw, voice=job.voice, lang=target, log=log)
                c.raw_wav = str(raw)
                c.natural_dur = ff.duration(raw)
            clips.append(c)
            prog("synthesizing", 58 + (i + 1) / n_tts * 18)

        # 5) zarovnání klipů na délku slotů
        prog("aligning", 80)
        for c in clips:
            if c.raw_wav:
                fitted = work / f"fit_{c.index:05d}.wav"
                c.fitted_dur = align.fit_clip(c.raw_wav, fitted, c.slot, log=log)
                c.fitted_wav = str(fitted)

        # 6) sestavení souvislé zvukové stopy (+ volitelný ducking)
        prog("mixing", 88)
        final_audio = work / "dub_track.wav"
        original_wav = None
        if job.audio_mode == "voiceover":
            original_wav = work / "orig48k.wav"
            ff.extract_audio(upload, original_wav, config.MIX_RATE, log=log)
        mixer.build_track(clips, dur, final_audio, work,
                          mode=job.audio_mode,
                          original_wav=str(original_wav) if original_wav else None,
                          log=log)

        # 7) mux do videa, nebo export audio (audio-only vstup)
        prog("muxing", 93)
        if is_video:
            out_video = config.OUTPUTS_DIR / f"{job_id}.dubbed.mp4"
            mux.mux_video(upload, final_audio, out_video, log=log)
            if job.burn_subs:
                prog("burning", 96)
                burned = work / "burned.mp4"
                ffmpeg_tools.burn_subtitles(
                    out_video, tgt_srt, burned, opts=_BURN_OPTS, log=log,
                    progress_cb=lambda pct: prog("burning", 96 + int(pct * 0.03)))
                out_video.unlink(missing_ok=True)
                Path(burned).replace(out_video)
            if not out_video.is_file() or out_video.stat().st_size == 0:
                raise RuntimeError(
                    f"Mux selhal: výstupní video chybí nebo je prázdné ({out_video.name})")
            jobs.update(job_id, output_video=str(out_video))
        else:
            out_audio = config.OUTPUTS_DIR / f"{job_id}.dubbed.mp3"
            mux.export_audio(final_audio, out_audio, log=log)
            if not out_audio.is_file() or out_audio.stat().st_size == 0:
                raise RuntimeError(
                    f"Export audia selhal: výstupní soubor chybí nebo je prázdný ({out_audio.name})")
            jobs.update(job_id, output_audio=str(out_audio))

        prog("done", 100)
        log("HOTOVO.")
        _cleanup(work)
    except Exception as e:                    # UI nesmí spadnout
        traceback.print_exc()
        log(f"CHYBA: {e}")
        jobs.update(job_id, error=str(e))
        jobs.set_status(job_id, "error")
        _cleanup(work)


def _cleanup(work: Path) -> None:
    try:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    except Exception:
        pass
