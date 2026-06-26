"use strict";
// PZ AI DAB ALL — web relay klient (mobil i PC). Mluví jen s api.php na Forpsi;
// PC worker si úkoly vyzvedává sám (worker_api.php). Funguje odkudkoliv.

const API = "api.php";
const $ = (id) => document.getElementById(id);
const CHUNK = 4 * 1024 * 1024;  // 4 MB na kus (obchází limit těla na hostingu)
let picked = null;

const LANG = {
  "auto":"Auto (detekce)","cs-CZ":"Čeština","en-US":"Angličtina","uk-UA":"Ukrajinština",
  "ru-RU":"Ruština","de-DE":"Němčina","pl-PL":"Polština","sk-SK":"Slovenština",
  "es-ES":"Španělština","fr-FR":"Francouzština","it-IT":"Italština",
};
const STATUS = {
  uploading:"Nahrávání", pending:"Ve frontě", processing:"Zpracovává se",
  review:"📝 Text k úpravě", approved:"Schváleno → dabuje se",
  done:"Hotovo", error:"Chyba",
};

function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
async function jget(u){const r=await fetch(u);if(!r.ok)throw new Error(r.status);return r.json();}

function fillSel(sel, codes, def){
  sel.innerHTML="";
  codes.forEach(c=>{const o=document.createElement("option");o.value=c;o.textContent=LANG[c]||c;if(c===def)o.selected=true;sel.appendChild(o);});
}

async function loadStatus(){
  let s;
  try{ s=await jget(API+"?action=status"); }
  catch{ $("status").innerHTML='<span class="bad">Server neodpovídá</span>'; return; }
  fillSel($("source_lang"), s.source_langs, "auto");
  fillSel($("target_lang"), s.target_langs, "cs-CZ");
  $("status").innerHTML='<span class="ok">'+esc(s.user||"")+' · připojeno</span>';
}

function setPicked(msg,err){const e=$("picked");e.textContent=msg;e.classList.toggle("err",!!err);}
function upbar(show,pct){const b=$("upbar"),f=$("upbar-fill");if(b)b.classList.toggle("hidden",!show);if(f&&pct!=null)f.style.width=pct+"%";}

async function startDub(){
  if(!picked) return;
  $("start").disabled=true;
  const f=picked;
  const meta={
    filename:f.name,
    source_lang:$("source_lang").value, target_lang:$("target_lang").value,
    tts_engine:$("tts_engine").value, voice:$("voice").value.trim(),
    audio_mode:document.querySelector('input[name=audio_mode]:checked').value,
    burn_subs:$("burn_subs").checked?"1":"0",
    subs_preset:$("subs_preset").value,
    review_text:$("review_text").checked?"1":"0",
    llm_correct:$("llm_correct").checked?"1":"0",
  };
  try{
    // 1) init
    upbar(true,0); setPicked("Nahrávám: "+f.name+" … 0%",false);
    const fd=new FormData(); Object.entries(meta).forEach(([k,v])=>fd.append(k,v));
    fd.append("action","upload_init");
    const init=await (await fetch(API,{method:"POST",body:fd})).json();
    if(!init.id) throw new Error(init.error||"init selhal");
    const id=init.id;
    // 2) chunky
    for(let off=0; off<f.size; off+=CHUNK){
      const part=f.slice(off, Math.min(off+CHUNK, f.size));
      const cfd=new FormData(); cfd.append("action","upload_chunk"); cfd.append("id",id);
      cfd.append("chunk", part, "c");
      const cr=await (await fetch(API,{method:"POST",body:cfd})).json();
      if(cr.error) throw new Error(cr.error);
      const pct=Math.round(Math.min(off+CHUNK,f.size)/f.size*100);
      upbar(true,pct); setPicked("Nahrávám: "+f.name+" … "+pct+"%",false);
    }
    // 3) finish → job pending, worker ho vyzvedne
    const ffd=new FormData(); ffd.append("action","upload_finish"); ffd.append("id",id);
    const fin=await (await fetch(API,{method:"POST",body:ffd})).json();
    if(fin.error) throw new Error(fin.error);
    upbar(true,100); setPicked("Zařazeno do fronty ✓ — dabuje se na PC",false);
    setTimeout(()=>upbar(false,0),900);
    picked=null; $("file").value="";
    refresh(); schedule(1500);
  }catch(e){
    setPicked("Chyba: "+e.message, true); upbar(false,0); $("start").disabled=false;
  }
}

function dlbtn(id,kind,label){return '<a class="dlbtn" href="'+API+'?action=download&id='+id+'&kind='+kind+'">'+label+'</a>';}

let openPlayers={};   // id -> kind (přehrávače otevřené ve frontě, přežijí refresh)
function playerHtml(id,kind){
  const tag=kind==="audio"?"audio":"video";
  return '<'+tag+' controls playsinline preload="metadata" '
    +'style="width:100%;max-height:70vh;margin-top:8px;border-radius:8px;background:#000" '
    +'src="'+API+'?action=stream&id='+encodeURIComponent(id)+'&kind='+kind+'"></'+tag+'>';
}

