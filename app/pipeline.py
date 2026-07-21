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


def _burn_preset_opts(preset: str, vid_w: int, vid_h: int) -> dict:
    """Styl zapečených titulků podle presetu a VÝŠKY videa (aby seděl na 16:9
    i 9:16). Velikost/okraj jsou podíl výšky → správně na libovolném rozlišení.

      classic    – 16:9/na šířku: klasické titulky dole, střídmé.
      reels      – 9:16 Reels/Stories: VELKÉ tučné v dolní třetině, silný okraj
                   (trendy styl Instagram/Facebook/TikTok), krátké řádky.
      reels_box  – jako reels, ale s poloprůhledným podkladovým boxem.
    """
    h = vid_h or 1080
    p = (preset or "classic").lower()
    if p in ("word", "word_by_word", "oneword"):
        # „Slovo po slově" (ElevenLabs One word after another) – velké JEDNO slovo
        size = max(30, round(h * 0.072))
        return {
            "font": "Arial", "size": size, "bold": True, "align": 2,
            "marginv": round(h * 0.42),                 # zhruba na střed
            "outline": max(3, round(size * 0.1)),
            "maxlines": 1, "chars": 999, "bg": "none",
            "mode": "word", "hicolor": "white",
        }
    if p in ("karaoke", "karaoke_yellow", "karaoke_green", "karaoke_box", "hype"):
        # Věta se zvýrazněním aktuálního slova (ElevenLabs Yellow highlight/Hype)
        size = max(22, round(h * 0.046))
        hic = "green" if p == "karaoke_green" else "yellow"
        return {
            "font": "Arial", "size": size, "bold": True, "align": 2,
            "marginv": round(h * 0.15),
            "outline": max(2, round(size * 0.12)),
            "maxlines": 2, "chars": 14,
            "bg": "box" if p in ("karaoke_box", "hype") else "none", "bgalpha": 35,
            "mode": "karaoke", "hicolor": hic,
        }
    if p in ("reels", "reels_box", "social", "9:16", "stories", "tiktok"):
        size = max(22, round(h * 0.046))
        return {
            "font": "Arial", "size": size, "bold": True, "align": 2,
            "marginv": round(h * 0.15),                 # dolní třetina (ne úplně dole)
            "outline": max(2, round(size * 0.12)),      # silný okraj pro čitelnost
            "maxlines": 2, "chars": 14,                 # 2-4 slova/řádek (úzké video)
            "bg": "box" if p == "reels_box" else "none", "bgalpha": 30,
        }
    # classic 16:9 / na šířku
    return {
        "font": "Arial", "size": max(16, round(h * 0.048)), "bold": False,
        "align": 2, "marginv": round(h * 0.055),
        "maxlines": 2, "chars": 42, "bg": "none",
    }


def _merge_segments(segs, min_slot: float = 2.8, max_slot: float = 9.0) -> list:
    """Sloučí krátké/navazující segmenty do delších bloků.

    Dabing do češtiny trpí přecpáním, když je slot malý (např. „8 000" v 0,8 s)
    nebo když je překlad delší než originál. Delší bloky dají TTS přirozený
    časový prostor a lepší prozodii (souvislá věta místo úlomků).
    """
    out: list = []
    for s in segs:
        st = float(s.get("start") or 0.0)
        en = float(s.get("end") or 0.0)
        txt = (s.get("text") or "").strip()
        if en <= st or not txt:
            continue
        if (out and (en - out[-1]["start"]) <= max_slot
                and (out[-1]["end"] - out[-1]["start"] < min_slot or (en - st) < min_slot)):
            out[-1]["end"] = en
            out[-1]["text"] = (out[-1]["text"] + " " + txt).strip()
        else:
            out.append({"start": st, "end": en, "text": txt})
    return out


# České typografické pravidlo: jednopísmenné předložky/spojky (k, s, v, z, o, u,
# a, i) a krátké předložky NESMÍ zůstat na konci řádku/titulku – patří ke slovu
# za nimi. Drží titulky profesionální (jako broadcast).
_CZ_NOBREAK = {
    "k", "s", "v", "z", "o", "u", "a", "i",          # jednopísmenné (striktní)
    "ke", "ve", "se", "ze", "ku", "ku",              # vokalizované
    "do", "na", "za", "po", "od", "ob", "pro", "při", # krátké předložky
    "nad", "pod", "bez", "přes", "ke", "že", "aby",
}


