"use strict";

// --- API base -------------------------------------------------------------
// Worker (Python/FastAPI) běží lokálně na 127.0.0.1:8790. Frontend může být
// servírovaný buď přímo workerem (lokálně) NEBO z Forpsi (appcreate.cloud).
//  • lokálně  → stejný origin (API_BASE = "")
//  • Forpsi   → volá lokální worker (http://127.0.0.1:8790)
// Adresu lze ručně přepsat (uloží se do localStorage) přes tlačítko 🖥️.
function _defaultApiBase() {
  try {
    const saved = localStorage.getItem("dab_api_base");
    if (saved !== null) return saved.replace(/\/+$/, "");
  } catch (_) {}
  const h = location.hostname;
  // Stránka z Forpsi (appcrate.cloud) → volej lokální worker na PC (127.0.0.1).
  // Jinak worker servíruje vlastní frontend (localhost, 127.0.0.1 NEBO LAN IP
  // typu 10.0.1.x při přístupu z mobilu) → stejný origin = volej sám sebe.
  if (h.endsWith("appcrate.cloud") || h.endsWith("appcreate.cloud")) {
    return "http://127.0.0.1:8790";
  }
  return "";
}
let API_BASE = _defaultApiBase();
const api = (p) => API_BASE + p;
function setApiBase(url) {
  API_BASE = (url || "").replace(/\/+$/, "");
  try { localStorage.setItem("dab_api_base", API_BASE); } catch (_) {}
}
function dabSetServer() {
  const cur = API_BASE || location.origin;
  const val = prompt(
    "Adresa workeru PZ AI DAB ALL (běží u tebe na PC).\n" +
    "Prázdné = stejný server jako tahle stránka.",
    cur || "http://127.0.0.1:8790");
  if (val === null) return;
  setApiBase(val.trim());
  loadStatus(); loadVoices(); refresh();
}

