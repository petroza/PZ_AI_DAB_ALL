"""
Dabingová pipeline — orchestrátor celého řetězu.

  video → [ffmpeg] WAV 16k → [parakeet] segmenty s časy → [LLM] překlad
        → [Piper/Studio] TTS klipy → [rubberband/atempo] zarovnání
        → [mixer] zvuková stopa → [ffmpeg] mux do videa (+ volitelně burn-in)

Znovupoužívá ASR/překlad z engines.asr, hlas z engines.tts a jádro z dub/.
Běží v samostatném vlákně; stav a progres hlásí přes JobManager.
"""
from __future__ import annotations

import re
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
            # 0 = odvodit z reálné šířky videa a velikosti písma (viz _burn_into).
            # Natvrdo daných 14 zn. bylo pod skutečnou kapacitou řádku, takže se
            # věty trhaly na útržky („Protivzdušná / obrana“).
            "maxlines": 2, "chars": 0,
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


def _norm_word(w: str) -> str:
    """Slovo bez interpunkce a velkých písmen — pro porovnání ASR × předloha."""
    return re.sub(r"[^0-9a-záčďéěíňóřšťúůýž]", "", (w or "").lower())


def _trim_tts_tail(wav, text: str, work, log=None) -> list:
    """Ořízne balast, který XTTS přilepí za konec repliky.

    XTTS běžně nechá za poslední větou dozvuk, nádech, nebo dokonce halucinuje
    slovo navíc (měřeno: „Přeji všem hezký pondělí.“ = 5,15 s klipu, ale řeč
    končí ve 2,24 s — 57 % je balast, na konci vymyšlené „kraj“). Ten balast se
    počítá do délky klipu, takže se replika „nevejde“ do slotu a time-stretch ji
    zbytečně zdrtí. Ořezáním se spousta segmentů vejde bez jakéhokoli zrychlení.

    Konec řeči se hledá ASR (slovní časy) a řeže se za posledním slovem, které
    opravdu patří do předlohy — halucinované slovo na konci tak padne taky.

    Vrací slova s časy uvnitř klipu (``[{"w", "start", "end"}]``) — používají se
    pak k navěšení titulků na skutečnou řeč. Prázdný seznam = ASR nic nedalo.
    Při jakékoli nejistotě nechá klip být (a slova stejně vrátí).
    """
    wav = Path(wav)
    try:
        dur = ff.duration(wav)
        if dur <= 0.6:
            return []
        w16 = Path(work) / (wav.stem + ".trimchk.wav")
        ffmpeg_tools.convert_to_wav(wav, w16)
        res = asr_engine.transcribe_file(str(w16), "cs-CZ", "trim", duration=dur,
                                         llm_correct=False)
        words = []
        for s in res.get("segments") or []:
            words += s.get("tokens") or []
        words = [w for w in words if _norm_word(w.get("w"))]
        if not words:
            return []
        # Poslední slovo předlohy najdi mezi rozpoznanými (od konce). Když ho ASR
        # netrefí (přepis TTS není dokonalý), spokoj se s posledním slovem, které
        # se v předloze vůbec vyskytuje — halucinace typicky v předloze není.
        want = [_norm_word(w) for w in text.split() if _norm_word(w)]
        if not want:
            return words
        cut_at = None
        for w in reversed(words):
            nw = _norm_word(w.get("w"))
            if nw == want[-1] or (len(nw) > 3 and nw in want):
                cut_at = float(w.get("end") or 0.0)
                break
        if cut_at is None:
            return words
        cut_at += 0.18                      # nechat doznít poslední hlásku
        # Nikdy neřež do řeči: příliš agresivní ořez znamená, že ASR spolehlivě
        # nerozpoznalo konec — pak je bezpečnější klip nechat, jak je.
        if cut_at >= dur - 0.12 or cut_at < 0.35 * dur:
            return words
        trimmed = wav.with_suffix(".trim.wav")
        ff.run(["-i", str(wav), "-t", f"{cut_at:.3f}", "-c", "copy", str(trimmed)], log=None)
        if ff.duration(trimmed) <= 0.2:
            return words
        trimmed.replace(wav)
        if log:
            log(f"    ořezán balast za koncem řeči: {dur:.2f}s → {cut_at:.2f}s")
        # Slova, která do ořezaného klipu spadají — s koncem zastřiženým na délku
        # klipu, jinak by titulek „přesahoval“ za konec repliky.
        kept = []
        for w in words:
            st = float(w.get("start") or 0.0)
            if st >= cut_at:
                continue
            kept.append({"w": w.get("w"), "start": st,
                         "end": min(float(w.get("end") or st), cut_at)})
        return kept
    except Exception as e:
        if log:
            log(f"    ořez konce klipu přeskočen ({e})")
        return []