function jobCard(j){
  const st=STATUS[j.status]||j.status;
  const running=!["done","error"].includes(j.status);
  const showPct=["uploading","pending","processing","approved"].includes(j.status);
  let outs="", playBox="";
  if(j.status==="review"){
    outs+='<button class="dlbtn" data-edit="'+esc(j.id)+'" style="border-color:var(--voice);color:var(--voice)">✏️ Upravit text a spustit dabing</button>';
    if((j.outputs||{}).srt_src) outs+=dlbtn(j.id,"srt_src","⬇ Titulky (zdroj)");
  }
  if(j.status==="done"){
    const o=j.outputs||{};
    const pk=o.video?"video":(o.audio?"audio":null);
    if(pk){
      const open=!!openPlayers[j.id];
      outs+='<button class="dlbtn" data-play="'+esc(j.id)+'" data-kind="'+pk+'">'+(open?"⏸ Skrýt":"▶ Přehrát")+'</button>';
      // box necháváme prázdný – živý <video> doplní klik a refresh() ho zachová
      playBox='<div class="player" id="pl-'+esc(j.id)+'"></div>';
    }
    if(o.video) outs+=dlbtn(j.id,"video","⬇ Video");
    if(o.audio) outs+=dlbtn(j.id,"audio","⬇ Audio");
    if(o.srt_tgt) outs+=dlbtn(j.id,"srt_tgt","⬇ Titulky");
    if(o.srt_src) outs+=dlbtn(j.id,"srt_src","⬇ Titulky (zdroj)");
  }
  const err=j.error?'<div class="err">'+esc(j.error)+'</div>':"";
  const dir=(LANG[j.source_lang]||j.source_lang)+" → "+(LANG[j.target_lang]||j.target_lang);
  return '<div class="job '+j.status+'">'
    +'<div class="jhead"><span class="jname">'+esc(j.filename)+'</span>'
    +'<span class="jstat">'+st+(showPct?" · "+j.progress+"%":"")+'</span></div>'
    +'<div class="jmeta">'+dir+' · '+esc(j.tts_engine)+(j.voice?" · "+esc(j.voice):"")
    +(j.audio_mode==="voiceover"?" · voice-over":"")+'</div>'
    +'<div class="bar"><div class="fill" style="width:'+j.progress+'%"></div></div>'
    +err+'<div class="jactions">'+outs
    +'<button class="lnk del" data-del="'+esc(j.id)+'">Smazat</button></div>'
    +playBox+'</div>';
}

let lastSig="", timer=null;
async function refresh(){
  let d; try{ d=await jget(API+"?action=list"); }catch{ return false; }
  const sig=JSON.stringify(d.jobs.map(j=>[j.id,j.status,j.progress]));
  const running=d.jobs.some(j=>!["done","error"].includes(j.status));
  if(!running && sig===lastSig) return false;
  lastSig=sig;
  // odpoj živé přehrávače, ať je překreslení nezničí (jinak video skáče na 0)
  const keep={};
  Object.keys(openPlayers).forEach(id=>{const b=document.getElementById("pl-"+id);if(b&&b.firstChild)keep[id]=b.firstChild;});
  $("jobs").innerHTML=d.jobs.length?d.jobs.map(jobCard).join(""):'<p class="empty">Zatím žádné zakázky.</p>';
  // vrať stejné <video> nody zpět → plynule pokračují tam, kde byly
  Object.keys(openPlayers).forEach(id=>{const b=document.getElementById("pl-"+id);if(!b)return;if(keep[id])b.appendChild(keep[id]);else b.innerHTML=playerHtml(id,openPlayers[id]);});
  return running;
}
function schedule(ms){clearTimeout(timer);timer=setTimeout(async()=>{const r=await refresh();schedule(r?2000:6000);},ms);}

async function delJob(id){
  if(!confirm("Smazat tuto zakázku?")) return;
  const fd=new FormData(); fd.append("action","delete"); fd.append("id",id);
  await fetch(API,{method:"POST",body:fd}); refresh();
}