function escHtml(s) {
  return (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

const LANG = {
  "auto": "Auto (detekce)", "cs-CZ": "Čeština", "en-US": "Angličtina",
  "uk-UA": "Ukrajinština", "ru-RU": "Ruština", "de-DE": "Němčina",
  "pl-PL": "Polština", "sk-SK": "Slovenština", "es-ES": "Španělština",
  "fr-FR": "Francouzština", "it-IT": "Italština",
};
const STATUS = {
  queued: "Ve frontě", extracting_audio: "Extrakce zvuku", transcribing: "Přepis",
  translating: "Překlad", synthesizing: "Generování hlasu", aligning: "Zarovnání",
  mixing: "Mix stopy", muxing: "Spojování do videa", burning: "Zapékání titulků",
  done: "Hotovo", error: "Chyba", review: "✏️ Čeká na úpravu textu",
};
const $ = (id) => document.getElementById(id);
let currentJob = null;
let _pickedFile = null;   // poslední vybraný soubor (pro detekci rozměrů v editoru titulků)

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

function fillSelect(sel, codes, def) {
  const keep = sel.value && codes.includes(sel.value) ? sel.value : def;
  sel.innerHTML = "";
  codes.forEach((c) => {
    const o = document.createElement("option");
    o.value = c; o.textContent = LANG[c] || c;
    if (c === keep) o.selected = true;
    sel.appendChild(o);
  });
}

async function loadStatus() {
  let s;
  try { s = await jget(api("/api/status")); }
  catch {
    const onForpsi = /appcr?eate\.cloud$/i.test(location.hostname);
    let msg = '<span class="bad">Worker neodpovídá</span>';
    if (onForpsi) {
      msg += ' · Na <b>mobilu</b> tahle adresa nefunguje (prohlížeč nepustí'
           + ' HTTPS stránku na lokální PC). Otevři appku <b>přímo</b> přes'
           + ' <b>http://[IP-tvého-PC]:8790/</b> na stejné WiFi'
           + ' (IP vypíše START.bat). Na PC musí běžet START.bat.';
    } else {
      msg += ' · spusť START.bat na PC' + (API_BASE ? ' (' + escHtml(API_BASE) + ')' : '');
    }
    $("status").textContent = location.hostname === "127.0.0.1" ? "localhost" : location.hostname;
    $("status").classList.add("bad");
    $("status").title = msg.replace(/<[^>]+>/g, "");
    return;
  }
  fillSelect($("source_lang"), s.source_languages, s.defaults.source);
  fillSelect($("target_lang"), s.target_languages, s.defaults.target);
  if (!loadStatus.defaultsApplied) {
    if ($("tts_engine")) $("tts_engine").value = s.tts.default || "xtts";
    const audio = document.querySelector(`input[name=audio_mode][value="${s.defaults.audio_mode || "voiceover"}"]`);
    if (audio) audio.checked = true;
    if ($("burn_subs")) $("burn_subs").checked = s.defaults.burn_subs !== false;
    loadStatus.defaultsApplied = true;
    _syncAudioMode();
    _fillVoiceList();
  }

  const bits = [];
  bits.push(s.ffmpeg.ok ? "ffmpeg ✓" : "ffmpeg ✗");
  if (s.parakeet && s.parakeet.ok) {
    bits.push("parakeet ✓");
  } else if (s.whisper && s.whisper.ok) {
    bits.push(`whisper(${s.whisper.model || "medium"}) ✓`);
  } else {
    bits.push("ASR ✗");
  }
  if (s.ollama && s.ollama.ok) {
    bits.push("překlad ✓");
  } else if (s.argostranslate && s.argostranslate.ok) {
    bits.push(`překlad (offline${s.argostranslate.langs > 0 ? " · " + s.argostranslate.langs + " jazyků" : ""})`);
  } else {
    bits.push("překlad ✗");
  }
  bits.push(s.tts.piper.ok ? "Piper ✓" : "Piper ✗");
  if (s.tts.piper.ok && !s.tts.piper.cs_voice) bits.push("(chybí CZ hlas)");
  if (s.tts.xtts) {
    const loading = s.tts.xtts.ok && /načítá/i.test(s.tts.xtts.info || "");
    bits.push(s.tts.xtts.ok ? (loading ? "XTTS načítá…" : "XTTS(CUDA) ✓") : "XTTS ✗");
  }
  $("status").textContent = location.hostname === "127.0.0.1" ? "localhost" : location.hostname;
  $("status").classList.remove("bad");
  $("status").title = bits.join(" · ");
  $("hint").textContent = s.ready
    ? "" : "Některé nástroje chybí — spusť: pip install faster-whisper piper-tts  (nebo viz README).";
}

let _voicesCache = null;

async function loadVoices() {
  try {
    _voicesCache = await jget(api("/api/voices"));
  } catch (_) { return; }
  _fillVoiceList();
}

function _fillVoiceList() {
  const voiceIn = $("voice");
  if (!voiceIn || !_voicesCache) return;
  const engine = ($("tts_engine") || {}).value || "piper";
  const src = engine === "xtts" ? (_voicesCache.xtts || [])
            : (_voicesCache.piper || []);
  const previous = voiceIn.value;
  const ids = src.map(v => typeof v === "string" ? v : (v.id || v.name || ""))
                 .filter(Boolean);
  voiceIn.innerHTML = "";
  const add = (value, label) => {
    const o = document.createElement("option"); o.value = value;
    o.textContent = label; voiceIn.appendChild(o);
  };
  if (engine === "xtts") {
    add("", "🎙 Klonovat původní hlas (výchozí)");
    const women = new Set(["Daisy Studious", "Alison Dietlinde", "Gracie Wise", "Alexandra Hisakawa"]);
    const men = new Set(["Damien Black", "Aaron Dreschner", "Baldur Sanjin", "Viktor Eka"]);
    const ordered = ["Daisy Studious", "Damien Black", ...ids.filter(id => !["Daisy Studious", "Damien Black"].includes(id))];
    [...new Set(ordered)].forEach(id => {
      const label = id === "Daisy Studious" ? "👩 XTTS ženský hlas — doporučeno"
                  : id === "Damien Black" ? "👨 XTTS mužský hlas — doporučeno"
                  : women.has(id) ? `👩 ${id} — ženský`
                  : men.has(id) ? `👨 ${id} — mužský`
                  : id;
      add(id, label);
    });
    voiceIn.value = ids.includes(previous) ? previous : "";
  } else {
    add("", "Automaticky podle cílového jazyka");
    ids.forEach(id => add(id, id));
    voiceIn.value = ids.includes(previous) ? previous : "";
  }
  const vHint = $("voice-hint");
  if (vHint) {
    vHint.textContent = engine === "xtts"
      ? "klon původního hlasu, nebo stabilní ženský/mužský XTTS hlas"
      : "(volitelné)";
  }
  if (engine === "xtts") return;
  // auto-suggest default voice for target language if voice field is empty
  if (voiceIn && !voiceIn.value.trim()) {
    const tgt = ($("target_lang") || {}).value || "";
    const prefix = tgt.replace("-", "_");   // cs-CZ -> cs_CZ
    const ids = src.map(v => typeof v === "string" ? v : (v.id || v.name || ""))
                   .filter(id => id && id !== "[object Object]");
    const match = ids.find(id => id.toLowerCase().startsWith(prefix.toLowerCase()));
    if (match) voiceIn.value = match;
  }
}

function _syncAudioMode() {
  const selected = document.querySelector('input[name=audio_mode]:checked');
  const subtitlesOnly = selected && selected.value === "subtitles";
  const engine = $("tts_engine"), voice = $("voice"), play = $("voice-play");
  if (engine) engine.disabled = subtitlesOnly;
  if (voice) voice.disabled = subtitlesOnly;
  if (play) play.disabled = subtitlesOnly;
  if ($("burn_subs")) {
    if (subtitlesOnly) $("burn_subs").checked = true;
    $("burn_subs").disabled = subtitlesOnly;
  }
  if ($("start")) $("start").textContent = subtitlesOnly ? "Vytvořit video s českými titulky" : "Spustit dabing";
  _syncSubsOpts();
}

// Vzhled titulků má smysl jen když se zapékají do obrazu — jinak zašedni.
function _syncSubsOpts() {
  const on = $("burn_subs") ? $("burn_subs").checked : true;
  const box = document.querySelector(".subopts");
  if (box) {
    box.style.opacity = on ? "1" : ".45";
    box.querySelectorAll("select").forEach(s => { s.disabled = !on; });
  }
}

function _setPicked(msg, isErr) {
  const el = $("picked");
  el.textContent = msg;
  el.classList.toggle("err", !!isErr);
}

function _upbar(show, pct) {
  const bar = $("upbar"), fill = $("upbar-fill");
  if (bar) bar.classList.toggle("hidden", !show);
  if (fill && pct != null) fill.style.width = pct + "%";
}

function uploadFile(file) {
  // XHR (ne fetch) kvůli sledování průběhu nahrávání → progress bar.
  _pickedFile = file;   // pro detekci orientace/rozlišení v editoru titulků
  _setPicked("Nahrávám: " + file.name + " …", false);
  _upbar(true, 0);
  $("start").disabled = true;
  const fd = new FormData(); fd.append("file", file);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", api("/api/upload"));
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = Math.round(e.loaded / e.total * 100);
      _upbar(true, pct);
      _setPicked("Nahrávám: " + file.name + " … " + pct + "%", false);
    }
  };
  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      try {
        const data = JSON.parse(xhr.responseText);
        currentJob = data.job_id;
        _upbar(true, 100);
        _setPicked("Připraveno: " + file.name, false);
        $("start").disabled = false;
        setTimeout(() => _upbar(false, 0), 900);
      } catch (_) {
        _setPicked("Chyba: neplatná odpověď serveru.", true);
        _upbar(false, 0); $("file").value = "";
      }
    } else {
      _setPicked("Chyba: " + (xhr.responseText || xhr.status), true);
      _upbar(false, 0); $("file").value = "";
    }
  };
  xhr.onerror = () => {
    _setPicked("Chyba nahrávání (spojení / běží worker?).", true);
    _upbar(false, 0); $("file").value = "";
  };
  xhr.send(fd);
}

