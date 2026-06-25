"""
ASR engine - wrapper kolem lokální parakeet.cpp binárky (parakeet-cli).

================================================================
  CELÉ volání CLI je izolováno do build_transcribe_command()
  a _run_parakeet(). Pokud se v budoucnu změní rozhraní
  parakeet.cpp, upravuje se POUZE tento soubor (ideálně jen
  funkce build_transcribe_command).
================================================================

Ověřené chování parakeet-cli (mudler/parakeet.cpp, master, 2026-06):

  parakeet-cli transcribe --model M.gguf --input audio.wav --json

  Vstup : WAV 16 kHz mono PCM s16le.
  --json: vypíše na stdout strukturu:
      {
        "text": "...",
        "frame_sec": 0.08,
        "words":  [{"w": "Well,", "start": 0.48, "end": 0.64, "conf": 0.78}],
        "tokens": [{"id": 639, "t": 0.48, "conf": 0.99}]
      }
  Další flagy: --timestamps, --decoder ctc|tdt, --stream, --threads N.

  Jazyk: model nemotron-3.5-asr-streaming-0.6b umí auto-detekci
  (cs / en / uk a dalších ~40 locale). --lang je u některých buildů
  jen u 'serve' režimu, ne u 'transcribe' -> proto je flag volitelný
  a když ho binárka odmítne, _run_parakeet() příkaz zopakuje bez něj.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

from . import config

LogFn = Optional[Callable[[str], None]]


# --- chyby ----------------------------------------------------------------
class AsrError(RuntimeError):
    """Obecná chyba ASR backendu (chytá ji pipeline a označí job jako error)."""


class ParakeetNotFoundError(AsrError):
    pass


class ModelNotFoundError(AsrError):
    pass


# --- pomocné --------------------------------------------------------------
def _log(log: LogFn, msg: str) -> None:
    if log:
        log(msg)


def _popen_kwargs() -> dict:
    kwargs: dict = {}
    if hasattr(subprocess, "STARTUPINFO"):       # Windows -> skryj konzoli
        kwargs["creationflags"] = 0x08000000     # CREATE_NO_WINDOW
    return kwargs


# ==========================================================================
#  JEDINÉ MÍSTO, kde se staví příkazová řádka pro parakeet.cpp.
#  >>> Když parakeet.cpp změní flagy, uprav TUTO funkci. <<<
# ==========================================================================
def build_transcribe_command(exe: Path, model: Path, wav: Path,
                             language: str, send_lang_flag: bool) -> List[str]:
    cmd: List[str] = [
        str(exe), "transcribe",
        "--model", str(model),
        "--input", str(wav),
        "--json",                # strukturovaný výstup s časy slov
    ]
    if config.PARAKEET_DECODER:
        cmd += ["--decoder", str(config.PARAKEET_DECODER)]
    if config.PARAKEET_THREADS:
        cmd += ["--threads", str(config.PARAKEET_THREADS)]
    if config.PARAKEET_STREAM:
        # Pozn.: --stream je cache-aware EOU režim (živý přepis). Pro offline
        # přepis souboru se NEdoporučuje (default vypnuto v config.py).
        cmd += ["--stream"]

    # Jazyk: viz docstring nahoře. Volitelný a s fallbackem v _run_parakeet().
    lang_code = config.LANGUAGE_MAP.get(language)
    if send_lang_flag and lang_code:
        cmd += [config.PARAKEET_LANG_FLAG, lang_code]
    return cmd


def _looks_like_unknown_flag_error(stderr: str) -> bool:
    s = (stderr or "").lower()
    needles = ["unknown", "unrecognized", "invalid option", "invalid argument",
               "unexpected argument", "no such option", "usage:"]
    return any(n in s for n in needles)


def _run_once(cmd: List[str], log: LogFn, timeout: Optional[int]):
    _log(log, "PARAKEET: " + " ".join(cmd))
    return subprocess.run(
        cmd, capture_output=True, text=True,
        # DŮLEŽITÉ: parakeet.cpp tiskne UTF-8. Bez explicitního encoding by
        # Python na české Windows dekódoval výstup přes cp1250 -> mojibake
        # (např. "mĂˇ" místo "má"). Vždy UTF-8.
        encoding="utf-8", errors="replace",
        timeout=timeout if timeout else None,
        **_popen_kwargs(),
    )


def _run_parakeet(exe: Path, model: Path, wav: Path, language: str,
                  log: LogFn) -> str:
    """Spustí parakeet-cli, vrátí stdout. Bezpečně (žádný shell=True)."""
    timeout = config.PARAKEET_TIMEOUT or None
    send_lang = config.PARAKEET_SEND_LANG_FLAG
    has_lang = config.LANGUAGE_MAP.get(language) is not None

    cmd = build_transcribe_command(exe, model, wav, language, send_lang)
    try:
        proc = _run_once(cmd, log, timeout)
    except subprocess.TimeoutExpired:
        raise AsrError(f"parakeet.cpp překročil timeout {timeout}s.")
    except FileNotFoundError:
        raise ParakeetNotFoundError(f"Nelze spustit binárku: {exe}")

    # Fallback: selhalo to kvůli neznámému flagu a posílali jsme --lang?
    if proc.returncode != 0 and send_lang and has_lang and \
            _looks_like_unknown_flag_error(proc.stderr):
        _log(log, "Binárka nezná jazykový flag, opakuji bez něj (auto-detekce).")
        cmd = build_transcribe_command(exe, model, wav, language,
                                       send_lang_flag=False)
        try:
            proc = _run_once(cmd, log, timeout)
        except subprocess.TimeoutExpired:
            raise AsrError(f"parakeet.cpp překročil timeout {timeout}s.")

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-20:]
        _log(log, "PARAKEET chyba:\n" + "\n".join(tail))
        raise AsrError(
            f"parakeet.cpp skončil s kódem {proc.returncode}. Detail v logu jobu."
        )

    if proc.stderr:
        tail = proc.stderr.strip().splitlines()[-6:]
        if tail:
            _log(log, "PARAKEET stderr (konec):\n" + "\n".join(tail))
    return proc.stdout or ""


def _extract_json(stdout: str) -> Optional[dict]:
    """parakeet-cli --json tiskne JSON na stdout; vytáhni první {...} blok."""
    stdout = (stdout or "").strip()
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except Exception:
        pass
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(stdout[start:end + 1])
        except Exception:
            return None
    return None


# Vícejazyčný Nemotron vkládá do výstupu lokalizační značky jako <en-US>,
# <cs-CZ> (a může i speciální tokeny <eou> apod.). Do čitelného textu nepatří.
_TAG_RE = re.compile(r"<[^>\s]{1,32}>")


def _is_tag(w: str) -> bool:
    return bool(_TAG_RE.fullmatch((w or "").strip()))


def _clean_text(s: str) -> str:
    s = _TAG_RE.sub(" ", s or "")
    s = re.sub(r"\s+([,.;:!?…])", r"\1", s)   # mezera před interpunkcí pryč
    s = re.sub(r"\s+", " ", s).strip()
    return s


# --- code-switching: automatická detekce anglických slov -----------------
# Anglická "funkční" slova, která NIKDY nevkládat (často se spletou s českou
# výplní a do české věty nepatří).
_EN_STOP = {
    "the", "and", "to", "of", "a", "in", "is", "it", "that", "this", "for",
    "on", "with", "as", "at", "be", "or", "an", "are", "i", "you", "we",
    "he", "she", "they", "my", "me", "do", "no", "so", "up", "by", "if",
    "ok", "yes", "was", "but", "not", "all", "can", "her", "him", "his",
}
_PUNCT = ".,!?…:;\"'()[]„“”‚‘»«"


def _norm_word(w: str) -> str:
    return (w or "").strip().strip(_PUNCT).lower()


def _en_core(w: str) -> str:
    """Anglické slovo bez koncové interpunkce, se zachovanou velikostí písmen."""
    return re.sub(r"[.,!?…:;]+$", "", (w or "").strip())


def _cz_tail(w: str) -> str:
    m = re.search(r"([.,!?…:;]+)$", (w or "").strip())
    return m.group(1) if m else ""


def _should_use_english(cz: str, en: str, cconf: float, econf: float) -> bool:
    cz_n, en_n = _norm_word(cz), _norm_word(en)
    if len(en_n) < config.CODESWITCH_MIN_LEN:
        return False
    if not cz_n or cz_n == en_n:
        return False
    if en_n in _EN_STOP:
        return False
    # anglické slovo musí být čistá latinka bez diakritiky (ne český patvar)
    if not re.fullmatch(r"[a-z][a-z'’.\-]*", en_n):
        return False
    # anglický model musí být dost jistý A výrazně jistější než český
    return (econf >= config.CODESWITCH_EN_MIN_CONF
            and (econf - cconf) >= config.CODESWITCH_CONF_DELTA)


def _merge_codeswitch(cs_words, en_words):
    """Zarovná EN slova k CZ podle časů; kde EN výrazně vyhraje, použije EN pravopis."""
    replaced = 0
    out = []
    for cw in cs_words:
        cs = float(cw.get("start", cw.get("t", 0.0)))
        ce = float(cw.get("end", cs))
        cconf = float(cw.get("conf", 1.0))
        best, best_ov = None, 0.0
        for ew in en_words:
            es = float(ew.get("start", ew.get("t", 0.0)))
            ee = float(ew.get("end", es))
            ov = min(ce, ee) - max(cs, es)       # časový překryv
            if ov > best_ov:
                best_ov, best = ov, ew
        new = dict(cw)
        if best is not None and best_ov > 0:
            cw_text = (cw.get("w") or "").strip()
            ew_text = (best.get("w") or "").strip()
            econf = float(best.get("conf", 0.0))
            if _should_use_english(cw_text, ew_text, cconf, econf):
                new["w"] = _en_core(ew_text) + _cz_tail(cw_text)
                replaced += 1
        out.append(new)
    return out, replaced


# --- slovník oprav (corrections.txt) -------------------------------------
# Opraví opakující se chyby modelu: vlastní jména, značky a anglická slova
# v české větě (model je píše foneticky, např. "porše" -> "Porsche",
# "Inijak" -> "Enyaq"). Soubor se čte při každém přepisu, takže úpravy se
# projeví bez restartu workeru.
def _load_corrections():
    rules = []
    try:
        path = config.CORRECTIONS_FILE
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                wrong, right = line.split("=", 1)
                wrong, right = wrong.strip(), right.strip()
                if wrong:
                    rx = re.compile(r"(?<!\w)" + re.escape(wrong) + r"(?!\w)",
                                    re.IGNORECASE | re.UNICODE)
                    rules.append((rx, right))
    except Exception:
        rules = []
    return rules


def _apply_corrections(text: str, rules) -> str:
    for rx, repl in rules:
        text = rx.sub(repl, text)
    return text


_LANG_LOC = {
    "cs-CZ": "češtině", "en-US": "angličtině", "uk-UA": "ukrajinštině",
    "ru-RU": "ruštině", "auto": None,
}


def _corr_prompt(lang: Optional[str], text: str) -> str:
    loc = _LANG_LOC.get(lang or "auto")
    lang_line = (f"Text je v {loc}; výstup MUSÍ zůstat v {loc} – jazyk NEMĚŇ a NEPŘEKLÁDEJ."
                 if loc else "Zachovej původní jazyk textu, NEPŘEKLÁDEJ.")
    return (
        "Jsi korektor přepisu řeči. " + lang_line + " Oprav v textu dvě věci: "
        "(1) foneticky špatně napsaná cizí slova, značky a vlastní jména na jejich "
        "správný pravopis (např. porše→Porsche, ajfoun→iPhone); "
        "(2) zjevně chybně rozpoznaná slova podle kontextu (např. déka→délka, "
        "sánku→tanku, pádesát→padesát). NEparafrázuj, neměň slovosled ani "
        "interpunkci, neopravuj už správná slova, nic nepřidávej. "
        "Vrať POUZE opravený text.\n\nText:\n" + text
    )


_TRANSLATE_NAMES = {
    "cs-CZ": "češtiny", "en-US": "angličtiny", "uk-UA": "ukrajinštiny",
    "ru-RU": "ruštiny", "de-DE": "němčiny", "sk-SK": "slovenštiny",
    "pl-PL": "polštiny", "es-ES": "španělštiny", "fr-FR": "francouzštiny",
    "it-IT": "italštiny",
}


def llm_translate(text: str, target: str, log: LogFn = None) -> str:
    """Přeloží titulkový řádek do cílového jazyka lokálním LLM (Ollama).
    Bezpečný fallback: při chybě vrátí původní text."""
    text = (text or "").strip()
    if not text:
        return text
    tname = _TRANSLATE_NAMES.get(target, target)
    try:
        import requests
    except Exception:
        return text
    prompt = (f"Přelož následující titulek do {tname}. Zachovej smysl i styl, "
              f"vrať POUZE překlad – žádný komentář, žádné uvozovky.\n\n{text}")
    try:
        r = requests.post(
            config.OLLAMA_URL,
            json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "keep_alive": "10m", "options": {"temperature": 0.1, "num_predict": 512}},
            timeout=config.LLM_TIMEOUT,
        )
        r.raise_for_status()
        out = (r.json().get("response") or "").strip().strip('"').strip("`").strip()
        return out or text
    except Exception:
        return text


def _llm_correct_chunk(text: str, lang: Optional[str] = None) -> str:
    """Jeden blok textu -> Ollama. Vždy bezpečný fallback na původní text."""
    text = (text or "").strip()
    if not config.LLM_CORRECT or len(text) < 3:
        return text
    try:
        import requests
    except Exception:
        return text
    prompt = _corr_prompt(lang, text)
    try:
        r = requests.post(
            config.OLLAMA_URL,
            json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "keep_alive": "10m",  # nech model nahřátý mezi segmenty/joby
                  "options": {"temperature": 0, "num_predict": 1024}},
            timeout=config.LLM_TIMEOUT,
        )
        r.raise_for_status()
        out = (r.json().get("response") or "").strip()
    except Exception:
        return text
    out = out.strip().strip('"').strip("`").strip()
    if not out:
        return text
    # podezřele jiná délka => spíš halucinace/uřknutí, vrať původní
    if len(out) > len(text) * 1.6 + 40 or len(out) < len(text) * 0.5:
        return text
    return out


def _llm_correct_text(text: str, lang: Optional[str] = None) -> str:
    """Opraví celý text (kvůli kontextu); dlouhý rozseká na věty do bloků ~1200 znaků."""
    text = (text or "").strip()
    if not config.LLM_CORRECT or len(text) < 3:
        return text
    if len(text) <= 1200:
        return _llm_correct_chunk(text, lang)
    sents = re.findall(r"[^.!?…]*[.!?…]", text) or [text]
    chunks, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) > 1200:
            chunks.append(cur)
            cur = s
        else:
            cur += s
    if cur:
        chunks.append(cur)
    return " ".join(_llm_correct_chunk(c.strip(), lang) for c in chunks).strip()


def _word_fixes(orig: str, corrected: str) -> dict:
    """Z rozdílu původní/opravený text vytáhne náhrady jednotlivých slov (porše->Porsche)."""
    o, c = orig.split(), corrected.split()
    sm = difflib.SequenceMatcher(a=[w.lower() for w in o], b=[w.lower() for w in c])
    fixes = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace" and (i2 - i1) == (j2 - j1):   # jen 1:1 záměny slov
            for oi, cj in zip(range(i1, i2), range(j1, j2)):
                ow, cw = o[oi].strip(_PUNCT), c[cj].strip(_PUNCT)
                if ow and cw and ow.lower() != cw.lower():
                    fixes[ow.lower()] = cw
    return fixes


def _apply_rules_to_tokens(seg: dict, rules) -> None:
    """Aplikuje regex náhrady na tokeny segmentu; změněné označí 'orig' a srovná text."""
    toks = seg.get("tokens")
    if not toks:
        seg["text"] = _apply_corrections(seg.get("text", ""), rules)
        return
    changed = False
    for t in toks:
        nw = t.get("w", "")
        for rx, repl in rules:
            nw = rx.sub(repl, nw)
        if nw != t.get("w", ""):
            t.setdefault("orig", t.get("w", ""))
            t["w"] = nw
            changed = True
    if changed:
        seg["text"] = _clean_text(" ".join(t.get("w", "") for t in toks))


def _postprocess(result: dict, use_llm: Optional[bool] = None,
                 lang: Optional[str] = None) -> dict:
    use_llm = config.LLM_CORRECT if use_llm is None else use_llm
    segs = result.get("segments", [])

    # 1) rychlý deterministický slovník (corrections.txt), pokud existuje
    rules = _load_corrections()
    if rules:
        result["text"] = _apply_corrections(result.get("text", ""), rules)
        for seg in segs:
            _apply_rules_to_tokens(seg, rules)

    # 2) automatická oprava cizích slov/značek lokálním LLM. Opraví se CELÝ
    #    text (kvůli kontextu), změny se diffem promítnou do tokenů segmentů
    #    (značka 'orig' = původní tvar), takže zůstane zachováno časování.
    if use_llm:
        orig = result.get("text", "")
        corrected = _llm_correct_text(orig, lang)
        if corrected and corrected != orig:
            result["text"] = corrected
            fixes = _word_fixes(orig, corrected)
            if fixes:
                frules = [(re.compile(r"(?<!\w)" + re.escape(k) + r"(?!\w)",
                                      re.IGNORECASE | re.UNICODE), v)
                          for k, v in fixes.items()]
                for seg in segs:
                    _apply_rules_to_tokens(seg, frules)
    # sjednotit zdroj pravdy: TXT (result['text']) ze stejných segmentů jako SRT/VTT/JSON
    if segs:
        joined = " ".join((s.get("text") or "").strip() for s in segs).strip()
        if joined:
            result["text"] = joined
    return result


def _group_words_into_segments(words: List[dict]) -> List[dict]:
    """Slova {w,start,end,conf} -> titulkové segmenty rozumné délky."""
    segments: List[dict] = []
    cur_toks: List[dict] = []
    cur_start: Optional[float] = None
    cur_end: float = 0.0
    last_end: Optional[float] = None

    def flush():
        nonlocal cur_toks, cur_start
        if cur_toks:
            segments.append({
                "start": round(cur_start or 0.0, 3),
                "end": round(cur_end, 3),
                "text": _clean_text(" ".join(t["w"] for t in cur_toks)),
                "tokens": cur_toks,
            })
        cur_toks = []
        cur_start = None

    for word in words:
        w = (word.get("w") or word.get("word") or "").strip()
        if not w or _is_tag(w):
            continue
        start = float(word.get("start", word.get("t", cur_end)))
        end = float(word.get("end", start))
        conf = round(float(word.get("conf", 1.0)), 3)
        gap = (start - last_end) if last_end is not None else 0.0
        candidate_len = len((" ".join([t["w"] for t in cur_toks] + [w])).strip())
        too_long = candidate_len > config.SUBTITLE_MAX_CHARS
        too_far = cur_start is not None and (end - cur_start) > config.SUBTITLE_MAX_DURATION
        big_gap = gap > config.SUBTITLE_MAX_GAP
        if cur_toks and (too_long or too_far or big_gap):
            flush()
        if cur_start is None:
            cur_start = start
        cur_toks.append({"w": w, "conf": conf,
                         "start": round(start, 3), "end": round(end, 3)})
        cur_end = end
        last_end = end
        # zalom po konci věty, pokud je řádek aspoň rozumně dlouhý
        if w.endswith((".", "!", "?", "…")) and \
                len(" ".join(t["w"] for t in cur_toks)) >= config.SUBTITLE_MAX_CHARS // 2:
            flush()
    flush()
    return segments


_SENTENCE_RE = re.compile(r"[^.!?…]+[.!?…]?", re.UNICODE)


def _fallback_segments_from_text(text: str, duration: float) -> List[dict]:
    """
    Fallback bez časů slov: rozdělí text na věty a rozloží je rovnoměrně
    po délce audia (podle počtu znaků věty).
    """
    text = (text or "").strip()
    if not text:
        return []
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
    if not sentences:
        sentences = [text]
    if duration <= 0:
        duration = max(2.0, len(text) / 14.0)   # ~14 znaků/s mluveného slova
    total_chars = sum(len(s) for s in sentences) or 1
    segments: List[dict] = []
    cursor = 0.0
    for s in sentences:
        seg_dur = max(1.0, duration * (len(s) / total_chars))
        segments.append({
            "start": round(cursor, 3),
            "end": round(min(duration, cursor + seg_dur), 3),
            "text": s,
        })
        cursor += seg_dur
    if segments:
        segments[-1]["end"] = round(max(segments[-1]["end"], duration), 3)
    return segments


# ==========================================================================
#  VEŘEJNÉ ROZHRANÍ
# ==========================================================================
def transcribe_file(input_wav_path: str, language: str, job_id: str,
                    duration: float = 0.0, log: LogFn = None,
                    llm_correct: Optional[bool] = None) -> dict:
    """
    Přepíše WAV (16 kHz mono PCM) lokální parakeet.cpp binárkou.

    Vrací:
      {
        "text": "...",
        "segments": [{"start": 0.0, "end": 2.5, "text": "..."}],
        "words": [ ... pokud jsou k dispozici ... ],
        "backend": "parakeet.cpp",
        "model": "<jméno modelu>"
      }

    Vyhazuje ParakeetNotFoundError / ModelNotFoundError / AsrError.
    """
    exe = config.find_parakeet_exe()
    if not exe:
        raise ParakeetNotFoundError(
            "parakeet.cpp (parakeet-cli) nebyl nalezen. Stáhni Windows build z "
            "https://github.com/mudler/parakeet.cpp/releases a dej "
            "parakeet-cli.exe do tools/parakeet/. Viz README."
        )
    model = config.find_model()
    if not model:
        raise ModelNotFoundError(
            "Nenašel jsem žádný .gguf model ve složce models/. Stáhni "
            "nemotron-3.5-asr-streaming-0.6b-*.gguf z "
            "https://huggingface.co/mudler/parakeet-cpp-gguf a dej ho do models/. "
            "Viz README."
        )
    wav = Path(input_wav_path)
    if not wav.is_file():
        raise AsrError(f"Vstupní WAV neexistuje: {wav}")

    _log(log, f"ASR start | model={model.name} | jazyk={language}")
    stdout = _run_parakeet(exe, model, wav, language, log)

    data = _extract_json(stdout)
    if data is not None:
        text = _clean_text(data.get("text") or "")
        words = [w for w in (data.get("words") or [])
                 if not _is_tag(w.get("w") or w.get("word") or "")]

        # Automatická detekce anglických slov: druhý průchod v angličtině
        # a sloučení (jen pokud primární jazyk není přímo angličtina).
        if config.CODESWITCH_EN and words and \
                config.LANGUAGE_MAP.get(language) != "en-US":
            try:
                _log(log, "code-switch: druhý průchod v angličtině…")
                en_data = _extract_json(_run_parakeet(exe, model, wav, "en-US", log)) or {}
                en_words = [w for w in (en_data.get("words") or [])
                            if not _is_tag(w.get("w") or w.get("word") or "")]
                words, replaced = _merge_codeswitch(words, en_words)
                if replaced:
                    text = _clean_text(" ".join((w.get("w") or "") for w in words))
                    _log(log, f"code-switch: použit anglický pravopis u {replaced} slov(a)")
                else:
                    _log(log, "code-switch: žádné anglické slovo nenalezeno")
            except Exception as e:
                _log(log, f"code-switch přeskočen: {e}")

        if words:
            segments = _group_words_into_segments(words)
        else:
            segments = _fallback_segments_from_text(text, duration)
        _log(log, f"ASR hotovo | {len(text)} znaků | {len(segments)} segmentů")
        return _postprocess({
            "text": text,
            "segments": segments,
            "words": words,
            "backend": "parakeet.cpp",
            "model": model.name,
        }, use_llm=llm_correct, lang=language)

    # JSON se nepodařilo načíst -> ber stdout jako čistý text (fallback).
    text = _clean_text(stdout)
    if not text:
        raise AsrError(
            "parakeet.cpp nevrátil žádný text ani JSON. Zkontroluj model a vstup."
        )
    _log(log, "JSON nešel přečíst, používám prostý text + fallback segmentaci.")
    return _postprocess({
        "text": text,
        "segments": _fallback_segments_from_text(text, duration),
        "words": [],
        "backend": "parakeet.cpp",
        "model": model.name,
    }, use_llm=llm_correct, lang=language)


def engine_status() -> dict:
    """Stav ASR backendu pro diagnostiku (/api/status)."""
    exe = config.find_parakeet_exe()
    model = config.find_model()
    return {
        "parakeet_exe": str(exe) if exe else None,
        "parakeet_ok": exe is not None,
        "model_path": str(model) if model else None,
        "model_name": model.name if model else None,
        "model_ok": model is not None,
    }
