"use strict";

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
  done: "Hotovo", error: "Chyba",
};
const $ = (id) => document.getElementById(id);
let currentJob = null;

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

function fillSelect(sel, codes, def) {
  sel.innerHTML = "";
  codes.forEach((c) => {
    const o = document.createElement("option");
    o.value = c; o.textContent = LANG[c] || c;
    if (c === def) o.selected = true;
    sel.appendChild(o);
  });
}

async function loadStatus() {
  let s;
  try { s = await jget("/api/status"); }
  catch { $("status").textContent = "Backend neodpovídá."; return; }
  fillSelect($("source_lang"), s.source_languages, s.defaults.source);
  fillSelect($("target_lang"), s.target_languages, s.defaults.target);

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
    bits.push(`překlad (offline${s.argostranslate.langs > 0 ? " ·" + s.argostranslate.langs + " párů" : ""})`);
  } else {
    bits.push("překlad ✗");
  }
  bits.push(s.tts.piper.ok ? "Piper ✓" : "Piper ✗");
  if (s.tts.piper.ok && !s.tts.piper.cs_voice) bits.push("(chybí CZ hlas)");
  $("status").innerHTML = bits.map((b) =>
    `<span class="${b.includes('✗') ? 'bad' : 'ok'}">${b}</span>`).join(" · ");
  $("hint").textContent = s.ready
    ? "" : "Některé nástroje chybí — spusť: pip install faster-whisper piper-tts  (nebo viz README).";
}

let _voicesCache = null;

async function loadVoices() {
  try {
    _voicesCache = await jget("/api/voices");
  } catch (_) { return; }
  _fillVoiceList();
}

function _fillVoiceList() {
  const dl = document.getElementById("voices-list");
  if (!dl || !_voicesCache) return;
  dl.innerHTML = "";
  const engine = ($("tts_engine") || {}).value || "piper";
  const src = engine === "piper" ? (_voicesCache.piper || [])
            : engine.includes("voicestudio") || engine === "voicestudio"
              ? (_voicesCache.voicestudio || [])
            : [...(_voicesCache.piper || []), ...(_voicesCache.voicestudio || [])];
  src.forEach(v => {
    const id = typeof v === "string" ? v : (v.id || v.name || String(v));
    if (!id || id === "[object Object]") return;
    const o = document.createElement("option"); o.value = id; dl.appendChild(o);
  });
}

function _setPicked(msg, isErr) {
  const el = $("picked");
  el.textContent = msg;
  el.classList.toggle("err", !!isErr);
}

async function uploadFile(file) {
  _setPicked("Nahrávám: " + file.name + " …", false);
  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    if (!r.ok) {
      _setPicked("Chyba: " + (await r.text()), true);
      $("file").value = "";
      return;
    }
    const data = await r.json();
    currentJob = data.job_id;
    _setPicked("Připraveno: " + file.name, false);
    $("start").disabled = false;
  } catch (e) {
    _setPicked("Chyba nahrávání: " + e, true);
    $("file").value = "";
  }
}