async function startDub() {
  if (!currentJob) return;
  $("start").disabled = true;
  const body = {
    source_lang: $("source_lang").value,
    target_lang: $("target_lang").value,
    tts_engine: $("tts_engine").value,
    voice: $("voice").value.trim() || null,
    audio_mode: document.querySelector('input[name=audio_mode]:checked').value,
    burn_subs: $("burn_subs").checked,
    llm_correct: $("llm_correct").checked,
    review_text: !!(($("review_text") || {}).checked),
    subs_chars: parseInt($("subs_chars")?.value || "0") || 0,
    subs_maxlines: parseInt($("subs_maxlines")?.value || "2") === 1 ? 1 : 2,
    subs_size: $("subs_size")?.value || "",
  };
  try {
    const res = await fetch(api("/api/dub/" + currentJob), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    currentJob = null;
    $("start").disabled = true;
    _setPicked(res.ok ? "Dabing spuštěn ✓" : "Chyba spuštění", !res.ok);
    setTimeout(() => { if ($("picked").textContent.startsWith("Dabing")) _setPicked("", false); }, 3000);
    $("file").value = "";
    await refresh();
    _scheduleRefresh(1500);
  } catch (e) {
    $("start").disabled = false;
    _setPicked("Chyba spojení: " + e, true);
  }
}

function dl(id, kind, label) {
  return `<a class="dlbtn" href="${api('/api/download/' + id + '/' + kind)}">${label}</a>`;
}

// --- Editor titulků pro zapékání -----------------------------------------
// Režim "new"  = nad právě nahraným vstupním videem (spustí novou zakázku).
// Režim "done" = dodatečné přezapečení do už hotového videa (/api/reburn).
let _edMode = "new";          // "new" | "done"
let _edJobId = null;          // id zakázky (done) nebo currentJob (new)
let _edFilename = "";
let _edW = 1920, _edH = 1080; // detekované rozměry vstupního videa

function _aeUpdatePreview() {
  const canvas = $("ae-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const compW = _edW || 1920, compH = _edH || 1080;
  const posYpct  = parseInt($("ae-posy").value)    || 88;
  const fontSizePx = parseInt($("ae-fontsize").value) || 80;
  const perLine  = parseInt($("ae-perline").value)  || 40;
  const maxLines = parseInt(($("ae-maxlines") || {}).value) || 2;

  // Fit canvas do dostupného prostoru náhledové oblasti (roste s oknem).
  const wrap = canvas.parentElement;
  const MAX_W = Math.max(160, (wrap ? wrap.clientWidth  : 400) - 16);
  const MAX_H = Math.max(120, (wrap ? wrap.clientHeight : 240) - 16);
  const aspect = compH / compW;
  let cW, cH;
  if (MAX_W * aspect <= MAX_H) { cW = MAX_W; cH = Math.round(MAX_W * aspect); }
  else { cH = MAX_H; cW = Math.round(MAX_H / aspect); }
  canvas.width = cW; canvas.height = cH;
  canvas.style.width  = cW + "px";
  canvas.style.height = cH + "px";

  // Background
  ctx.fillStyle = "#0d1117"; ctx.fillRect(0, 0, cW, cH);
  // Subtle grid
  ctx.strokeStyle = "rgba(255,255,255,0.06)"; ctx.lineWidth = 1;
  [0.25,0.5,0.75].forEach(f => {
    ctx.beginPath(); ctx.moveTo(cW*f,0); ctx.lineTo(cW*f,cH); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0,cH*f); ctx.lineTo(cW,cH*f); ctx.stroke();
  });
  // Y position guide line
  const posY = cH * (posYpct / 100);
  ctx.strokeStyle = "rgba(226,162,60,0.55)"; ctx.lineWidth = 1;
  ctx.setLineDash([6,3]);
  ctx.beginPath(); ctx.moveTo(0, posY); ctx.lineTo(cW, posY); ctx.stroke();
  ctx.setLineDash([]);

  // Sample subtitle text — wrap to perLine
  const SAMPLE = "Toto je ukázkový titulek pro náhled pozice";
  const words = SAMPLE.split(" "); const lines = []; let ln = "";
  for (const w of words) {
    const c = ln ? ln + " " + w : w;
    if (c.length <= perLine) { ln = c; } else { if (ln) lines.push(ln); ln = w; }
  }
  if (ln) lines.push(ln);
  const dispLines = lines.slice(0, maxLines);

  const scale = cW / compW;
  const fs = Math.max(9, Math.min(Math.round(fontSizePx * scale), 56));
  const lh = fs * 1.25;
  const totalH = dispLines.length * lh;
  const startY = posY - totalH / 2;

  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.font = `bold ${fs}px sans-serif`;
  dispLines.forEach((l, i) => {
    const y = startY + i * lh + lh / 2;
    ctx.strokeStyle = "#000"; ctx.lineWidth = Math.max(2, fs / 14);
    ctx.strokeText(l, cW / 2, y);
    ctx.fillStyle = "#fff"; ctx.fillText(l, cW / 2, y);
  });

  // Info label
  const orient = compW >= compH ? "na šířku" : "na výšku";
  ctx.font = "10px monospace"; ctx.fillStyle = "rgba(226,162,60,0.75)";
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText(`${compW}×${compH} · ${orient} · ${fontSizePx}px · Y ${posYpct}%`, 7, 5);
}

const _AE_FIELDS = ["ae-fontsize","ae-perline","ae-posy","ae-maxlines"];
function _aeSaveSettings() {
  const obj = {};
  _AE_FIELDS.forEach(id => { if ($(id)) obj[id] = $(id).value; });
  localStorage.setItem("subedit-settings", JSON.stringify(obj));
}
function _aeLoadSettings() {
  try {
    const obj = JSON.parse(localStorage.getItem("subedit-settings")
                        || localStorage.getItem("ae-settings") || "{}");
    _AE_FIELDS.forEach(id => { if ($(id) && obj[id] !== undefined) $(id).value = obj[id]; });
  } catch (_) {}
}

// Detekce rozměrů videa: z lokálního souboru (nová zakázka) nebo z URL
// hotového výstupu (přezapečení). Nastaví _edW/_edH a překreslí náhled.
function _edDetectSize(src) {
  const info = $("ed-detect");
  if (info) info.textContent = "Detekuji video…";
  const v = document.createElement("video");
  v.preload = "metadata"; v.muted = true;
  let url = null, done = false;
  const finish = (w, h, ok) => {
    if (done) return; done = true;
    if (ok && w && h) { _edW = w; _edH = h; }
    if (url) { try { URL.revokeObjectURL(url); } catch (_) {} }
    if (info) {
      info.textContent = ok
        ? `Detekováno: ${_edW}×${_edH} (${_edW >= _edH ? "na šířku" : "na výšku"})`
        : `Rozměry se nepodařilo zjistit — náhled je orientační (${_edW}×${_edH}).`;
    }
    _aeUpdatePreview();
  };
  v.onloadedmetadata = () => finish(v.videoWidth, v.videoHeight, true);
  v.onerror = () => finish(0, 0, false);
  setTimeout(() => finish(0, 0, false), 6000); // pojistka proti zaseknutí
  try {
    if (src instanceof File) { url = URL.createObjectURL(src); v.src = url; }
    else if (typeof src === "string") { v.src = src; }
    else { finish(0, 0, false); }
  } catch (_) { finish(0, 0, false); }
}

// Otevři editor. mode="new" nad nahraným vstupem, "done" nad hotovou zakázkou.
function openBurnEditor(id, filename, mode) {
  _edMode = mode || "new";
  _edJobId = id;
  _edFilename = filename || "";
  _edW = 1920; _edH = 1080;
  $("ae-jobname").textContent = _edFilename || (_edMode === "done" ? "hotová zakázka" : "vstupní video");
  _aeLoadSettings();
  // slidery na hodnoty z číselných polí
  [["ae-fontsize","ae-fontsize-r"],["ae-perline","ae-perline-r"],["ae-posy","ae-posy-r"]]
    .forEach(([n, r]) => { if ($(n) && $(r)) $(r).value = $(n).value; });
  // přepínání tlačítek podle režimu
  const bBurn = $("ed-burn"), bDub = $("ed-burndub"), bRe = $("ed-reburn");
  if (_edMode === "done") {
    if (bBurn) bBurn.classList.add("hidden");
    if (bDub)  bDub.classList.add("hidden");
    if (bRe)   bRe.classList.remove("hidden");
  } else {
    if (bBurn) bBurn.classList.remove("hidden");
    if (bDub)  bDub.classList.remove("hidden");
    if (bRe)   bRe.classList.add("hidden");
  }
  $("aebox").classList.remove("hidden"); $("aebackdrop").classList.remove("hidden");
  _edRestorePlace();
  _aeUpdatePreview();
  // detekce rozměrů
  if (_edMode === "done") { _edDetectSize(api("/api/download/" + id + "/video")); }
  else if (_pickedFile)   { _edDetectSize(_pickedFile); }
  else {
    const info = $("ed-detect");
    if (info) info.textContent = "Nahraj nejdřív video ve Studiu — náhled je zatím orientační.";
  }
}
// zpětná kompatibilita se starým voláním
function showAeModal(id, filename) { openBurnEditor(id, filename, "done"); }

// Posbírej nastavení titulků z editoru do těla požadavku.
function _edSubsPayload() {
  return {
    subs_size_px: parseInt($("ae-fontsize").value) || 0,
    subs_posy:    parseInt($("ae-posy").value) || 0,
    subs_chars:   parseInt($("ae-perline").value) || 0,
    subs_maxlines: (parseInt(($("ae-maxlines") || {}).value) || 2) === 1 ? 1 : 2,
  };
}

// "new" režim: spusť zakázku. dub=false → jen titulky, dub=true → titulky+dabing.
async function _edStartNew(dub) {
  if (!currentJob) { _setPicked("Nejdřív nahraj video ve Studiu.", true); return; }
  _aeSaveSettings();
  let audioMode = document.querySelector('input[name=audio_mode]:checked');
  audioMode = audioMode ? audioMode.value : "voiceover";
  if (!dub) audioMode = "subtitles";                 // jen zapéct titulky
  else if (audioMode === "subtitles") audioMode = "voiceover"; // dub potřebuje hlas
  const body = Object.assign({
    source_lang: $("source_lang").value,
    target_lang: $("target_lang").value,
    tts_engine: $("tts_engine").value,
    voice: $("voice").value.trim() || null,
    audio_mode: audioMode,
    burn_subs: true,
    llm_correct: $("llm_correct").checked,
    subs_size: $("subs_size") ? $("subs_size").value : "",
  }, _edSubsPayload());
  try {
    const res = await fetch(api("/api/dub/" + currentJob), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text().catch(() => res.status));
    currentJob = null; $("file").value = "";
    $("aebox").classList.add("hidden"); $("aebackdrop").classList.add("hidden");
    _setPicked(dub ? "Dabing + zapékání spuštěno ✓" : "Zapékání titulků spuštěno ✓", false);
    setTimeout(() => { if ($("picked").textContent.indexOf("spuštěno") >= 0) _setPicked("", false); }, 3500);
    await refresh(); _scheduleRefresh(1500);
  } catch (e) { _setPicked("Chyba spuštění: " + e, true); }
}

// "done" režim: dodatečné přezapečení titulků do hotového videa.
async function _edReburn() {
  if (!_edJobId) return;
  _aeSaveSettings();
  const btn = $("ed-reburn");
  if (btn) { btn.disabled = true; btn.textContent = "Přezapékám…"; }
  try {
    const res = await fetch(api("/api/reburn/" + _edJobId), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_edSubsPayload()),
    });
    if (!res.ok) throw new Error(await res.text().catch(() => res.status));
    $("aebox").classList.add("hidden"); $("aebackdrop").classList.add("hidden");
    _setPicked("Přezapékání titulků spuštěno ✓", false);
    setTimeout(() => { if ($("picked").textContent.indexOf("spuštěno") >= 0) _setPicked("", false); }, 3500);
    _lastJobsJson = ""; await refresh(); _scheduleRefresh(1500);
  } catch (e) { _setPicked("Chyba přezapékání: " + e, true); }
  finally { if (btn) { btn.disabled = false; btn.textContent = "▶ Přezapéct titulky"; } }
}
function _elapsed(isoStr) {
  if (!isoStr) return "";
  const sec = Math.round((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (sec < 0) return "";
  const m = Math.floor(sec / 60), s = sec % 60;
  return ` · ${m}:${s.toString().padStart(2, "0")}`;
}

function jobCard(j) {
  const st = STATUS[j.status] || j.status;
  const running = !["done", "error", "review"].includes(j.status);
  let outs = "";
  if (j.status === "done") {
    if (j.output_video) outs += `<button class="dlbtn play" data-action="play" data-id="${escHtml(j.id)}" data-fn="${escHtml(j.filename)}"><i data-lucide="play"></i> Přehrát</button>`;
    if (j.output_video) outs += dl(j.id, "video", "⬇ Video");
    if (j.output_audio) outs += dl(j.id, "audio", "⬇ Audio");
    if (j.output_srt_tgt) outs += dl(j.id, "srt_tgt", "⬇ Titulky (cíl)");
    if (j.output_srt_src) outs += dl(j.id, "srt_src", "⬇ Titulky (zdroj)");
    if (j.output_video && j.output_srt_tgt) outs += `<button class="dlbtn" data-action="burnedit" data-id="${escHtml(j.id)}" data-fn="${escHtml(j.filename)}"><i data-lucide="subtitles"></i> Titulky</button>`;
  }
  const err = j.error ? `<div class="err">${escHtml(j.error)}</div>` : "";
  const dir = (LANG[j.source_lang] || j.source_lang) + " → " + (LANG[j.target_lang] || j.target_lang);
  const durStr = j.duration > 0 ? ` · ${Math.round(j.duration)}s` : "";
  const segStr = j.segments_count > 0 ? ` · ${j.segments_count} seg` : "";
  const metaExtra = j.status === "done" ? durStr + segStr : "";
  return `<div class="job ${j.status}">
    <div class="jhead">
      <span class="jname" title="${escHtml(j.filename)}">${escHtml(j.filename)}</span>
      <span class="jstat">${st}${running ? " · " + j.progress + "%" + _elapsed(j.started_at || j.created_at) : ""}</span>
    </div>
    <div class="jmeta">${dir} · ${j.audio_mode === "subtitles" ? "původní hlas · české titulky" : j.tts_engine}${j.audio_mode === "voiceover" ? " · voice-over" : ""}${metaExtra}</div>
    <div class="bar"><div class="fill" style="width:${j.progress}%"></div></div>
    ${err}
    <div class="jactions">
      ${outs}
      ${j.status === "review" ? `<button class="dlbtn" data-action="edit" data-id="${escHtml(j.id)}">✏️ Upravit titulky</button>` : ""}
      <button class="lnk" data-action="log" data-id="${escHtml(j.id)}" data-fn="${escHtml(j.filename)}">Log</button>
      <button class="lnk del" data-action="del" data-id="${escHtml(j.id)}" title="Odebrat ze seznamu (soubory na disku zůstanou)">Smazat</button>
      <button class="lnk del hard" data-action="delfiles" data-id="${escHtml(j.id)}" data-fn="${escHtml(j.filename)}" title="Smazat zakázku i všechny její soubory z disku"><i data-lucide="trash-2"></i> Z disku</button>
    </div>
  </div>`;
}

let _lastJobsJson = "";
let _refreshTimer = null;
let _lastJobs = [];

// --- levé menu: aktivní stav + proklik na sekce ---
function _updateJobsBadge() {
  const badge = $("nav-jobs-count");
  if (!badge) return;
  const n = _lastJobs.length;
  badge.textContent = n;
  badge.classList.toggle("hidden", n === 0);
}

function _setActiveNav(btn) {
  document.querySelectorAll(".side-nav .navi").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
}

function _gotoSection(targetId, btn) {
  _setActiveNav(btn);
  const el = document.getElementById(targetId);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.remove("flash");
  // restart animace
  void el.offsetWidth;
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1200);
}

function _openAeFromNav(btn) {
  _setActiveNav(btn);
  // Nová zakázka: editor nad právě nahraným vstupem.
  if (currentJob) {
    const name = ($("picked").textContent || "").replace(/^Připraveno:\s*/, "").trim();
    openBurnEditor(currentJob, name || "vstupní video", "new");
    return;
  }
  // Jinak nabídni přezapečení poslední hotové zakázky (s videem + titulky).
  const done = _lastJobs.filter(j => j.status === "done" && j.output_video && j.output_srt_tgt);
  if (done.length) {
    const j = done[done.length - 1];
    openBurnEditor(j.id, j.filename, "done");
    return;
  }
  _gotoSection("panel-input", btn);
  _setPicked("Nahraj nejdřív video ve Studiu — pak otevři editor titulků.", true);
}

document.querySelectorAll(".side-nav .navi").forEach(btn => {
  if (btn.id === "server-btn") {
    btn.addEventListener("click", () => _setActiveNav(btn));
    return;
  }
  if (btn.hasAttribute("data-ae")) {
    btn.addEventListener("click", () => _openAeFromNav(btn));
    return;
  }
  const target = btn.getAttribute("data-goto");
  if (target) btn.addEventListener("click", () => _gotoSection(target, btn));
});

async function refresh() {
  let data;
  try { data = await jget(api("/api/jobs")); } catch { return false; }
  const sig = JSON.stringify(data.jobs.map(j => [j.id, j.status, j.progress, j.error, j.duration, j.segments_count]));
  const hasRunning = data.jobs.some(j => !["done", "error", "review"].includes(j.status));
  _lastJobs = data.jobs;
  _updateJobsBadge();
  if (!hasRunning && sig === _lastJobsJson) return false;
  _lastJobsJson = sig;
  const box = $("jobs");
  if (!data.jobs.length) { box.innerHTML = '<p class="empty">Zatím žádné zakázky.</p>'; return false; }
  box.innerHTML = data.jobs.map(jobCard).join("");
  return hasRunning;
}

function _scheduleRefresh(delayMs) {
  clearTimeout(_refreshTimer);
  _refreshTimer = setTimeout(async () => {
    const hasRunning = await refresh();
    _scheduleRefresh(hasRunning ? 1500 : 5000);
  }, delayMs);
}

let _logJobId = null, _logInterval = null;

function _logStop() {
  if (_logInterval) { clearInterval(_logInterval); _logInterval = null; }
}

async function _logFetch(id) {
  try {
    const r = await fetch(api("/api/jobs/" + id + "/log"));
    if (!r.ok) return;
    const txt = await r.text();
    const el = $("logtext");
    const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 40;
    el.textContent = txt;
    if (atBottom) el.scrollTop = el.scrollHeight;
  } catch (_) {}
}

async function _logCheckRunning(id) {
  try {
    const j = await jget(api("/api/jobs/" + id));
    if (["done", "error"].includes(j.status)) {
      _logStop();
      $("logtitle").textContent = $("logtitle").textContent.replace(" ⟳", "");
    }
  } catch (_) {}
}

async function showLog(id, name) {
  _logStop();
  _logJobId = id;
  $("logtitle").textContent = "Log · " + name;
  $("logtext").textContent = "…";
  $("logbox").classList.remove("hidden");
  await _logFetch(id);
  // auto-refresh pokud job stále běží
  try {
    const j = await jget(api("/api/jobs/" + id));
    if (!["done", "error"].includes(j.status)) {
      $("logtitle").textContent = "Log · " + name + " ⟳";
      _logInterval = setInterval(async () => {
        await _logFetch(_logJobId);
        await _logCheckRunning(_logJobId);
      }, 2000);
    }
  } catch (_) {}
}

async function delJob(id) {
  // Jen odebrat ze seznamu — výstupní soubory na disku zůstanou.
  try {
    const r = await fetch(api("/api/jobs/" + id + "?files=false"), { method: "DELETE" });
    if (!r.ok) { alert("Smazání selhalo (" + r.status + ")."); return; }
  } catch (e) { alert("Chyba spojení: " + e); return; }
  _lastJobsJson = ""; refresh();
}

async function delJobFiles(id, fn) {
  if (!confirm("Smazat zakázku „" + (fn || id) + "\" i se všemi soubory z disku?\n" +
               "(Výstupní video, audio i titulky budou nenávratně odstraněny.)")) return;
  try {
    const r = await fetch(api("/api/jobs/" + id + "?files=true"), { method: "DELETE" });
    if (!r.ok) { alert("Smazání selhalo (" + r.status + ")."); return; }
  } catch (e) { alert("Chyba spojení: " + e); return; }
  _lastJobsJson = ""; refresh();
}

function playVideo(id, fn) {
  const box = $("vidbox"), player = $("vidplayer");
  if (!box || !player) return;
  $("vid-name").textContent = fn || "";
  player.src = api("/api/play/" + id);
  box.classList.remove("hidden"); $("vidbackdrop").classList.remove("hidden");
  player.play().catch(() => {});
}

function closeVideo() {
  const box = $("vidbox"), player = $("vidplayer");
  if (player) { try { player.pause(); } catch (_) {} player.removeAttribute("src"); player.load(); }
  if (box) box.classList.add("hidden");
  const bd = $("vidbackdrop"); if (bd) bd.classList.add("hidden");
}

async function clearQueue(scope) {
  scope = scope || "finished";
  const onlyDone = scope === "done";
  if (!confirm(onlyDone
      ? "Smazat dokončené zakázky včetně jejich výstupů (video, titulky)?\n" +
        "(Chybné i rozpracované zůstanou.)"
      : "Smazat všechny hotové a chybné zakázky ze seznamu?\n" +
        "(Rozpracovaná zakázka zůstane.)")) return;
  const btn = $(onlyDone ? "clear-done" : "clear-queue");
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(api("/api/jobs/clear?scope=" + scope), { method: "POST" });
    if (!r.ok) { alert("Promazání selhalo (" + r.status + ")."); return; }
    const d = await r.json();
    _setPicked(`Promazáno: ${d.deleted} zakázek` +
               (d.skipped ? ` · ${d.skipped} běží (ponecháno)` : ""), false);
    setTimeout(() => { if ($("picked").textContent.startsWith("Promazáno")) _setPicked("", false); }, 3000);
  } catch (e) { alert("Chyba spojení: " + e); }
  finally { if (btn) btn.disabled = false; _lastJobsJson = ""; refresh(); }
}