def _resync_to_dub(out_segs, clips, log=None) -> list:
    """Přepíše časy titulků podle toho, kdy dabing SKUTEČNĚ zní.

    Časy segmentů pocházejí z přepisu ORIGINÁLU. Český dabing ale mluví jinak
    dlouho: klip se zkrátil ořezem, protáhl se do pauzy, nebo ho posunul mixer
    (skládá klipy za sebe, takže delší replika odsune tu další). Titulek pak
    nesedí na hlas. Tady se časy přepočítají na skutečnou stopu — stejnou
    logikou, jakou stopu skládá ``mixer.build_track``.

    Když jsou k dispozici slovní časy z ASR klipu, použijí se i uvnitř repliky,
    takže titulek naskočí přesně na první slovo a zmizí s posledním.
    """
    if not clips:
        return out_segs
    usable = sorted([c for c in clips if (c.fitted_wav or c.raw_wav)],
                    key=lambda c: c.start)
    cursor = 0.0
    for c in usable:                       # tohle musí odpovídat mixeru
        if c.start - cursor > 0.02:
            cursor = c.start
        c.real_start = cursor
        cursor += (c.fitted_dur or c.natural_dur or 0.0)
    by_index = {c.index: c for c in usable}
    out, moved = [], 0
    for i, s in enumerate(out_segs):
        c = by_index.get(i)
        seg = dict(s)
        if c and (c.fitted_dur or c.natural_dur):
            length = c.fitted_dur or c.natural_dur
            # Slovní časy se měřily před time-stretchem → přepočítat měřítkem.
            scale = (length / c.natural_dur) if c.natural_dur > 0 else 1.0
            first_w, last_w = None, None
            if c.words:
                first_w = float(c.words[0].get("start") or 0.0) * scale
                last_w = float(c.words[-1].get("end") or 0.0) * scale
            new_start = c.real_start + min(first_w if first_w is not None else 0.0, length)
            new_end = c.real_start + min(last_w if last_w is not None else length, length)
            if new_end - new_start < 0.3:          # nesmyslně krátké → radši celý klip
                new_start, new_end = c.real_start, c.real_start + length
            if abs(new_start - float(s.get("start") or 0.0)) > 0.15:
                moved += 1
            seg["start"] = round(new_start, 3)
            seg["end"] = round(new_end, 3)
            if c.words:
                seg["clip_words"] = [
                    {"w": w.get("w"),
                     "start": round(c.real_start + float(w.get("start") or 0.0) * scale, 3),
                     "end": round(c.real_start + float(w.get("end") or 0.0) * scale, 3)}
                    for w in c.words]
            if log and i < 12:
                log(f"    titulek {i}: {float(s.get('start') or 0):.2f}–{float(s.get('end') or 0):.2f}"
                    f" → {new_start:.2f}–{new_end:.2f}  (klip zní od {c.real_start:.2f},"
                    f" délka {length:.2f}s)")
        out.append(seg)
    # Titulky se nikdy nesmí překrývat — replika se mohla protáhnout do pauzy
    # a přesáhnout tak přes začátek té další.
    for i in range(len(out) - 1):
        nxt = float(out[i + 1].get("start") or 0.0)
        if float(out[i].get("end") or 0.0) > nxt - 0.04:
            out[i]["end"] = round(max(float(out[i]["start"]) + 0.3, nxt - 0.04), 3)
    if log and moved:
        log(f"Titulky navěšeny na skutečný dabing ({moved} z {len(out)} posunuto)")
    return out