def _norm_w(w: str) -> str:
    return (w or "").strip(".,!?…:;\"'()[]„“”‚‘»«").lower()


def _carry_dangling(parts: list) -> list:
    """Krátkou předložku/spojku na konci kusu přesune na začátek dalšího kusu
    (žádné „…agenta pro" na konci titulku)."""
    for i in range(len(parts) - 1):
        ws = parts[i].split()
        if len(ws) >= 2 and _norm_w(ws[-1]) in _CZ_NOBREAK:
            parts[i] = " ".join(ws[:-1])
            parts[i + 1] = ws[-1] + " " + parts[i + 1]
    return parts


def _wrap_two_lines(text: str, max_line: int = 42) -> str:
    """Zalomí titulek na max 2 řádky (zlom co nejblíž půlce, na hranici slov).
    Nezalomí hned za jednopísmennou/krátkou předložkou (ta nesmí viset na konci
    řádku – České typografické pravidlo)."""
    if len(text) <= max_line:
        return text
    words = text.split()
    if len(words) < 2:
        return text
    target = len(text) / 2
    acc, best_i, best_d = 0, -1, 1e9          # nejlepší POVOLENÝ zlom
    fb_i, fb_d = 0, 1e9                        # záloha (kdyby vše bylo zakázané)
    for i in range(len(words) - 1):
        acc += len(words[i]) + 1
        d = abs(acc - target)
        if d < fb_d:
            fb_d, fb_i = d, i
        if _norm_w(words[i]) in _CZ_NOBREAK:  # za předložkou nelámat
            continue
        if d < best_d:
            best_d, best_i = d, i
    if best_i < 0:
        best_i = fb_i
    return " ".join(words[:best_i + 1]) + "\n" + " ".join(words[best_i + 1:])


def _subtitle_cues(segs, max_chars: int = 84, max_dur: float = 5.5,
                   min_dur: float = 1.0, wrap_chars: int = 42) -> list:
    """Rozdělí (sloučené) bloky na čitelné titulky (≤2 řádky) s proporčním časem.

    Po sloučení segmentů pro dabing jsou bloky dlouhé; titulky chtějí krátké
    cue. Text se sype do kusů do ~max_chars (≈ 2 řádky) a ≤ max_dur s, čas se
    dělí proporčně podle délky. Příliš krátký poslední kus se přilepí k
    předchozímu (žádná osamocená slova jako „dříve.").
    """
    cues = []
    for s in segs:
        text = (s.get("text") or "").strip()
        start = float(s.get("start") or 0.0)
        end = float(s.get("end") or 0.0)
        if not text or end <= start:
            continue
        block_dur = end - start
        cps = max(1.0, len(text)) / block_dur          # znaků za sekundu v bloku
        words, parts, cur = text.split(), [], ""
        for w in words:
            cand = (cur + " " + w) if cur else w
            if cur and (len(cand) > max_chars or len(cand) / cps > max_dur):
                parts.append(cur)
                cur = w
            else:
                cur = cand
            # zlom na konci věty, ať cue nepřekrývá dvě věty
            if cur and cur[-1] in ".!?…" and len(cur) >= 28:
                parts.append(cur)
                cur = ""
        if cur:
            parts.append(cur)
        parts = _carry_dangling(parts)         # předložka nesmí viset na konci cue
        total = sum(len(p) for p in parts) or 1
        local, t = [], start
        for i, p in enumerate(parts):
            dur = block_dur * (len(p) / total)
            ce = end if i == len(parts) - 1 else t + dur
            local.append([round(t, 3), round(ce, 3), p])
            t = ce
        # přilep moc krátký poslední kus k předchozímu
        if len(local) >= 2 and (local[-1][1] - local[-1][0]) < min_dur:
            local[-2][1] = local[-1][1]
            local[-2][2] = (local[-2][2] + " " + local[-1][2]).strip()
            local.pop()
        for st_, en_, tx in local:
            cues.append({"start": st_, "end": en_, "text": _wrap_two_lines(tx, wrap_chars)})
    return cues