// Delegace kliknutí — žádné inline onclick (XSS safe)
$("jobs").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const { action, id, fn } = btn.dataset;
  if (action === "log") showLog(id, fn);
  else if (action === "play") playVideo(id, fn);
  else if (action === "del") delJob(id);
  else if (action === "delfiles") delJobFiles(id, fn);
  else if (action === "burnedit") openBurnEditor(id, fn, "done");
  else if (action === "edit") openSubEditor(id);
});


// --- editor titulků (fáze "upravit text před dabingem") -------------------
// Job se po analýze zastaví ve stavu "review". Tady se dají opravit texty
// i časy, stáhnout SRT, a teprve pak pustit dabing / zapékání.
let _edId = null, _edSegs = [];

async function openSubEditor(id) {
  try {
    const d = await jget(api("/api/segments/" + id));
    _edId = id; _edSegs = d.segments || [];
  } catch {
    _setPicked("Segmenty se nepodařilo načíst.", true); return;
  }
  $("sub-rows").innerHTML = _edSegs.map((s, i) => `
    <div class="sub-row">
      <div class="sub-time">
        <input type="text" data-i="${i}" data-k="start" value="${(+s.start).toFixed(2)}"> –
        <input type="text" data-i="${i}" data-k="end" value="${(+s.end).toFixed(2)}">
      </div>
      <textarea data-i="${i}" data-k="text" rows="2">${escHtml(s.text || "")}</textarea>
      ${s.src ? `<div class="sub-src" title="originál">${escHtml(s.src)}</div>` : ""}
    </div>`).join("");
  $("sub-count").textContent = _edSegs.length + " titulků";
  $("submodal").classList.remove("hidden");
}