def _speech_end(segs, i: int, total_dur: float = 0.0) -> float:
    """Do kdy smí i-tá replika mluvit — konec slotu + ticho, které po ní běží.

    Čeština je delší než většina zdrojových jazyků, takže se do slotu často
    nevejde. Místo drcení věty time-stretchem se domluví do následující pauzy,
    jak to dělá i lidský dabing. Před další replikou zůstane rezerva
    (``DUB_SLOT_GUARD``) a prodloužení je shora omezené (``DUB_SLOT_EXTEND_MAX``),
    aby řeč nezůstávala viset dlouho po obraze.
    """
    end = float(segs[i].get("end") or 0.0)
    if i + 1 < len(segs):
        limit = float(segs[i + 1].get("start") or 0.0)
    else:
        limit = float(total_dur or 0.0)
    if limit <= end:
        return end
    room = min(limit - end - config.DUB_SLOT_GUARD, config.DUB_SLOT_EXTEND_MAX)
    return end + room if room > 0 else end


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
        # Zlom na hranici věty je pro čtenáře přirozený, i když není přesně
        # v půlce – zvýhodni ho (a o něco méně i zlom za čárkou).
        tail = words[i].rstrip()[-1:]
        if tail in ".!?…":
            d -= 40
        elif tail in ",;:–—":
            d -= 12
        if d < fb_d:
            fb_d, fb_i = d, i
        if _norm_w(words[i]) in _CZ_NOBREAK:  # za předložkou nelámat
            continue
        if d < best_d:
            best_d, best_i = d, i
    if best_i < 0:
        best_i = fb_i
    return " ".join(words[:best_i + 1]) + "\n" + " ".join(words[best_i + 1:])