def _burn_into(src_video, out_segs, dst_video, work, preset,
               u_chars, u_lines, u_size, log=None, progress_cb=None,
               preserve_audio=False):
    """Zapéct titulky (z out_segs) do src_video → dst_video. Jediné místo s burn
    logikou (volá ho run_dub i dodatečné zapečení burn_existing_video)."""
    vw, vh = ffmpeg_tools.get_video_size(src_video, log)
    bopts = _burn_preset_opts(preset, vw, vh)
    bopts["audio_copy"] = bool(preserve_audio)
    if u_lines in (1, 2):
        bopts["maxlines"] = u_lines
    _SZ = {"small": 0.035, "medium": 0.046, "large": 0.062, "xl": 0.08}
    if u_size in _SZ and vh:
        bopts["size"] = max(12, round(vh * _SZ[u_size]))
    if vw and vw > 0:
        fit = max(8, int(vw * 0.92 / (bopts["size"] * 0.62)))
        bopts["chars"] = min(u_chars, fit) if u_chars > 0 else min(bopts.get("chars", 42), fit)
    elif u_chars > 0:
        bopts["chars"] = u_chars
    if bopts.get("maxlines", 2) == 1:
        cue_max = max(8, bopts["chars"]); wrapc = 9999
    else:
        cue_max = max(14, bopts["chars"] * 2 - 4); wrapc = bopts["chars"]
    short = _subtitle_cues(out_segs, max_chars=cue_max, max_dur=4.0, wrap_chars=wrapc)
    burn_srt = Path(work) / "burn.srt"
    exporters.write_srt({"segments": short or out_segs}, burn_srt)
    mode = bopts.get("mode", "normal")
    if mode in ("karaoke", "word"):
        bopts["segments"] = [
            {"start": c["start"], "end": c["end"],
             "text": (c.get("text") or "").replace("\n", " ")}
            for c in (short or out_segs)]
    if log:
        log(f"Zapékání titulků: preset={preset} (mode={mode}, video {vw}×{vh}, "
            f"font {bopts['size']}, {bopts['chars']} zn./řádek)")
    ffmpeg_tools.burn_subtitles(src_video, str(burn_srt), dst_video,
                                opts=bopts, log=log, progress_cb=progress_cb)


def _parse_srt(path) -> list:
    """SRT → [{start,end,text}] (sekundy). Pro dodatečné zapečení titulků."""
    import re as _re
    txt = Path(path).read_text(encoding="utf-8", errors="replace")

    def _ts(s):
        s = s.strip().replace(",", ".")
        h, m, rest = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    segs = []
    for b in _re.split(r"\r?\n\s*\r?\n", txt.strip()):
        lines = b.splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, _, c = lines[1].partition("-->")
            try:
                segs.append({"start": _ts(a), "end": _ts(c),
                             "text": " ".join(l.strip() for l in lines[2:] if l.strip())})
            except Exception:
                continue
    return segs


def burn_existing_video(video_path, srt_path, out_path, preset="classic",
                        subs_chars=0, subs_maxlines=0, subs_size="", log=None):
    """Dodatečné zapečení titulků do JIŽ hotového (např. dabovaného) videa –
    bez nového nahrávání/dabingu. Segmenty se vezmou z existujícího SRT."""
    out_segs = _parse_srt(srt_path)
    if not out_segs:
        raise RuntimeError("V titulkovém SRT nejsou žádné titulky.")
    work = Path(out_path).parent
    tmp = work / ("_reburn_" + Path(out_path).name)
    _burn_into(Path(video_path), out_segs, tmp, work, preset,
               int(subs_chars or 0), int(subs_maxlines or 0), str(subs_size or ""), log=log)
    if not tmp.is_file() or tmp.stat().st_size == 0:
        raise RuntimeError("Zapékání selhalo (prázdný výstup).")
    Path(tmp).replace(out_path)
    return out_path