function _edCollect() {
  const rows = $("sub-rows").querySelectorAll("[data-i]");
  const out = _edSegs.map(s => ({ ...s }));
  rows.forEach(el => {
    const i = +el.dataset.i, k = el.dataset.k;
    if (!out[i]) return;
    out[i][k] = (k === "text") ? el.value : parseFloat(el.value.replace(",", "."));
  });
  return out.filter(s => (s.text || "").trim() && s.end > s.start);
}

async function _edSave(startDubbing) {
  const segs = _edCollect();
  if (!segs.length) { _setPicked("Titulky jsou prázdné.", true); return; }
  const btn = startDubbing ? $("sub-go") : $("sub-save");
  btn.disabled = true;
  try {
    const r = await fetch(api("/api/segments/" + _edId), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments: segs, start: !!startDubbing }),
    });
    if (!r.ok) throw new Error(await r.text());
    _edSegs = segs;
    if (startDubbing) { $("submodal").classList.add("hidden"); }
    else { _setPicked("Titulky uložené (" + segs.length + ").", false); }
    _scheduleRefresh(300);
  } catch (e) {
    _setPicked("Uložení selhalo: " + e.message, true);
  } finally { btn.disabled = false; }
}

(function () {
  const s = $("sub-save"), g = $("sub-go"), c = $("sub-close"), d = $("sub-srt");
  if (s) s.addEventListener("click", () => _edSave(false));
  if (g) g.addEventListener("click", () => _edSave(true));
  if (c) c.addEventListener("click", () => $("submodal").classList.add("hidden"));
  if (d) d.addEventListener("click", async () => {
    await _edSave(false);
    window.location = api("/api/download/" + _edId + "/srt_tgt");
  });
})();