// ---- živý editor titulků na videu ----
let edId=null, edSegs=[], edLast=null;
function fmtTC(s){s=Math.max(0,s||0);const m=Math.floor(s/60),sec=Math.floor(s%60);return (m<10?"0":"")+m+":"+(sec<10?"0":"")+sec;}
function edActive(){const t=$("ed-video").currentTime||0;return edSegs.find(s=>t>=s.start-0.04 && t<s.end);}
function edSync(){
  const ov=$("ed-overlay"), cur=edActive();
  if(cur!==edLast){
    edSegs.forEach(s=>s.row.classList.toggle("active", s===cur));
    if(cur) cur.row.scrollIntoView({block:"nearest"});
    edLast=cur;
  }
  ov.textContent=cur?cur.ta.value:"";   // živý náhled (i s rozepsanou opravou)
}
async function openEditor(id){
  edId=id; edSegs=[]; edLast=null;
  const wrap=$("ed-segs"); wrap.innerHTML='<p class="modal-sub">Načítám text…</p>';
  $("ed-status").textContent=""; $("ed-start").disabled=false;
  $("editmodal").classList.remove("hidden");
  const v=$("ed-video"); v.pause(); $("ed-overlay").textContent="";
  let d; try{ d=await jget(API+"?action=segments&id="+encodeURIComponent(id)); }
  catch{ wrap.innerHTML='<p class="modal-sub">Text se nepodařilo načíst (možná se ještě připravuje).</p>'; return; }
  v.src=API+"?action=stream&id="+encodeURIComponent(id)+"&kind=source"; v.load();
  const segs=d.segments||[];
  wrap.innerHTML="";
  segs.forEach(s=>{
    const row=document.createElement("div"); row.className="ed-seg";
    row.innerHTML='<div class="ed-tc">'+fmtTC(s.start)+' → '+fmtTC(s.end)+' ▶</div>'
      +(s.src?'<div class="ed-src">'+esc(s.src)+'</div>':'')
      +'<textarea rows="2"></textarea>';
    const ta=row.querySelector("textarea"); ta.value=s.text||"";
    wrap.appendChild(row);
    const o={start:+s.start, end:+s.end, ta, row};
    edSegs.push(o);
    row.querySelector(".ed-tc").addEventListener("click",()=>{ v.currentTime=o.start+0.02; v.pause(); edSync(); });
    ta.addEventListener("input",()=>{ if(row.classList.contains("active")) $("ed-overlay").textContent=ta.value; });
  });
  if(!segs.length) wrap.innerHTML='<p class="modal-sub">Žádné segmenty.</p>';
  v.ontimeupdate=edSync; v.onseeked=edSync;
}
function closeEditor(){
  const v=$("ed-video"); v.pause(); v.removeAttribute("src");
  try{ v.load(); }catch(e){}
  $("editmodal").classList.add("hidden"); edId=null; edSegs=[]; edLast=null;
}
async function approveEdits(){
  if(!edId) return;
  const segs=edSegs.map(s=>({start:s.start, end:s.end, text:s.ta.value.trim()}))
                   .filter(s=>s.text && s.end>s.start);
  if(!segs.length){ $("ed-status").textContent="Text je prázdný."; return; }
  $("ed-start").disabled=true; $("ed-status").textContent="Spouštím dabing…";
  try{
    const r=await fetch(API+"?action=approve",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id:edId,segments:segs})});
    const j=await r.json();
    if(j.error) throw new Error(j.error);
    closeEditor(); refresh(); schedule(1500);
  }catch(e){ $("ed-status").textContent="Chyba: "+e.message; $("ed-start").disabled=false; }
}
$("ed-close").addEventListener("click",closeEditor);
$("ed-cancel").addEventListener("click",closeEditor);
$("ed-start").addEventListener("click",approveEdits);
$("ed-overlay").addEventListener("click",()=>{ const s=edActive(); if(s){ s.ta.focus(); try{s.ta.select();}catch(e){} } });
$("editmodal").addEventListener("click",e=>{ if(e.target.id==="editmodal") closeEditor(); });

// události
$("drop").addEventListener("click",()=>$("file").click());
$("file").addEventListener("change",e=>{const f=e.target.files[0];if(f){picked=f;setPicked("Vybráno: "+f.name+" ("+(f.size/1048576).toFixed(1)+" MB)",false);$("start").disabled=false;}});
$("start").addEventListener("click",startDub);
$("jobs").addEventListener("click",e=>{
  const del=e.target.closest("[data-del]");
  if(del){ delJob(del.getAttribute("data-del")); return; }
  const ed=e.target.closest("[data-edit]");
  if(ed){ openEditor(ed.getAttribute("data-edit")); return; }
  const pl=e.target.closest("[data-play]");
  if(pl){
    const id=pl.getAttribute("data-play"), kind=pl.getAttribute("data-kind")||"video";
    const box=document.getElementById("pl-"+id);
    if(openPlayers[id]){ delete openPlayers[id]; if(box)box.innerHTML=""; pl.textContent="▶ Přehrát"; }
    else{
      openPlayers[id]=kind;
      if(box){ box.innerHTML=playerHtml(id,kind); const v=box.firstChild; if(v&&v.play)v.play().catch(()=>{}); }
      pl.textContent="⏸ Skrýt";
    }
  }
});
$("voice").addEventListener("focus",()=>{const h=$("voice-hint");if(h&&$("tts_engine").value==="xtts")h.textContent="prázdné=klon · Ž: Daisy/Alison/Gracie · M: Damien/Aaron/Baldur";});
$("burn_subs").addEventListener("change",e=>{$("preset-wrap").classList.toggle("hidden",!e.target.checked);});

(function(){const root=document.documentElement,btn=$("theme-btn");
  function ap(d){root.setAttribute("data-theme",d?"dark":"light");btn.textContent=d?"🌙":"☀️";}
  ap((localStorage.getItem("theme")||"")==="dark"||(!localStorage.getItem("theme")&&matchMedia("(prefers-color-scheme:dark)").matches));
  btn.addEventListener("click",()=>{const d=root.getAttribute("data-theme")==="dark";localStorage.setItem("theme",d?"light":"dark");ap(!d);});
})();

loadStatus();
refresh().then(r=>schedule(r?2000:6000));