def _prepare(jobs, job_id, work, upload, target, log, prog):
    """Fáze 1: zvuk → ASR → překlad. Vrátí (out_segs, dur, is_video, src_srt).

    Každý segment je dict {start, end, text (přeložený), src (originál)}.
    TTS se NEspouští – výstup jde buď rovnou do fáze 2, nebo k editaci uživatelem.
    """
    job = jobs.get(job_id)
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
    # Slouč krátké segmenty → přirozené časové bloky (řeší přecpání).
    chunks = _merge_segments(segs)
    if len(chunks) != len(segs):
        log(f"Sloučeno {len(segs)} segmentů → {len(chunks)} bloků pro plynulejší dabing")
    # Zdroj i cíl ve stejném jazyce (např. cs→cs) → NEpřekládat. „Překlad"
    # by jen parafrázoval přepis a zanášel chyby (např. „má v sobě“ →
    # „má ve svém náboji“). Použije se přesný přepis.
    same_lang = (effective_src != "auto"
                 and effective_src.split("-")[0] == target.split("-")[0])
    if same_lang:
        log(f"Zdroj i cíl = {target.split('-')[0]} → přeskakuji překlad (jen přepis)")
    n = len(chunks) or 1
    batch_translations = None
    if not same_lang:
        batch_translations = asr_engine.translate_segments(
            chunks, target, log, source=effective_src,
            translator=getattr(job, "translator", "local"))
    out_segs = []
    for i, s in enumerate(chunks):
        txt = (s.get("text") or "").strip()
        start = float(s.get("start") or 0.0)
        end   = float(s.get("end")   or 0.0)
        if start >= end:
            log(f"Přeskakuji segment {i} s neplatným časováním ({start}–{end})")
            continue
        # Dabingový rozpočet: kolik znaků se dá vyslovit za délku slotu
        # (~14 zn./s je přirozené české tempo s mírným zrychlením). Drží
        # překlad dost krátký, aby se řeč nemusela drtit časem.
        budget = int((end - start) * 14) if (end - start) > 0 else 0
        if batch_translations is not None:
            tr = batch_translations[i]
        elif txt and not same_lang:
            tr = asr_engine.translate_text(txt, target, log, source=effective_src,
                                           max_chars=budget,
                                           translator=getattr(job, "translator", "local"))
        else:
            tr = txt
        # POZN.: NEpouštět tu asr_engine.correct_text() na překlad! Je to korektor
        # ASR přepisu (fonetické cizí názvy porše→Porsche) a na hotovém ČESKÉM
        # překladu misfiruje – anglicizuje termíny („orchestrační“→„orchestration“,
        # „GTC“→„GTc“). Přepis se opravuje už ve _postprocess (transcribe_file).
        out_segs.append({"start": start, "end": end, "text": tr, "src": txt})
        prog("translating", 40 + (i + 1) / n * 16)
    return out_segs, dur, is_video, src_srt


def _write_target_outputs(jobs, job_id, job, target, out_segs):
    """Zapíše cílové titulky/JSON z (případně upravených) segmentů."""
    sub_cues = _subtitle_cues(out_segs)
    tgt_result = {
        "text": " ".join(x["text"] for x in out_segs if x["text"]).strip(),
        "segments": sub_cues or out_segs,
    }
    tgt_srt = config.OUTPUTS_DIR / f"{job_id}.{target}.srt"
    exporters.write_srt(tgt_result, tgt_srt)
    out_json = config.OUTPUTS_DIR / f"{job_id}.json"
    exporters.write_json(tgt_result, out_json,
                         {"job_id": job_id, "source_lang": job.source_lang,
                          "target_lang": target, "filename": job.filename})
    jobs.update(job_id, text_preview=tgt_result["text"][:8000],
                output_srt_tgt=str(tgt_srt), output_json=str(out_json))
    return tgt_srt


