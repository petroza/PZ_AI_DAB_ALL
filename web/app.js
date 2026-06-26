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

function jobCard(j){
  const st=STATUS[j.status]||j.status;
  const running=!["done","error"].includes(j.status);
  let outs="";
  if(j.status==="done"){
    const o=j.outputs||{};
    if(o.video) outs+=dlbtn(j.id,"video","⬇ Video");
    if(o.audio) outs+=dlbtn(j.id,"audio","⬇ Audio");
    if(o.srt_tgt) outs+=dlbtn(j.id,"srt_tgt","⬇ Titulky");
    if(o.srt_src) outs+=dlbtn(j.id,"srt_src","⬇ Titulky (zdroj)");
  }
  const err=j.error?'<div class="err">'+esc(j.error)+'</div>':"";
  const dir=(LANG[j.source_lang]||j.source_lang)+" → "+(LANG[j.target_lang]||j.target_lang);
  return '<div class="job '+j.status+'">'
    +'<div class="jhead"><span class="jname">'+esc(j.filename)+'</span>'
    +'<span class="jstat">'+st+(running?" · "+j.progress+"%":"")+'</span></div>'
    +'<div class="jmeta">'+dir+' · '+esc(j.tts_engine)+(j.voice?" · "+esc(j.voice):"")
    +(j.audio_mode==="voiceover"?" · voice-over":"")+'</div>'
    +'<div class="bar"><div class="fill" style="width:'+j.progress+'%"></div></div>'
    +err+'<div class="jactions">'+outs
    +'<button class="lnk del" data-del="'+esc(j.id)+'">Smazat</button></div></div>';
}

let lastSig="", timer=null;
async function refresh(){
  let d; try{ d=await jget(API+"?action=list"); }catch{ return false; }
  const sig=JSON.stringify(d.jobs.map(j=>[j.id,j.status,j.progress]));
  const running=d.jobs.some(j=>!["done","error"].includes(j.status));
  if(!running && sig===lastSig) return false;
  lastSig=sig;
  $("jobs").innerHTML=d.jobs.length?d.jobs.map(jobCard).join(""):'<p class="empty">Zatím žádné zakázky.</p>';
  return running;
}
function schedule(ms){clearTimeout(timer);timer=setTimeout(async()=>{const r=await refresh();schedule(r?2000:6000);},ms);}

async function delJob(id){
  if(!confirm("Smazat tuto zakázku?")) return;
  const fd=new FormData(); fd.append("action","delete"); fd.append("id",id);
  await fetch(API,{method:"POST",body:fd}); refresh();
}

// události
$("drop").addEventListener("click",()=>$("file").click());
$("file").addEventListener("change",e=>{const f=e.target.files[0];if(f){picked=f;setPicked("Vybráno: "+f.name+" ("+(f.size/1048576).toFixed(1)+" MB)",false);$("start").disabled=false;}});
$("start").addEventListener("click",startDub);
$("jobs").addEventListener("click",e=>{const b=e.target.closest("[data-del]");if(b)delJob(b.getAttribute("data-del"));});
$("voice").addEventListener("focus",()=>{const h=$("voice-hint");if(h&&$("tts_engine").value==="xtts")h.textContent="prázdné=klon · Ž: Daisy/Alison/Gracie · M: Damien/Aaron/Baldur";});

(function(){const root=document.documentElement,btn=$("theme-btn");
  function ap(d){root.setAttribute("data-theme",d?"dark":"light");btn.textContent=d?"🌙":"☀️";}
  ap((localStorage.getItem("theme")||"")==="dark"||(!localStorage.getItem("theme")&&matchMedia("(prefers-color-scheme:dark)").matches));
  btn.addEventListener("click",()=>{const d=root.getAttribute("data-theme")==="dark";localStorage.setItem("theme",d?"light":"dark");ap(!d);});
})();

loadStatus();
refresh().then(r=>schedule(r?2000:6000));
