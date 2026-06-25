"""
Práce s ffmpeg / ffprobe:
  - ověření dostupnosti
  - konverze libovolného audia/videa na WAV 16 kHz mono PCM s16le
  - zjištění délky audia a FPS videa
  - zapékání SRT titulků do videa
  - logování chyb
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional, Union

from . import config

LogFn = Optional[Callable[[str], None]]

# Na Windows skryj okno konzole spouštěného procesu (CREATE_NO_WINDOW).
_CREATE_NO_WINDOW = 0x08000000


def _popen_kwargs() -> dict:
    kwargs: dict = {}
    if hasattr(subprocess, "STARTUPINFO"):   # tj. běžíme na Windows
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return kwargs


def _log(log: LogFn, msg: str) -> None:
    if log:
        log(msg)


class FfmpegError(RuntimeError):
    pass


def check_ffmpeg() -> dict:
    """Vrátí stav ffmpeg / ffprobe (pro diagnostiku)."""
    ffmpeg = config.find_ffmpeg()
    ffprobe = config.find_ffprobe()
    version = None
    if ffmpeg:
        try:
            out = subprocess.run(
                [str(ffmpeg), "-version"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=15, **_popen_kwargs(),
            )
            if out.stdout:
                version = out.stdout.splitlines()[0]
        except Exception:
            version = None
    return {
        "ok": ffmpeg is not None,
        "ffmpeg": str(ffmpeg) if ffmpeg else None,
        "ffprobe": str(ffprobe) if ffprobe else None,
        "version": version,
    }


def get_audio_duration(path: Union[str, Path], log: LogFn = None) -> float:
    """Délka audia v sekundách. Nejdřív ffprobe, fallback wave (jen .wav)."""
    path = Path(path)
    ffprobe = config.find_ffprobe()
    if ffprobe:
        try:
            out = subprocess.run(
                [str(ffprobe), "-v", "quiet", "-print_format", "json",
                 "-show_format", str(path)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, **_popen_kwargs(),
            )
            data = json.loads(out.stdout or "{}")
            dur = data.get("format", {}).get("duration")
            if dur is not None:
                return float(dur)
        except Exception as e:
            _log(log, f"ffprobe nezjistil délku, zkouším wave: {e}")
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                frames = w.getnframes()
                rate = w.getframerate()
                if rate:
                    return frames / float(rate)
        except Exception as e:
            _log(log, f"wave délku nezjistil: {e}")
    return 0.0


def convert_to_wav(input_path: Union[str, Path], output_wav: Union[str, Path],
                   log: LogFn = None) -> Path:
    """
    Převede libovolné podporované audio/video na WAV 16 kHz mono PCM s16le.
    Vrací cestu k výslednému WAV. Při chybě vyhodí FfmpegError.
    """
    ffmpeg = config.find_ffmpeg()
    if not ffmpeg:
        raise FfmpegError(
            "ffmpeg nebyl nalezen. Dej ffmpeg.exe (a ffprobe.exe) do "
            "tools/ffmpeg/ nebo do systémové PATH. Viz README."
        )
    input_path = Path(input_path)
    output_wav = Path(output_wav)
    output_wav.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(ffmpeg), "-y",
        "-i", str(input_path),
        "-vn",                                  # zahodit video stopu
        "-ac", str(config.TARGET_CHANNELS),     # mono
        "-ar", str(config.TARGET_SAMPLE_RATE),  # 16 kHz
        "-acodec", config.TARGET_CODEC,         # PCM s16le
        "-f", "wav",
        str(output_wav),
    ]
    _log(log, "FFMPEG: " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=1800, **_popen_kwargs())
    except subprocess.TimeoutExpired:
        raise FfmpegError("ffmpeg konverze trvala příliš dlouho (timeout 30 min).")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-15:]
        _log(log, "FFMPEG chyba:\n" + "\n".join(tail))
        raise FfmpegError(
            f"ffmpeg konverze selhala (kód {proc.returncode}). Detail v logu jobu."
        )
    if not output_wav.is_file() or output_wav.stat().st_size == 0:
        raise FfmpegError("ffmpeg nevytvořil výstupní WAV (prázdný soubor).")
    _log(log, f"WAV hotov: {output_wav.name} ({output_wav.stat().st_size} B)")
    return output_wav


def get_video_fps(path: Union[str, Path], log: LogFn = None) -> Optional[float]:
    """Vrátí FPS první video stopy, nebo None pokud nelze zjistit."""
    path = Path(path)
    ffprobe = config.find_ffprobe()
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [str(ffprobe), "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, **_popen_kwargs(),
        )
        streams = json.loads(out.stdout or "{}").get("streams", [])
        if streams:
            rfr = streams[0].get("r_frame_rate", "")
            if "/" in rfr:
                num, den = rfr.split("/")
                if int(den):
                    return round(int(num) / int(den), 3)
    except Exception as e:
        _log(log, f"ffprobe fps nezjistil: {e}")
    return None


def get_video_size(path: Union[str, Path], log: LogFn = None) -> tuple:
    """Vrátí (šířka, výška) první video stopy, fallback (1920, 1080)."""
    path = Path(path)
    ffprobe = config.find_ffprobe()
    if ffprobe:
        try:
            out = subprocess.run(
                [str(ffprobe), "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-select_streams", "v:0", str(path)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, **_popen_kwargs(),
            )
            streams = json.loads(out.stdout or "{}").get("streams", [])
            if streams:
                w = int(streams[0].get("width") or 0)
                h = int(streams[0].get("height") or 0)
                if w > 0 and h > 0:
                    return (w, h)
        except Exception as e:
            _log(log, f"ffprobe rozměry nezjistil: {e}")
    return (1920, 1080)


def _wrap_line(text: str, chars: int, max_lines: int = 2) -> str:
    """Zalomí text na řádky do `chars` znaků (po slovech), max `max_lines` řádků."""
    words = (text or "").split()
    lines, cur = [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= chars:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        head = lines[:max_lines - 1]
        head.append(" ".join(lines[max_lines - 1:]))
        lines = head
    return "\n".join(lines)


def _rewrap_srt(srt_text: str, chars: int, max_lines: int = 2) -> str:
    """Přepíše každý titulek v SRT tak, aby měl max `chars` znaků na řádek
    a nejvýš `max_lines` řádků."""
    import re as _re
    blocks = _re.split(r"\r?\n\s*\r?\n", (srt_text or "").strip())
    out = []
    for b in blocks:
        lines = b.splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            text = " ".join(l.strip() for l in lines[2:] if l.strip())
            out.append(lines[0] + "\n" + lines[1] + "\n" + _wrap_line(text, chars, max_lines))
        elif b.strip():
            out.append(b)
    return "\n\n".join(out) + "\n"


def _escape_ass(text: str) -> str:
    """Escapuje znaky se speciálním významem v ASS ({ } \\), aby je libass
    nebral jako override tagy (např. uvozovky/závorky z LLM korekce/překladu)."""
    return (text or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _srt_ts_to_ass(ts: str) -> str:
    """HH:MM:SS,mmm -> H:MM:SS.cc (ASS čas)."""
    ts = ts.strip().replace(".", ",")
    try:
        hh, mm, rest = ts.split(":")
        ss, ms = rest.split(",")
        return f"{int(hh)}:{int(mm):02d}:{int(ss):02d}.{int(ms) // 10:02d}"
    except Exception:
        return "0:00:00.00"


def _build_ass(srt_text: str, vid_w: int, vid_h: int, font: str, size: int,
               align: int, marginv: int, bold: int, outline: int,
               chars: int, max_lines: int, border_style: int = 1,
               outline_colour: str = "&H00000000", back_colour: str = "&H00000000",
               shadow: int = 0) -> str:
    """Sestaví ASS titulky s WrapStyle:2 (vypne auto-zalamování libass), takže
    počet řádků je přesně dán (1 nebo 2) – ne šířkou videa.
    border_style 1=okraj+stín, 3=plný box (podklad). Barvy ASS = &HAABBGGRR."""
    import re as _re
    blocks = _re.split(r"\r?\n\s*\r?\n", (srt_text or "").strip())
    events = []
    wrap_chars = chars if (chars and chars >= 10) else (1000 if max_lines == 1 else 42)
    for b in blocks:
        lines = b.splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            start, _, end = lines[1].partition("-->")
            text = _escape_ass(" ".join(l.strip() for l in lines[2:] if l.strip()))
            wrapped = _wrap_line(text, wrap_chars, max_lines).replace("\n", "\\N")
            events.append(
                f"Dialogue: 0,{_srt_ts_to_ass(start)},{_srt_ts_to_ass(end)},"
                f"Default,,0,0,0,,{wrapped}")
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
        f"PlayResX: {vid_w}\nPlayResY: {vid_h}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},&H00FFFFFF,{outline_colour},{back_colour},{bold},0,0,0,"
        f"100,100,0,0,{border_style},{outline},{shadow},{align},40,40,{marginv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header + "\n".join(events) + "\n"


def _sec_to_ass(sec: float) -> str:
    """Sekundy -> ASS čas H:MM:SS.cc."""
    if sec < 0:
        sec = 0.0
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, c = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _karaoke_segment(tokens: list, seg_start: float, seg_end: float,
                     chars: int, max_lines: int) -> str:
    """Z tokenů {w,start,end} sestaví karaoke text s \\k tagy (celé slovo se
    rozsvítí naráz) a zlomy \\N. Bez časů slov rozloží trvání poměrově."""
    words = [(t.get("w") or "").strip() for t in tokens]
    words = [w for w in words if w]
    if not words:
        return ""
    n = len(words)
    toks = [t for t in tokens if (t.get("w") or "").strip()]
    have = len(toks) == n and all(("start" in t and "end" in t) for t in toks)
    if have:
        st = [float(toks[i]["start"]) for i in range(n)]
        durs = []
        for i in range(n):
            nxt = st[i + 1] if i + 1 < n else seg_end
            durs.append(max(1, int(round((nxt - st[i]) * 100))))
    else:
        total = max(1, int(round((seg_end - seg_start) * 100)))
        lens = [max(1, len(w)) for w in words]
        ssum = sum(lens)
        durs = [max(1, int(round(total * l / ssum))) for l in lens]
    wrap = chars if (chars and chars >= 10) else (1000 if max_lines == 1 else 42)
    lines, cur, curlen = [], [], 0
    for w, d in zip(words, durs):
        if cur and (curlen + 1 + len(w)) > wrap and len(lines) < max_lines - 1:
            lines.append(cur)
            cur, curlen = [], 0
        cur.append((w, d))
        curlen += len(w) + (1 if curlen else 0)
    if cur:
        lines.append(cur)
    # \k = celé slovo se rozsvítí naráz, jakmile se vysloví (ne plynulý sweep \kf)
    # escapuje se JEN slovo, nikdy ne \k tag
    parts = ["".join(f"{{\\k{d}}}{_escape_ass(w)} " for w, d in ln).rstrip() for ln in lines]
    return "\\N".join(parts)


def _build_ass_karaoke(segments: list, vid_w: int, vid_h: int, font: str, size: int,
                       align: int, marginv: int, bold: int, outline: int, chars: int,
                       max_lines: int, border_style: int, outline_colour: str,
                       back_colour: str, shadow: int, primary: str, secondary: str) -> str:
    """ASS s karaoke efektem (slova se vybarvují v rytmu řeči). primary = barva
    vybarveného slova, secondary = barva ještě nevyřčeného (základ)."""
    events = []
    for seg in segments:
        toks = seg.get("tokens") or [{"w": w} for w in str(seg.get("text", "")).split()]
        start = float(seg.get("start", 0) or 0)
        end = float(seg.get("end", start) or start)
        ktext = _karaoke_segment(toks, start, end, chars, max_lines)
        if ktext:
            events.append(f"Dialogue: 0,{_sec_to_ass(start)},{_sec_to_ass(end)},"
                          f"Default,,0,0,0,,{ktext}")
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\n"
        f"PlayResX: {vid_w}\nPlayResY: {vid_h}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},{primary},{secondary},{outline_colour},{back_colour},"
        f"{bold},0,0,0,100,100,0,0,{border_style},{outline},{shadow},{align},40,40,{marginv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header + "\n".join(events) + "\n"


def burn_subtitles(video_path: Union[str, Path], srt_path: Union[str, Path],
                   output_path: Union[str, Path], opts: Optional[dict] = None,
                   progress_cb: Optional[Callable[[int], None]] = None,
                   log: LogFn = None) -> Path:
    """
    Zapéct SRT titulky do videa (H.264/AAC MP4). opts: font, size, align
    (2=dole,5=uprostřed,8=nahoře), marginv, chars (zalomení), bold.
    progress_cb(pct 0-100) hlásí průběh enkódování.
    """
    import shutil
    opts = opts or {}
    ffmpeg = config.find_ffmpeg()
    if not ffmpeg:
        raise FfmpegError("ffmpeg nebyl nalezen.")

    video_path  = Path(video_path)
    srt_path    = Path(srt_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # nastavení stylu
    font = re.sub(r"[^A-Za-z0-9 ]", "", str(opts.get("font", "Arial"))) or "Arial"
    size = max(8, min(200, int(opts.get("size", 24))))
    align = int(opts.get("align", 2))
    if align not in (1, 2, 3, 4, 5, 6, 7, 8, 9):
        align = 2
    marginv = max(0, min(800, int(opts.get("marginv", 36))))
    bold = -1 if opts.get("bold") else 0
    chars = int(opts.get("chars", 0) or 0)
    maxlines = 1 if int(opts.get("maxlines", 2) or 2) == 1 else 2

    # okraj (uživatelský, jinak automatický dle velikosti)
    outline = int(opts.get("outline", 0) or 0)
    if outline <= 0:
        outline = max(1, round(size / 12))
    outline = max(0, min(20, outline))

    # podklad pod titulky: 'box' = plný obdélník (BorderStyle 3), jinak jen okraj.
    # průhlednost 0 % = plná barva, 100 % = neviditelné. ASS alfa: 00=plné, FF=průhledné.
    bg = str(opts.get("bg", "none"))
    bgalpha = max(0, min(100, int(opts.get("bgalpha", 35))))
    a = format(round(bgalpha / 100 * 255), "02X")
    if bg == "box":
        border_style = 3
        outline_colour = f"&H{a}000000"   # box je černý s danou průhledností (libass: OutlineColour)
        back_colour = f"&H{a}000000"
        shadow = 0
    else:
        border_style = 1                  # klasický černý okraj kolem písma
        outline_colour = "&H00000000"
        back_colour = "&H00000000"
        shadow = 0

    # Vlastní ASS s WrapStyle:2 – počet řádků (1/2) je pevně daný, libass
    # dlouhý řádek sám nezalomí. ASS dáme vedle výstupu a ffmpeg pustíme s cwd
    # (Windows neumí dvojtečku disku ve filtru -> relativní název).
    vid_w, vid_h = get_video_size(video_path, log)
    mode = str(opts.get("mode", "normal"))
    segments = opts.get("segments")
    if mode == "karaoke" and segments:
        # barva vybarveného slova (PrimaryColour); základ = bílá (SecondaryColour)
        HI = {"yellow": "&H0000FFFF", "green": "&H0000FF00", "cyan": "&H00FFFF00",
              "red": "&H000000FF", "white": "&H00FFFFFF"}
        primary = HI.get(str(opts.get("hicolor", "yellow")), "&H0000FFFF")
        ass_text = _build_ass_karaoke(
            segments, vid_w, vid_h, font, size, align, marginv, bold, outline,
            chars, maxlines, border_style, outline_colour, back_colour, shadow,
            primary, "&H00FFFFFF")
    else:
        src_text = srt_path.read_text(encoding="utf-8", errors="replace")
        ass_text = _build_ass(src_text, vid_w, vid_h, font, size, align, marginv,
                              bold, outline, chars, maxlines,
                              border_style=border_style, outline_colour=outline_colour,
                              back_colour=back_colour, shadow=shadow)
    tmp_ass = output_path.with_suffix(".tmp.ass")
    tmp_ass.write_text(ass_text, encoding="utf-8")
    work_dir = tmp_ass.parent
    srt_rel = tmp_ass.name

    total = get_audio_duration(video_path) or 0.0
    cmd = [
        str(ffmpeg), "-y", "-i", str(video_path),
        "-vf", f"subtitles={srt_rel}",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(output_path),
    ]
    _log(log, f"FFMPEG burn-in (cwd={work_dir}): " + " ".join(cmd))

    err_path = output_path.with_suffix(".tmp.err")
    rc = 1
    stalled = {"v": False}
    proc = None
    try:
        with open(err_path, "w", encoding="utf-8", errors="replace") as errf:
            proc = subprocess.Popen(
                cmd, cwd=str(work_dir), stdout=subprocess.PIPE, stderr=errf,
                text=True, encoding="utf-8", errors="replace", **_popen_kwargs())
            # watchdog: zabije zaseknutý ffmpeg, když 10 min nepřibude žádný řádek
            activity = [time.time()]

            def _watchdog():
                while proc.poll() is None:
                    if time.time() - activity[0] > 600:
                        stalled["v"] = True
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        return
                    time.sleep(5)

            threading.Thread(target=_watchdog, daemon=True).start()
            last = -1
            for line in proc.stdout:
                activity[0] = time.time()
                line = line.strip()
                us = None
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    val = line.split("=", 1)[1]
                    if val.isdigit():
                        us = int(val) / (1e6 if "us=" in line else 1e3)
                if us is not None and total > 0 and progress_cb:
                    pct = max(0, min(99, int(us / total * 100)))
                    if pct != last:
                        last = pct
                        progress_cb(pct)
            proc.wait()
            rc = proc.returncode
    finally:
        for p in (tmp_ass,):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    if stalled["v"]:
        raise FfmpegError("ffmpeg zapékání se zaseklo (10 min bez postupu) – ukončeno.")

    err = ""
    try:
        err = err_path.read_text(encoding="utf-8", errors="replace")
        err_path.unlink(missing_ok=True)
    except Exception:
        pass

    if rc != 0:
        tail = (err or "").strip().splitlines()[-20:]
        _log(log, "FFMPEG chyba:\n" + "\n".join(tail))
        raise FfmpegError(
            f"ffmpeg burn-in selhal (kód {rc}). Detail v logu jobu.")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise FfmpegError("ffmpeg nevytvořil výstupní video (prázdný soubor).")
    if progress_cb:
        progress_cb(100)
    _log(log, f"Burn-in hotov: {output_path.name} ({output_path.stat().st_size // 1024} kB)")
    return output_path