def prepare_segments(jobs, job_id: str):
    """Fáze 1 pro režim „upravit text před dabingem".

    Vrátí list segmentů {start, end, text, src} k editaci (nebo None při chybě).
    Worker je nahraje na relay a job přejde do stavu „review".
    """
    job = jobs.get(job_id)
    if not job:
        return None

    def log(msg):
        jobs.append_log(job, msg)

    def prog(status, pct):
        jobs.set_status(job_id, status, int(pct))

    work = config.WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    upload = Path(job.upload_path)
    target = job.target_lang
    try:
        jobs.update(job_id, started_at=datetime.now().isoformat(timespec="seconds"))
        out_segs, dur, is_video, src_srt = _prepare(
            jobs, job_id, work, upload, target, log, prog)
        jobs.update(job_id, output_srt_src=str(src_srt))
        prog("review", 50)
        log(f"Příprava textu hotová: {len(out_segs)} segmentů k úpravě.")
        return {"segments": out_segs, "duration": dur, "src_srt": str(src_srt),
                "text": " ".join(x["text"] for x in out_segs if x["text"])[:8000]}
    except Exception as e:
        traceback.print_exc()
        log(f"CHYBA: {e}")
        jobs.update(job_id, error=str(e))
        jobs.set_status(job_id, "error")
        return None
    finally:
        # zdroj se pro fázi 2 stáhne znovu z relay → pracovní adresář ukliď
        _cleanup(work)