async function startDub() {
  if (!currentJob) return;
  const body = {
    source_lang: $("source_lang").value,
    target_lang: $("target_lang").value,
    tts_engine: $("tts_engine").value,
    voice: $("voice").value.trim() || null,
    audio_mode: document.querySelector('input[name=audio_mode]:checked').value,
    burn_subs: $("burn_subs").checked,
    llm_correct: $("llm_correct").checked,
  };
  const res = await fetch("/api/dub/" + currentJob, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  currentJob = null;
  $("start").disabled = true;
  _setPicked(res.ok ? "Dabing spuštěn ✓" : "Chyba spuštění", !res.ok);
  setTimeout(() => { if ($("picked").textContent.startsWith("Dabing")) _setPicked("", false); }, 3000);
  $("file").value = "";
  refresh();
}

function dl(id, kind, label) {
  return `<a class="dlbtn" href="/api/download/${id}/${kind}">${label}</a>`;
}

// --- After Effects export -------------------------------------------------
let _aeJobId = null, _aeFilename = "";

function _aeUpdatePreview() {
  const canvas = $("ae-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const [compW, compH] = ($("ae-res").value || "1920x1080").split("x").map(Number);
  const posYpct  = parseInt($("ae-posy").value)    || 88;
  const fontSizePx = parseInt($("ae-fontsize").value) || 80;
  const perLine  = parseInt($("ae-perline").value)  || 40;

  // Fit canvas into 400×240 box
  const MAX_W = 400, MAX_H = 240;
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
  const dispLines = lines.slice(0, 2);

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
  ctx.font = "10px monospace"; ctx.fillStyle = "rgba(226,162,60,0.75)";
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText(`Y: ${posYpct}%  ·  ${compW}×${compH}  ·  ${fontSizePx}px`, 7, 5);
}

const _AE_FIELDS = ["ae-fontsize","ae-perline","ae-posy","ae-res","ae-fps"];
function _aeSaveSettings() {
  const obj = {};
  _AE_FIELDS.forEach(id => { obj[id] = $(id).value; });
  localStorage.setItem("ae-settings", JSON.stringify(obj));
}
function _aeLoadSettings() {
  try {
    const obj = JSON.parse(localStorage.getItem("ae-settings") || "{}");
    _AE_FIELDS.forEach(id => { if (obj[id] !== undefined) $(id).value = obj[id]; });
  } catch (_) {}
}

function showAeModal(id, filename) {
  _aeJobId = id; _aeFilename = filename;
  $("ae-jobname").textContent = filename;
  _aeLoadSettings();
  $("aebox").classList.remove("hidden");
  _aeUpdatePreview();
}
function _aeChunkLines(text, perLine, maxLines) {
  const words = (text || "").trim().split(/\s+/);
  const lines = []; let line = "";
  for (const w of words) {
    const cand = line ? line + " " + w : w;
    if (cand.length <= perLine) { line = cand; }
    else { if (line) lines.push(line); line = w; }
  }
  if (line) lines.push(line);
  if (maxLines > 0) lines.length = Math.min(lines.length, maxLines);
  return lines.join("\r");
}
function _aeBuildJsx(segments, { fontSize, perLine, compW, compH, fps, posYpct }) {
  const subs = segments
    .map(s => ({ t: _aeChunkLines(s.text, perLine, 2),
                 s: +((s.start || 0).toFixed(3)),
                 e: +((s.end   || 0).toFixed(3)) }))
    .filter(s => s.t);
  if (!subs.length) return null;
  const strokeW = Math.max(1, Math.round(fontSize / 40));
  return `// PZ AI DAB ALL — After Effects titulky
// Spusť: File > Scripts > Run Script File…
(function () {
  var SUBS = ${JSON.stringify(subs)};
  var FONT_SIZE = ${fontSize};
  var COMP_W = ${compW}; var COMP_H = ${compH};
  var FPS = ${fps}; var POS_Y_PCT = ${posYpct};
  var comp = app.project.activeItem;
  if (!comp || !(comp instanceof CompItem)) {
    var dur = SUBS[SUBS.length - 1].e + 1;
    comp = app.project.items.addComp("Titulky", COMP_W, COMP_H, 1, dur, FPS);
  }
  app.beginUndoGroup("Přidat titulky — PZ AI DAB ALL");
  for (var i = 0; i < SUBS.length; i++) {
    var sub = SUBS[i]; if (!sub.t) continue;
    var layer = comp.layers.addText(sub.t);
    layer.name = "Titulek " + (i + 1);
    layer.inPoint = sub.s; layer.outPoint = sub.e;
    var src = layer.property("Source Text"); var doc = src.value;
    doc.resetCharStyle();
    doc.fontSize = FONT_SIZE; doc.fillColor = [1,1,1];
    doc.strokeColor = [0,0,0]; doc.strokeWidth = ${strokeW};
    doc.strokeOverFill = false; doc.applyStroke = true;
    doc.justification = ParagraphJustification.CENTER_JUSTIFY;
    src.setValue(doc);
    layer.property("Transform").property("Position")
        .setValue([COMP_W / 2, COMP_H * (POS_Y_PCT / 100)]);
  }
  app.endUndoGroup();
  alert("Hotovo! Přidáno " + SUBS.length + " titulků.");
}());
`;
}
async function _aeDownload() {
  const btn = $("ae-dl");
  btn.disabled = true; btn.textContent = "Načítám titulky…";
  try {
    const r = await fetch("/api/download/" + _aeJobId + "/json");
    if (!r.ok) throw new Error("JSON nedostupný");
    const data = await r.json();
    const segs = data.segments || [];
    if (!segs.length) { alert("Žádné segmenty k exportu."); return; }
    const [compW, compH] = $("ae-res").value.split("x").map(Number);
    const jsx = _aeBuildJsx(segs, {
      fontSize: parseInt($("ae-fontsize").value) || 80,
      perLine:  parseInt($("ae-perline").value)  || 40,
      compW, compH,
      fps:     parseFloat($("ae-fps").value) || 25,
      posYpct: parseInt($("ae-posy").value)  || 88,
    });
    if (!jsx) { alert("Nepodařilo se vygenerovat skript."); return; }
    const blob = new Blob([jsx], { type: "application/octet-stream" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (_aeFilename.replace(/\.[^.]+$/, "") || "titulky") + "_AE.jsx";
    a.click(); URL.revokeObjectURL(a.href);
    $("aebox").classList.add("hidden");
  } catch (e) { alert("Chyba: " + e); }
  finally { btn.disabled = false; btn.textContent = "⬇ Stáhnout .jsx skript pro After Effects"; }
}
function jobCard(j) {
  const st = STATUS[j.status] || j.status;
  const running = !["done", "error"].includes(j.status);
  let outs = "";
  if (j.status === "done") {
    if (j.output_video) outs += dl(j.id, "video", "⬇ Video");
    if (j.output_audio) outs += dl(j.id, "audio", "⬇ Audio");
    if (j.output_srt_tgt) outs += dl(j.id, "srt_tgt", "⬇ Titulky (cíl)");
    if (j.output_srt_src) outs += dl(j.id, "srt_src", "⬇ Titulky (zdroj)");
    if (j.output_json) outs += `<button class="dlbtn" data-action="ae" data-id="${escHtml(j.id)}" data-fn="${escHtml(j.filename)}">⬇ After Effects (.jsx)</button>`;
  }
  const err = j.error ? `<div class="err">${escHtml(j.error)}</div>` : "";
  const dir = (LANG[j.source_lang] || j.source_lang) + " → " + (LANG[j.target_lang] || j.target_lang);
  const durStr = j.duration > 0 ? ` · ${Math.round(j.duration)}s` : "";
  const segStr = j.segments_count > 0 ? ` · ${j.segments_count} seg` : "";
  const metaExtra = j.status === "done" ? durStr + segStr : "";
  return `<div class="job ${j.status}">
    <div class="jhead">
      <span class="jname" title="${escHtml(j.filename)}">${escHtml(j.filename)}</span>
      <span class="jstat">${st}${running ? " · " + j.progress + "%" : ""}</span>
    </div>
    <div class="jmeta">${dir} · ${j.tts_engine}${j.audio_mode === "voiceover" ? " · voice-over" : ""}${metaExtra}</div>
    <div class="bar"><div class="fill" style="width:${j.progress}%"></div></div>
    ${err}
    <div class="jactions">
      ${outs}
      <button class="lnk" data-action="log" data-id="${escHtml(j.id)}" data-fn="${escHtml(j.filename)}">Log</button>
      <button class="lnk del" data-action="del" data-id="${escHtml(j.id)}">Smazat</button>
    </div>
  </div>`;
}

async function refresh() {
  let data;
  try { data = await jget("/api/jobs"); } catch { return; }
  const box = $("jobs");
  if (!data.jobs.length) { box.innerHTML = '<p class="empty">Zatím žádné zakázky.</p>'; return; }
  box.innerHTML = data.jobs.map(jobCard).join("");
}

let _logJobId = null, _logInterval = null;

function _logStop() {
  if (_logInterval) { clearInterval(_logInterval); _logInterval = null; }
}

async function _logFetch(id) {
  try {
    const r = await fetch("/api/jobs/" + id + "/log");
    const txt = await r.text();
    const el = $("logtext");
    const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 40;
    el.textContent = txt;
    if (atBottom) el.scrollTop = el.scrollHeight;
  } catch (_) {}
}

async function _logCheckRunning(id) {
  try {
    const j = await jget("/api/jobs/" + id);
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
    const j = await jget("/api/jobs/" + id);
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
  if (!confirm("Opravdu smazat tuto zakázku?")) return;
  await fetch("/api/jobs/" + id, { method: "DELETE" });
  refresh();
}

// Delegace kliknutí — žádné inline onclick (XSS safe)
$("jobs").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const { action, id, fn } = btn.dataset;
  if (action === "log") showLog(id, fn);
  else if (action === "del") delJob(id);
  else if (action === "ae") showAeModal(id, fn);
});

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
$("tts_engine").addEventListener("change", _fillVoiceList);
$("logclose").addEventListener("click", () => { _logStop(); $("logbox").classList.add("hidden"); });
$("aeclose").addEventListener("click", () => $("aebox").classList.add("hidden"));
$("ae-dl").addEventListener("click", _aeDownload);
["ae-fontsize","ae-perline","ae-posy","ae-res","ae-fps"].forEach(id =>
  $(id).addEventListener("input", () => { _aeUpdatePreview(); _aeSaveSettings(); }));

loadStatus();
loadVoices();
refresh();
setInterval(refresh, 1500);
setInterval(loadStatus, 15000);

// --- přepínač světlý / tmavý režim ---
(function () {
  const root = document.documentElement;
  const btn = $("theme-btn");

  function applyTheme(dark) {
    root.setAttribute("data-theme", dark ? "dark" : "light");
    btn.textContent = dark ? "🌙" : "☀️";
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
