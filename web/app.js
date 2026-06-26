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
    translator:$("translator").value,
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
    outs+='<button class="dlbtn" data-edit="'+esc(j.id)+'" style="border-color:var(--voice);color:var(--voice)">✏️ Upravit titulky / dabovat</button>';
    outs+='<a class="dlbtn" href="'+API+'?action=export_srt&id='+j.id+'">⬇ SRT</a>';
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

// ---- editor titulků na videu (video-centric, jako ElevenLabs) ----
let edId=null, edSegs=[], edLast=null, edEditing=false, edMaxChars=0;
let edChars=0, edLines=2, edSize="", edDirty=false, edEC=16;   // znaků/řádek, řádky, velikost, změny, eff. znaků
const ED_SIZE={small:0.035, medium:0.046, large:0.062, xl:0.08};   // podíl VÝŠKY videa
function fmtTC(s){s=Math.max(0,s||0);const m=Math.floor(s/60),sec=Math.floor(s%60);return (m<10?"0":"")+m+":"+(sec<10?"0":"")+sec;}
function edActive(){const t=$("ed-video").currentTime||0;return edSegs.find(s=>t>=s.start-0.04 && t<s.end);}
// Rozseká segment na KRÁTKÉ titulky (cue) jako se zapéče do videa – stejné
// dělení, ať náhled = výstup (ne zeď textu).
function edSplitCues(seg, perLine, maxLines){
  const text=(seg.text||"").trim(), start=+seg.start, end=+seg.end;
  if(!text || end<=start) return [];
  const maxChars=Math.max(4, perLine*maxLines);
  const words=text.split(/\s+/).filter(Boolean), parts=[]; let cur="";
  for(const w of words){
    const cand=cur?cur+" "+w:w;
    if(cur && cand.length>maxChars){ parts.push(cur); cur=w; } else cur=cand;
    if(cur && ".!?…".includes(cur[cur.length-1]) && cur.length>=Math.min(18,maxChars)){ parts.push(cur); cur=""; }
  }
  if(cur) parts.push(cur);
  const total=parts.reduce((a,p)=>a+p.length,0)||1, dur=end-start, cues=[]; let t=start;
  parts.forEach((p,i)=>{ const ce=(i===parts.length-1)?end:t+dur*(p.length/total); cues.push({start:t,end:ce,text:p}); t=ce; });
  return cues;
}
function edActiveCue(){
  const seg=edActive(); if(!seg) return null;
  const cues=edSplitCues(seg, edEC, edLines), t=$("ed-video").currentTime||0;
  return cues.find(c=>t>=c.start-0.04 && t<c.end) || cues[0] || null;
}
// Ručně zalom cue na PŘESNĚ ≤maxLines řádků (ne přes CSS – to u českých znaků
// zalamuje dřív a dělalo 3 řádky místo 2). Vrátí text s \n.
function edWrapLines(text, perLine, maxLines){
  const words=(text||"").split(/\s+/).filter(Boolean), lines=[]; let cur="";
  for(const w of words){
    const cand=cur?cur+" "+w:w;
    if(cur && cand.length>perLine && lines.length<maxLines-1){ lines.push(cur); cur=w; }
    else cur=cand;
  }
  if(cur) lines.push(cur);
  return lines.join("\n");
}
function edRender(){   // skutečné rozměry zobrazeného videa (kvůli letterboxu)
  const v=$("ed-video");
  const vw=v.videoWidth||16, vh=v.videoHeight||9, cw=v.clientWidth||480, ch=v.clientHeight||270;
  const sc=Math.min(cw/vw, ch/vh)||1;
  return {w:vw*sc, h:vh*sc, cw, ch};
}
function edFitOverlay(){
  // VELIKOST řídí uživatel (podíl výšky videa); znaky/řádek jen ZALAMUJE a nikdy
  // nepřeteče (omezí se na to, co se na šířku vejde).
  const ov=$("ed-overlay"), R=edRender();
  const fs=Math.max(12, Math.round(R.h*(ED_SIZE[edSize]||0.05)));
  ov.style.fontSize=fs+"px";
  const fit=Math.max(6, Math.floor(R.w*0.9/(fs*0.64)));   // konzervativní – řádek se VŽDY vejde
  const ec=edChars>0 ? Math.min(edChars, fit) : fit;
  edEC=ec;                       // efektivní znaků/řádek (pro dělení na cue)
  ov.style.maxWidth=Math.round(R.w*0.95)+"px";   // ruční \n řídí počet řádků, ne CSS
  const padV=Math.max(0,(R.ch-R.h)/2);          // posuň titulky na obsah videa
  ov.style.bottom=(padV + R.h*0.06)+"px";
  const sh=$("ed-srchint"); if(sh) sh.style.top=(padV + R.h*0.035)+"px";
  edMaxChars=ec*edLines;
  edUpdateLen();
}
function edUpdateLen(){
  const cur=edActive(), el=$("ed-len");
  if(!cur){ el.textContent=""; el.classList.remove("warn"); return; }
  const txt=(edEditing?$("ed-overlay").textContent:cur.text)||"";
  const words=(txt.trim().match(/\S+/g)||[]).length;
  const n=edSplitCues({text:txt,start:cur.start,end:cur.end}, edEC, edLines).length;
  el.textContent=words+" slov · "+n+(n===1?" titulek":(n<5?" titulky":" titulků"));
  el.classList.remove("warn");   // rozdělení je normální (náhled ho ukazuje)
}
function edSetLines(n){ edLines=(n===1?1:2); $("ed-line1").classList.toggle("active",edLines===1); $("ed-line2").classList.toggle("active",edLines===2); }
function gotoTime(t){ const v=$("ed-video"); v.pause(); v.currentTime=Math.max(0,t+0.02); edSync(); }
function edNextIdx(){ const t=$("ed-video").currentTime; for(let i=0;i<edSegs.length;i++) if(edSegs[i].start>t+0.15) return i; return -1; }
function edPrevIdx(){ const t=$("ed-video").currentTime; for(let i=edSegs.length-1;i>=0;i--) if(edSegs[i].start<t-0.15) return i; return -1; }
function edTimeline(){
  const v=$("ed-video"), dur=v.duration||0;
  $("ed-time").textContent=fmtTC(v.currentTime)+" / "+fmtTC(dur);
  if(dur>0) $("ed-playhead").style.left=(v.currentTime/dur*100)+"%";
}
function edBuildMarkers(){
  const v=$("ed-video"), dur=v.duration||0, box=$("ed-markers"); box.innerHTML="";
  if(dur<=0) return;
  edSegs.forEach(s=>{
    const el=document.createElement("div"); el.className="ed-marker";
    el.style.left=(s.start/dur*100)+"%"; el.style.width=Math.max(0.6,(s.end-s.start)/dur*100)+"%";
    el.addEventListener("click",ev=>{ ev.stopPropagation(); gotoTime(s.start); });
    box.appendChild(el); s.marker=el;
  });
}
function edSync(){
  const ov=$("ed-overlay"), cur=edActive();
  edTimeline();
  if(cur!==edLast){
    edSegs.forEach(s=>{ if(s.marker) s.marker.classList.toggle("active", s===cur); });
    const idx=cur?edSegs.indexOf(cur):-1;
    $("ed-counter").textContent=edSegs.length?((idx>=0?idx+1:"–")+" / "+edSegs.length):"";
    $("ed-srchint").textContent=cur?(cur.src||""):"";
    edLast=cur;
  }
  if(!edEditing){                                  // náhled = aktuální KRÁTKÝ titulek (jako výstup)
    const cue=edActiveCue();
    ov.textContent=cue?edWrapLines(cue.text, edEC, edLines):"";   // ručně na ≤N řádků
  }
  edUpdateLen();
}
async function openEditor(id){
  edId=id; edSegs=[]; edLast=null; edEditing=false; edDirty=false;
  $("ed-status").textContent=""; $("ed-start").disabled=false; $("ed-play").textContent="▶";
  const ov=$("ed-overlay"); ov.setAttribute("contenteditable","false"); ov.textContent="Načítám…";
  $("ed-srchint").textContent=""; $("ed-counter").textContent="";
  $("ed-markers").innerHTML=""; $("ed-playhead").style.left="0%"; $("ed-time").textContent="0:00 / 0:00";
  $("editmodal").classList.remove("hidden");
  const v=$("ed-video"); v.pause();
  let d; try{ d=await jget(API+"?action=segments&id="+encodeURIComponent(id)); }
  catch{ ov.textContent="Text se nepodařilo načíst."; return; }
  edSegs=(d.segments||[]).map(s=>({start:+s.start, end:+s.end, text:s.text||"", src:s.src||"", marker:null}));
  edChars=+(d.subs_chars||0); edLines=(+d.subs_maxlines===1?1:2); edSize=String(d.subs_size||"");
  $("ed-chars").value=String(edChars); edSetLines(edLines); $("ed-size").value=edSize;
  $("ed-trans").value=d.translator||"local";
  ov.textContent="";
  v.src=API+"?action=stream&id="+encodeURIComponent(id)+"&kind=source"; v.load();
  v.ontimeupdate=edSync; v.onseeked=edSync;
  v.onloadedmetadata=()=>{ edBuildMarkers(); edTimeline(); edFitOverlay(); };
  v.onplay=()=>$("ed-play").textContent="⏸"; v.onpause=()=>$("ed-play").textContent="▶";
  if(v.readyState>=1){ edBuildMarkers(); edTimeline(); }
  edFitOverlay();
  if(edSegs.length) v.currentTime=edSegs[0].start+0.02;
  edSync();
}
function closeEditor(){
  const v=$("ed-video"); v.pause(); v.removeAttribute("src");
  try{ v.load(); }catch(e){}
  $("editmodal").classList.add("hidden"); edId=null; edSegs=[]; edLast=null; edEditing=false; edDirty=false;
}
function edPayload(){
  return edSegs.map(s=>({start:s.start, end:s.end, text:(s.text||"").trim(), src:s.src||""}))
               .filter(s=>s.text && s.end>s.start);
}
async function pushSegments(action){
  $("ed-overlay").blur();
  const segs=edPayload();
  if(!segs.length) throw new Error("Po úpravě nezůstal žádný text");
  const r=await fetch(API+"?action="+action,{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:edId, segments:segs, subs_chars:edChars, subs_maxlines:edLines, subs_size:edSize})});
  const j=await r.json();
  if(j.error) throw new Error(j.error);
  edDirty=false;
  return j;
}
async function approveEdits(){    // ulož + spusť dabing
  if(!edId) return;
  $("ed-start").disabled=true; $("ed-status").textContent="Spouštím dabing…";
  try{ await pushSegments("approve"); closeEditor(); refresh(); schedule(1500); }
  catch(e){ $("ed-status").textContent="Chyba: "+e.message; $("ed-start").disabled=false; }
}
async function saveAndClose(){    // zavřít = uložit (text se pamatuje i bez dabingu)
  if(edId && edDirty){ try{ await pushSegments("save_segments"); }catch(e){} }
  closeEditor();
}
async function downloadSrt(){
  if(!edId) return;
  if(edDirty){ try{ await pushSegments("save_segments"); }catch(e){ $("ed-status").textContent="Uložení selhalo: "+e.message; return; } }
  window.location.href=API+"?action=export_srt&id="+encodeURIComponent(edId);
}
async function reloadEditorSegments(){
  let d; try{ d=await jget(API+"?action=segments&id="+encodeURIComponent(edId)); }catch{ return; }
  edSegs=(d.segments||[]).map(s=>({start:+s.start, end:+s.end, text:s.text||"", src:s.src||"", marker:null}));
  edLast=null; edDirty=false; edBuildMarkers(); edSync();
}
async function retranslate(){
  if(!edId) return;
  const tr=$("ed-trans").value;
  $("ed-retrans").disabled=true; $("ed-status").textContent="Překládám…";
  try{
    const r=await fetch(API+"?action=retranslate",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify({id:edId,translator:tr})});
    const j=await r.json(); if(j.error) throw new Error(j.error);
    if(j.done){ await reloadEditorSegments(); $("ed-status").textContent="Přeloženo ✓ ("+(j.count||edSegs.length)+")"; }
    else { $("ed-status").textContent="Překládám na PC… (chvíli to trvá)"; await pollRetrans(); }
  }catch(e){ $("ed-status").textContent="Chyba: "+e.message; }
  finally{ $("ed-retrans").disabled=false; }
}
async function pollRetrans(){
  let sawProc=false;
  for(let i=0;i<30;i++){                       // ~2 min
    await new Promise(r=>setTimeout(r,4000));
    if(!edId) return;
    let d; try{ d=await jget(API+"?action=segments&id="+encodeURIComponent(edId)); }catch{ continue; }
    if(d.status==="processing") sawProc=true;
    if(sawProc && d.status==="review"){ await reloadEditorSegments(); $("ed-status").textContent="Přeloženo ✓"; return; }
  }
  $("ed-status").textContent="Re-překlad běží na pozadí – za chvíli zavři a otevři editor.";
}
// ovládání: play/pauza, předchozí/další titulek, klik na timeline
$("ed-play").addEventListener("click",()=>{ const v=$("ed-video"); if(v.paused) v.play().catch(()=>{}); else v.pause(); });
$("ed-prev").addEventListener("click",()=>{ const i=edPrevIdx(); if(i>=0) gotoTime(edSegs[i].start); });
$("ed-next").addEventListener("click",()=>{ const i=edNextIdx(); if(i>=0) gotoTime(edSegs[i].start); });
$("ed-track").addEventListener("click",e=>{
  const v=$("ed-video"), dur=v.duration||0; if(dur<=0) return;
  const r=$("ed-track").getBoundingClientRect();
  gotoTime((e.clientX-r.left)/r.width*dur);
});
// volby titulků: znaků/řádek + počet řádků
$("ed-size").addEventListener("change",()=>{ edSize=$("ed-size").value; edDirty=true; edFitOverlay(); edSync(); });
$("ed-chars").addEventListener("change",()=>{ edChars=parseInt($("ed-chars").value)||0; edDirty=true; edFitOverlay(); edSync(); });
$("ed-line1").addEventListener("click",()=>{ edSetLines(1); edDirty=true; edFitOverlay(); edSync(); });
$("ed-line2").addEventListener("click",()=>{ edSetLines(2); edDirty=true; edFitOverlay(); edSync(); });
$("ed-srt").addEventListener("click",downloadSrt);
$("ed-retrans").addEventListener("click",retranslate);
// editace PŘÍMO v obraze (klikni na titulek na videu a piš)
const _ov=$("ed-overlay");
_ov.addEventListener("click",()=>{ const s=edActive(); if(_ov.getAttribute("contenteditable")!=="true" && s){ $("ed-video").pause(); edEditing=true; _ov.textContent=s.text; _ov.setAttribute("contenteditable","true"); _ov.focus(); edUpdateLen(); }});
_ov.addEventListener("focus",()=>{ edEditing=true; $("ed-video").pause(); });
_ov.addEventListener("input",()=>{ const s=edActive(); if(s){ s.text=_ov.textContent; edDirty=true; } edUpdateLen(); });
window.addEventListener("resize",()=>{ if(!$("editmodal").classList.contains("hidden")) edFitOverlay(); });
_ov.addEventListener("blur",()=>{ edEditing=false; _ov.setAttribute("contenteditable","false"); edSync(); });
_ov.addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); _ov.blur(); }});
$("ed-close").addEventListener("click",saveAndClose);
$("ed-cancel").addEventListener("click",saveAndClose);
$("ed-start").addEventListener("click",approveEdits);
$("editmodal").addEventListener("click",e=>{ if(e.target.id==="editmodal") saveAndClose(); });

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