def run_dub(jobs, job_id: str, segments=None) -> None:
    """Plný dabing. Když ``segments`` je None, udělá i přípravu (ASR+překlad);
    jinak použije dodané (uživatelem upravené) segmenty a přeskočí přepis."""
    job = jobs.get(job_id)
    if not job:
        return

    def log(msg: str) -> None:
        jobs.append_log(job, msg)

    def prog(status, pct):
        jobs.set_status(job_id, status, int(pct))

    work = config.WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    upload = Path(job.upload_path)
    target = job.target_lang
    wav16 = work / "asr16k.wav"

    try:
        jobs.update(job_id, started_at=datetime.now().isoformat(timespec="seconds"))

        if segments is None:
            # plný běh – fáze 1 i 2 v jednom
            out_segs, dur, is_video, src_srt = _prepare(
                jobs, job_id, work, upload, target, log, prog)
            jobs.update(job_id, output_srt_src=str(src_srt))
        else:
            # fáze 2 – použij upravené segmenty, přepis/překlad přeskoč
            prog("extracting_audio", 30)
            ffmpeg_tools.convert_to_wav(upload, wav16, log=log)
            dur = ffmpeg_tools.get_audio_duration(wav16, log=log)
            jobs.update(job_id, duration=dur)
            is_video = job.is_video and ff.has_video(upload)
            out_segs = []
            for s in segments:
                txt = (s.get("text") or "").strip()
                st = float(s.get("start") or 0.0)
                en = float(s.get("end") or 0.0)
                if txt and en > st:
                    out_segs.append({"start": st, "end": en, "text": txt})
            log(f"Použity upravené titulky: {len(out_segs)} segmentů")

        _write_target_outputs(jobs, job_id, job, target, out_segs)

        # Režim pouze titulky: původní zvuk zůstane zachovaný, TTS a mix se vůbec
        # nespouštějí. Český překlad se zapeče přímo do původního videa.
        if job.audio_mode == "subtitles":
            if not is_video:
                raise RuntimeError("Režim pouze titulky vyžaduje video, ne samostatný zvuk.")
            prog("burning", 82)
            out_video = config.OUTPUTS_DIR / f"{job_id}.subtitled.mp4"
            _burn_into(upload, out_segs, out_video, work,
                       getattr(job, "subs_preset", "classic"),
                       int(getattr(job, "subs_chars", 0) or 0),
                       int(getattr(job, "subs_maxlines", 0) or 0),
                       str(getattr(job, "subs_size", "") or ""),
                       log=log,
                       progress_cb=lambda pct: prog("burning", 82 + int(pct * 0.17)),
                       preserve_audio=True)
            if not out_video.is_file() or out_video.stat().st_size == 0:
                raise RuntimeError("Vytvoření videa s titulky selhalo.")
            jobs.update(job_id, output_video=str(out_video), burn_subs=True)
            prog("done", 100)
            log("HOTOVO: původní zvuk zachován, přidány české titulky.")
            _cleanup(work)
            return

        # 4) TTS — z každého přeloženého segmentu jeden klip
        prog("synthesizing", 58)
        backend = get_backend(job.tts_engine)
        ready, info = backend.is_ready()
        if not ready:
            raise TTSNotReady(info)
        log(f"TTS backend: {backend.name} ({info})")
        # Klonovací enginy (XTTS) potřebují referenční hlas. Když uživatel
        # nezadal vlastní, vezmi vzorek z PŮVODNÍHO zvuku → dabing zní jako
        # stejný mluvčí.
        tts_voice = job.voice
        # Klonuj původního mluvčího JEN když uživatel nezadal konkrétní hlas.
        # Když zvolil vestavěný hlas (jméno), předá se beze změny.
        if getattr(backend, "needs_reference", False) and not tts_voice:
            # Krátká, čistá 24kHz reference. Celá stopa obsahuje pauzy, ruchy a
            # proměnlivou hlasitost, což zhoršuje českou artikulaci klonu.
            ref = work / "voice_ref.wav"
            try:
                # Střední část bývá stabilnější než úvod videa (méně nádechů,
                # náběhů hudby a změn hlasitosti). Testy klonu ukázaly nejlepší
                # českou artikulaci na souvislém osmivteřinovém vzorku.
                ref_start = min(8.0, max(0.4, dur * 0.20))
                ref_len = min(8.0, max(6.0, dur - ref_start - 0.2))
                ff.run(["-ss", f"{ref_start:.2f}", "-t", f"{ref_len:.2f}",
                        "-i", str(upload), "-vn",
                        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                        str(ref)], log=log)
                tts_voice = str(ref)
            except Exception:
                tts_voice = str(wav16)
            log(f"Klonování hlasu z čisté reference {Path(tts_voice).name} "
                f"({ref_len:.1f} s, 24 kHz)")
        elif tts_voice:
            log(f"Hlas: {tts_voice}")
        clips = []
        n_tts = len(out_segs) or 1
        for i, s in enumerate(out_segs):
            text = (s["text"] or "").strip()
            c = VoiceClip(index=i, start=float(s["start"] or 0.0),
                          end=float(s["end"] or 0.0), text=text)
            if text:
                raw = work / f"tts_{i:05d}.wav"
                backend.synth(text, raw, voice=tts_voice, lang=target, log=log)
                c.natural_dur = ff.duration(raw)
                # Enginy s nativní rychlostí (XTTS): když je klip delší než slot,
                # přemluv ho RYCHLEJI nativně — zachová kvalitu, na rozdíl od
                # ffmpeg atempo, které při ~1.5× rozbije řeč na kaši.
                if (getattr(backend, "supports_speed", False)
                        and c.slot > 0.3 and c.natural_dur > 0):
                    ratio = c.natural_dur / c.slot
                    if ratio > 1.05:
                        # XTTS udrží přirozenost jen do mírného zrychlení;
                        # zbytek dožene rubberband (kvalitní stretch).
                        spd = min(ratio, 1.3)
                        log(f"  segment {i}: klip {ratio:.2f}× delší než slot "
                            f"→ přemluvím nativní rychlostí {spd:.2f}× (zbytek rubberband)")
                        backend.synth(text, raw, voice=tts_voice, lang=target,
                                      speed=spd, log=log)
                        c.natural_dur = ff.duration(raw)
                c.raw_wav = str(raw)
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
            if not out_video.is_file() or out_video.stat().st_size == 0:
                raise RuntimeError(
                    f"Mux selhal: výstupní video chybí nebo je prázdné ({out_video.name})")
            # Zaregistruj hned, aby delete() uklidil i při selhání burn.
            jobs.update(job_id, output_video=str(out_video))
            if job.burn_subs:
                prog("burning", 96)
                burned = work / "burned.mp4"
                _burn_into(out_video, out_segs, burned, work,
                           getattr(job, "subs_preset", "classic"),
                           int(getattr(job, "subs_chars", 0) or 0),
                           int(getattr(job, "subs_maxlines", 0) or 0),
                           str(getattr(job, "subs_size", "") or ""),
                           log=log,
                           progress_cb=lambda pct: prog("burning", 96 + int(pct * 0.03)))
                out_video.unlink(missing_ok=True)
                Path(burned).replace(out_video)
                if not out_video.is_file() or out_video.stat().st_size == 0:
                    raise RuntimeError(
                        f"Burn-in selhal: výstupní video chybí nebo je prázdné ({out_video.name})")
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