// --- události ---
const drop = $("drop"), fileInput = $("file");
drop.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => { if (e.target.files[0]) uploadFile(e.target.files[0]); });
["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]); });
$("start").addEventListener("click", startDub);
(function () { const b = $("burn_subs"); if (b) b.addEventListener("change", _syncSubsOpts); })();
_syncSubsOpts();
(function () {
  const b = $("clear-queue"); if (b) b.addEventListener("click", () => clearQueue("finished"));
  const d = $("clear-done");  if (d) d.addEventListener("click", () => clearQueue("done"));
})();
(function () { const b = $("server-btn"); if (b) b.addEventListener("click", dabSetServer); })();
// Náhled hlasu (XTTS) — přehraje krátkou ukázku vybraného vestavěného hlasu.
(function () {
  const b = $("voice-play");
  if (!b) return;
  let au = null;
  b.addEventListener("click", () => {
    const eng = ($("tts_engine") || {}).value;
    const v = ($("voice").value || "").trim();
    if (eng !== "xtts") { _setPicked("Náhled hlasu je jen pro XTTS.", true); return; }
    if (!v) { _setPicked("Vyber konkrétní hlas (prázdné = klon, nelze přehrát).", true); return; }
    if (au) { try { au.pause(); } catch (_) {} }
    b.textContent = "⏳";
    au = new Audio(api("/api/voice_preview?voice=" + encodeURIComponent(v)));
    au.onended = () => { b.textContent = "🔊"; };
    au.onerror = () => { b.textContent = "🔊"; _setPicked("Náhled selhal (běží XTTS server?).", true); };
    au.play().catch(() => { b.textContent = "🔊"; });
  });
})();
$("tts_engine").addEventListener("change", _fillVoiceList);
document.querySelectorAll('input[name=audio_mode]').forEach(el => el.addEventListener("change", _syncAudioMode));
$("target_lang").addEventListener("change", () => { $("voice").value = ""; _fillVoiceList(); });
$("logclose").addEventListener("click", () => { _logStop(); $("logbox").classList.add("hidden"); });
$("aeclose").addEventListener("click", _edClose);
$("aebackdrop").addEventListener("click", _edClose);
{ const vc = $("vidclose"), vb = $("vidbackdrop");
  if (vc) vc.addEventListener("click", closeVideo);
  if (vb) vb.addEventListener("click", closeVideo); }
function _edClose() { $("aebox").classList.add("hidden"); $("aebackdrop").classList.add("hidden"); }

// --- okno editoru: zvětšování tahem (CSS resize) + posun za lištu + paměť ---
function _edClamp() {
  const box = $("aebox"); if (!box) return;
  const w = box.offsetWidth, h = box.offsetHeight;
  let l = parseFloat(box.style.left) || 0, t = parseFloat(box.style.top) || 0;
  l = Math.min(Math.max(6, l), Math.max(6, window.innerWidth  - w - 6));
  t = Math.min(Math.max(6, t), Math.max(6, window.innerHeight - h - 6));
  box.style.left = l + "px"; box.style.top = t + "px";
}
// Umísti okno: obnov uloženou velikost/pozici, jinak vycentruj. Zruší CSS
// transform-centrování, aby úchyt v rohu i tažení za lištu fungovaly 1:1.
function _edRestorePlace() {
  const box = $("aebox"); if (!box) return;
  box.style.transform = "none";
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("subedit-box") || "null"); } catch (_) {}
  if (saved && saved.w && saved.h) {
    box.style.width  = Math.min(saved.w, window.innerWidth  - 12) + "px";
    box.style.height = Math.min(saved.h, window.innerHeight - 12) + "px";
  }
  const w = box.offsetWidth, h = box.offsetHeight;
  if (saved && typeof saved.l === "number" && typeof saved.t === "number") {
    box.style.left = saved.l + "px"; box.style.top = saved.t + "px";
  } else {
    box.style.left = Math.max(6, (window.innerWidth  - w) / 2) + "px";
    box.style.top  = Math.max(6, (window.innerHeight - h) / 2) + "px";
  }
  _edClamp();
}
function _edSaveBox() {
  const box = $("aebox"); if (!box || box.classList.contains("hidden")) return;
  try {
    localStorage.setItem("subedit-box", JSON.stringify({
      w: box.offsetWidth, h: box.offsetHeight,
      l: parseFloat(box.style.left) || 0, t: parseFloat(box.style.top) || 0,
    }));
  } catch (_) {}
}
(function _edWindowSetup() {
  const box = $("aebox"); if (!box) return;
  // zapamatuj velikost po tažení úchytu (CSS resize)
  if (window.ResizeObserver) {
    let tmr = null;
    new ResizeObserver(() => {
      if (box.classList.contains("hidden")) return;
      _aeUpdatePreview();                       // náhled se přizpůsobí nové šířce
      clearTimeout(tmr); tmr = setTimeout(_edSaveBox, 250);
    }).observe(box);
  }
  // posun okna tahem za horní lištu (mimo tlačítko ✕)
  const bar = box.querySelector(".logbar");
  if (bar) {
    let sx = 0, sy = 0, ox = 0, oy = 0, moving = false;
    bar.addEventListener("mousedown", (e) => {
      if (e.target.closest("button")) return;
      moving = true; box.classList.add("dragging");
      sx = e.clientX; sy = e.clientY;
      ox = parseFloat(box.style.left) || 0; oy = parseFloat(box.style.top) || 0;
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!moving) return;
      box.style.left = (ox + e.clientX - sx) + "px";
      box.style.top  = (oy + e.clientY - sy) + "px";
    });
    window.addEventListener("mouseup", () => {
      if (!moving) return;
      moving = false; box.classList.remove("dragging");
      _edClamp(); _edSaveBox();
    });
  }
  window.addEventListener("resize", () => {
    if (!box.classList.contains("hidden")) _edClamp();
  });
})();
(function () {
  const b = $("ed-burn");    if (b) b.addEventListener("click", () => _edStartNew(false));
  const d = $("ed-burndub"); if (d) d.addEventListener("click", () => _edStartNew(true));
  const r = $("ed-reburn");  if (r) r.addEventListener("click", _edReburn);
})();
// Číselné pole + slider drží stejnou hodnotu (obousměrně).
[["ae-fontsize","ae-fontsize-r"],["ae-perline","ae-perline-r"],["ae-posy","ae-posy-r"]]
  .forEach(([numId, rngId]) => {
    const num = $(numId), rng = $(rngId);
    if (num) num.addEventListener("input", () => {
      if (rng) rng.value = num.value;
      _aeUpdatePreview(); _aeSaveSettings();
    });
    if (rng) rng.addEventListener("input", () => {
      if (num) num.value = rng.value;
      _aeUpdatePreview(); _aeSaveSettings();
    });
  });
{ const ml = $("ae-maxlines");
  if (ml) ml.addEventListener("input", () => { _aeUpdatePreview(); _aeSaveSettings(); }); }

loadStatus();
loadVoices();
_syncAudioMode();
refresh().then(r => _scheduleRefresh(r ? 1500 : 5000));
setInterval(loadStatus, 15000);

// --- přepínač světlý / tmavý režim ---
(function () {
  const root = document.documentElement;
  const btn = $("theme-btn");

  function applyTheme(dark) {
    root.setAttribute("data-theme", dark ? "dark" : "light");
    btn.innerHTML = `<i data-lucide="${dark ? "moon" : "sun"}"></i>`;
    if (window.lucide) window.lucide.createIcons({ attrs: { "stroke-width": 1.75 } });
    btn.title = dark ? "Přepnout na světlý režim" : "Přepnout na tmavý režim";
  }

  const saved = localStorage.getItem("theme");
  const sysDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved ? saved === "dark" : sysDark);

  btn.addEventListener("click", function () {
    const nowDark = root.getAttribute("data-theme") === "dark";
    localStorage.setItem("theme", nowDark ? "light" : "dark");
    applyTheme(!nowDark);
  });
})();