def _split_by_sense(text: str, max_chars: int) -> list:
    """Rozseká text na kusy do `max_chars` tak, aby řezy padly na hranice smyslu.

    Hladové plnění po slovech seká fráze uprostřed („Protivzdušná / obrana“,
    „a já nemůžu / připojit“), což se špatně čte. Proto se text nejdřív dělí na
    věty, a teprve příliš dlouhá věta se dělí dál — přednostně za čárkou nebo
    před spojkou, až v poslední řadě na nejvyváženějším místě mezi slovy.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    sentences = [s.strip() for s in re.findall(r"[^.!?…]+[.!?…]*", text) if s.strip()]
    if len(sentences) > 1:
        out = []
        for s in sentences:
            out += _split_by_sense(s, max_chars)
        return out
    words = text.split()
    if len(words) < 2:
        return [text]
    # Jedna dlouhá věta: najdi řez co nejblíž půlce, ale na hranici smyslu.
    target = len(text) / 2
    best, best_score = None, None
    acc = 0
    for i in range(len(words) - 1):
        acc += len(words[i]) + 1
        score = abs(acc - target)
        if words[i].rstrip()[-1:] in ",;:–—":
            score -= 25                       # řez za čárkou je přirozený
        elif _norm_w(words[i + 1]) in ("a", "ale", "nebo", "protože", "že", "když"):
            score -= 15                       # před spojkou taky
        if _norm_w(words[i]) in _CZ_NOBREAK:  # předložka nesmí zůstat viset
            continue
        if best_score is None or score < best_score:
            best_score, best = score, i
    if best is None:
        best = len(words) // 2 - 1
    left = " ".join(words[:best + 1])
    right = " ".join(words[best + 1:])
    return _split_by_sense(left, max_chars) + _split_by_sense(right, max_chars)


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
        # Nejdřív rozděl podle smyslu (věty, čárky), pak sousední kousky slučuj
        # zpátky, dokud se vejdou do rozsahu cue — tím se text neseká uprostřed
        # fráze a zároveň nevznikají zbytečně útržkovité titulky.
        parts = []
        for piece in _split_by_sense(text, max_chars):
            if (parts and len(parts[-1]) + 1 + len(piece) <= max_chars
                    and (len(parts[-1]) + 1 + len(piece)) / cps <= max_dur):
                parts[-1] = parts[-1] + " " + piece
            else:
                parts.append(piece)
        parts = _carry_dangling(parts)         # předložka nesmí viset na konci cue
        # Časy kusů: nejpřesnější jsou slovní časy naměřené v dabovaném klipu
        # (řeč není rovnoměrná, takže dělení „podle počtu znaků“ ujíždí).
        # Použijí se, jen když počet slov sedí — jinak zůstane poměrný odhad.
        cw = s.get("clip_words") or []
        seg_words = text.split()
        local = []
        if cw and len(cw) == len(seg_words):
            wi = 0
            for i, p in enumerate(parts):
                n = len(p.split())
                w0, w1 = cw[wi], cw[min(wi + n - 1, len(cw) - 1)]
                st_ = float(w0.get("start") or start)
                en_ = float(w1.get("end") or end)
                if i == len(parts) - 1:
                    en_ = max(en_, end if end > st_ else en_)
                local.append([round(st_, 3), round(max(en_, st_ + 0.3), 3), p])
                wi += n
        else:
            total = sum(len(p) for p in parts) or 1
            t = start
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
               preserve_audio=False, u_size_px=0, u_posy=0):
    """Zapéct titulky (z out_segs) do src_video → dst_video. Jediné místo s burn
    logikou (volá ho run_dub i dodatečné zapečení burn_existing_video).

    u_size_px > 0 : přesná velikost písma v pixelech (má přednost před presetem
                    i před u_size). PlayResY == výška videa, takže ASS Fontsize
                    je přímo v pixelech výstupu.
    u_posy   > 0  : svislá pozice titulku v % výšky videa shora (88 = klasické
                    dole, menší číslo = výš). Přepočte se na spodní okraj MarginV.
    """
    vw, vh = ffmpeg_tools.get_video_size(src_video, log)
    bopts = _burn_preset_opts(preset, vw, vh)
    bopts["audio_copy"] = bool(preserve_audio)
    if u_lines in (1, 2):
        bopts["maxlines"] = u_lines
    _SZ = {"small": 0.035, "medium": 0.046, "large": 0.062, "xl": 0.08}
    if u_size in _SZ and vh:
        bopts["size"] = max(12, round(vh * _SZ[u_size]))
    if u_size_px and int(u_size_px) > 0:
        bopts["size"] = max(8, min(400, int(u_size_px)))
        # okraj přepočítej na novou velikost, ať nezmizí u velkého písma
        bopts["outline"] = max(1, round(bopts["size"] * 0.09))
    if u_posy and int(u_posy) > 0 and vh:
        # align 2 = kotva dole; MarginV = mezera od spodní hrany.
        py = max(1, min(99, int(u_posy)))
        bopts["align"] = 2
        bopts["marginv"] = max(0, round(vh * (100 - py) / 100.0))
    if vw and vw > 0:
        # 0.50 = změřená střední šířka znaku Arial Bold vůči velikosti písma
        # (dřívějších 0.62 bylo nadhodnocené: u 720×1280 to vycházelo na 14 zn.,
        # ale reálně se na řádek vejde ~25 → titulky se lámaly zbytečně brzy).
        fit = max(8, int(vw * 0.92 / (bopts["size"] * 0.50)))
        preset_chars = int(bopts.get("chars") or 0)     # 0 = řídit se jen šířkou
        bopts["chars"] = (min(u_chars, fit) if u_chars > 0
                          else (min(preset_chars, fit) if preset_chars else fit))
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
                        subs_chars=0, subs_maxlines=0, subs_size="", log=None,
                        subs_size_px=0, subs_posy=0):
    """Dodatečné zapečení titulků do JIŽ hotového (např. dabovaného) videa –
    bez nového nahrávání/dabingu. Segmenty se vezmou z existujícího SRT."""
    out_segs = _parse_srt(srt_path)
    if not out_segs:
        raise RuntimeError("V titulkovém SRT nejsou žádné titulky.")
    work = Path(out_path).parent
    tmp = work / ("_reburn_" + Path(out_path).name)
    _burn_into(Path(video_path), out_segs, tmp, work, preset,
               int(subs_chars or 0), int(subs_maxlines or 0), str(subs_size or ""),
               log=log, u_size_px=int(subs_size_px or 0), u_posy=int(subs_posy or 0))
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
    # Rozpočet znaků pro každý blok — ať i dávkový (kontextový) překlad ví,
    # kolik se toho dá reálně vyslovit; jinak si počítá vlastní hrubý odhad.
    for i, ch in enumerate(chunks):
        room = _speech_end(chunks, i, dur) - float(ch.get("start") or 0.0)
        ch["max_chars"] = max(20, int(room * config.DUB_CHARS_PER_SEC))
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
        # Dabingový rozpočet: kolik znaků se dá vyslovit za čas, který replika
        # reálně má (slot + ticho za ním). Sazba je MĚŘENÁ pro české XTTS, ne
        # odhadnutá – viz config.DUB_CHARS_PER_SEC. Drží překlad dost krátký,
        # aby se řeč nemusela drtit time-stretchem.
        speak_until = _speech_end(chunks, i, dur)
        budget = int((speak_until - start) * config.DUB_CHARS_PER_SEC)
        budget = max(0, budget)
        if batch_translations is not None:
            tr = batch_translations[i]
        elif txt and not same_lang:
            tr = asr_engine.translate_text(txt, target, log, source=effective_src,
                                           max_chars=budget,
                                           translator=getattr(job, "translator", "local"))
        else:
            tr = txt
        # Překladač rozpočet znaků nedodrží spolehlivě. Co hodně přeteče, musel
        # by dohnat time-stretch (a ten řeč deformuje), tak repliku zkrátíme.
        # Práh je vysoký schválně: přetečení do ~třetiny utáhne nativní rychlost
        # TTS bez slyšitelné újmy, kdežto zkracování přes LLM umí rozbít smysl
        # věty. Radši mírně rychlejší řeč než zkomolený obsah.
        if not same_lang and budget and len(tr) > budget * 1.35:
            tr = asr_engine.condense_text(tr, budget, log)
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
                       preserve_audio=True,
                       u_size_px=int(getattr(job, "subs_size_px", 0) or 0),
                       u_posy=int(getattr(job, "subs_posy", 0) or 0))
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
        # Ořez balastu za koncem repliky se vyplatí u klonovacích enginů (XTTS);
        # stojí jeden rychlý ASR průchod na klip, ale ušetří drcení time-stretchem.
        trim_tail = config.DUB_TRIM_TAIL and getattr(backend, "needs_reference", False)
        for i, s in enumerate(out_segs):
            text = (s["text"] or "").strip()
            c = VoiceClip(index=i, start=float(s["start"] or 0.0),
                          end=float(s["end"] or 0.0), text=text)
            # Ticho za replikou patří taky řeči — smí se do něj domluvit.
            c.slot_ext = _speech_end(out_segs, i, dur) - c.start
            if text:
                raw = work / f"tts_{i:05d}.wav"
                backend.synth(text, raw, voice=tts_voice, lang=target, log=log)
                if trim_tail:
                    c.words = _trim_tts_tail(raw, text, work, log=log)
                c.natural_dur = ff.duration(raw)
                # Enginy s nativní rychlostí (XTTS): když je klip delší než slot,
                # přemluv ho RYCHLEJI nativně — zachová kvalitu, na rozdíl od
                # ffmpeg atempo, které při ~1.5× rozbije řeč na kaši.
                if (getattr(backend, "supports_speed", False)
                        and c.fit_slot > 0.3 and c.natural_dur > 0):
                    ratio = c.natural_dur / c.fit_slot
                    if ratio > config.DUB_NATIVE_SPEED_FROM:
                        # XTTS udrží přirozenost jen do mírného zrychlení;
                        # zbytek dožene rubberband (kvalitní stretch).
                        spd = min(ratio, 1.3)
                        gained = c.fit_slot - c.slot
                        extra = f", slot {c.slot:.1f}s + {gained:.1f}s pauzy" if gained > 0.05 else ""
                        log(f"  segment {i}: klip {ratio:.2f}× delší než slot{extra} "
                            f"→ přemluvím nativní rychlostí {spd:.2f}× (zbytek rubberband)")
                        backend.synth(text, raw, voice=tts_voice, lang=target,
                                      speed=spd, log=log)
                        if trim_tail:
                            c.words = _trim_tts_tail(raw, text, work, log=log)
                        c.natural_dur = ff.duration(raw)
                c.raw_wav = str(raw)
            clips.append(c)
            prog("synthesizing", 58 + (i + 1) / n_tts * 18)

        # 5) zarovnání klipů na čas, který mají k dispozici (slot + pauza za ním)
        prog("aligning", 80)
        for c in clips:
            if c.raw_wav:
                fitted = work / f"fit_{c.index:05d}.wav"
                c.fitted_dur = align.fit_clip(c.raw_wav, fitted, c.fit_slot, log=log)
                c.fitted_wav = str(fitted)

        # 5b) titulky navěsit na SKUTEČNÝ dabing, ne na časy originálu
        resynced = _resync_to_dub(out_segs, clips, log=log)
        if resynced is not out_segs:
            out_segs = resynced
            # SRT/JSON se psaly ještě před syntézou (z časů originálu) — přepiš je,
            # ať přiložené titulky sedí na hlas stejně jako ty zapečené.
            _write_target_outputs(jobs, job_id, job, target, out_segs)

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
                           progress_cb=lambda pct: prog("burning", 96 + int(pct * 0.03)),
                           u_size_px=int(getattr(job, "subs_size_px", 0) or 0),
                           u_posy=int(getattr(job, "subs_posy", 0) or 0))
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
