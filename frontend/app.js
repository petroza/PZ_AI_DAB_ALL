"use strict";

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

async function jget(url) { const r = await fetch(url); return r.json(); }

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
  bits.push(s.ollama.ok ? "překlad ✓" : "překlad (offline)");
  bits.push(s.tts.piper.ok ? "Piper ✓" : "Piper ✗");
  if (s.tts.piper.ok && !s.tts.piper.cs_voice) bits.push("(chybí CZ hlas)");
  $("status").innerHTML = bits.map((b) =>
    `<span class="${b.includes('✗') ? 'bad' : 'ok'}">${b}</span>`).join(" · ");
  $("hint").textContent = s.ready
    ? "" : "Některé nástroje chybí — spusť: pip install faster-whisper piper-tts  (nebo viz README).";
}

async function uploadFile(file) {
  $("picked").textContent = "Nahrávám: " + file.name + " …";
  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    if (!r.ok) { $("picked").textContent = "Chyba: " + (await r.text()); return; }
    const data = await r.json();
    currentJob = data.job_id;
    $("picked").textContent = "Připraveno: " + file.name;
    $("start").disabled = false;
  } catch (e) { $("picked").textContent = "Chyba nahrávání: " + e; }
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
  await fetch("/api/dub/" + currentJob, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  currentJob = null;
  $("start").disabled = true;
  $("picked").textContent = "";
  refresh();
}

function dl(id, kind, label) {
  return `<a class="dlbtn" href="/api/download/${id}/${kind}">${label}</a>`;
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
  }
  const err = j.error ? `<div class="err">${j.error}</div>` : "";
  const dir = (LANG[j.source_lang] || j.source_lang) + " → " + (LANG[j.target_lang] || j.target_lang);
  return `<div class="job ${j.status}">
    <div class="jhead">
      <span class="jname" title="${j.filename}">${j.filename}</span>
      <span class="jstat">${st}${running ? " · " + j.progress + "%" : ""}</span>
    </div>
    <div class="jmeta">${dir} · ${j.tts_engine}${j.audio_mode === "voiceover" ? " · voice-over" : ""}</div>
    <div class="bar"><div class="fill" style="width:${j.progress}%"></div></div>
    ${err}
    <div class="jactions">
      ${outs}
      <button class="lnk" onclick="showLog('${j.id}','${j.filename}')">Log</button>
      <button class="lnk del" onclick="delJob('${j.id}')">Smazat</button>
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

async function showLog(id, name) {
  $("logtitle").textContent = "Log · " + name;
  $("logtext").textContent = "…";
  $("logbox").classList.remove("hidden");
  $("logtext").textContent = await (await fetch("/api/jobs/" + id + "/log")).text();
}
async function delJob(id) {
  await fetch("/api/jobs/" + id, { method: "DELETE" });
  refresh();
}
window.showLog = showLog; window.delJob = delJob;

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
$("logclose").addEventListener("click", () => $("logbox").classList.add("hidden"));

loadStatus();
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
