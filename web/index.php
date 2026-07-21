<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PZ AI DAB ALL — automatický dabing</title>
<script>!function(){var t=localStorage.getItem('theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t)}();</script>
<link rel="stylesheet" href="style.css?v=10">
</head>
<body>
<header class="top">
  <div class="brand"><span class="logo">🎙️</span> PZ&nbsp;AI&nbsp;<b>DAB&nbsp;ALL</b></div>
  <div class="top-right">
    <button id="theme-btn" class="theme-btn" title="Přepnout světlý/tmavý režim">🌙</button>
    <div id="status" class="status">Připojuji…</div>
  </div>
</header>

<main>
  <section class="panel">
    <h2>1 · Vstup</h2>
    <div id="drop" class="drop">
      <input id="file" type="file" hidden
             accept="video/*,audio/*,.mp4,.mkv,.mov,.webm,.avi,.m4v,.mp3,.wav,.m4a">
      <p class="drop-main">Vyber video&nbsp;/&nbsp;audio</p>
      <p class="drop-sub">klikni a vyber · MP4, MKV, MOV, WEBM, AVI, MP3, WAV…</p>
      <p id="picked" class="picked"></p>
      <div id="upbar" class="bar hidden" style="margin-top:8px"><div id="upbar-fill" class="fill" style="width:0%"></div></div>
    </div>
  </section>

  <section class="panel">
    <h2>2 · Nastavení dabingu</h2>
    <div class="grid grid-3">
      <label>Zdrojový jazyk <select id="source_lang"></select></label>
      <label>Cílový jazyk <select id="target_lang"></select></label>
      <label>Překladač
        <select id="translator">
          <option value="local">gemma3:12b + glosář (offline)</option>
          <option value="gemma31b">gemma4:31b (nejkvalitnější)</option>
          <option value="google">Google (online)</option>
        </select>
      </label>
    </div>
    <div class="grid grid-voice">
      <label>Hlasový engine
        <select id="tts_engine">
          <option value="piper">Piper — offline</option>
          <option value="xtts" selected>XTTS — klonuje hlas (GPU)</option>
        </select>
      </label>
      <label><span class="cap">Hlas <span id="voice-hint" class="hint">(volitelné)</span></span>
        <select id="voice"></select>
      </label>
    </div>
    <div class="row">
      <fieldset class="seg">
        <legend>Zvuk originálu</legend>
        <label class="rad"><input type="radio" name="audio_mode" value="replace"> Nahradit dabingem</label>
        <label class="rad"><input type="radio" name="audio_mode" value="voiceover" checked> Voice-over přes ztlumený originál</label>
        <label class="rad"><input type="radio" name="audio_mode" value="subtitles"> Ponechat původní hlas, jen české titulky</label>
      </fieldset>
      <fieldset class="seg">
        <legend>Titulky a text</legend>
        <label class="chk"><input id="burn_subs" type="checkbox" checked> Zapéct titulky do videa</label>
        <label id="preset-wrap" class="chk hidden">Styl titulků
          <select id="subs_preset">
            <option value="classic">16:9 klasické (dole)</option>
            <option value="reels">9:16 Reels/Stories — velké tučné</option>
            <option value="reels_box">9:16 + podklad (box)</option>
            <option value="word">⚡ Slovo po slově (velké, trendy)</option>
            <option value="karaoke">🎤 Karaoke — aktivní slovo žlutě</option>
            <option value="karaoke_green">🎤 Karaoke — aktivní slovo zeleně</option>
            <option value="karaoke_box">🎤 Karaoke v boxu (hype)</option>
          </select>
        </label>
        <label class="chk"><input id="llm_correct" type="checkbox" checked> LLM korekce přepisu</label>
        <label class="chk"><input id="review_text" type="checkbox"> ✏️ Upravit text před dabingem</label>
      </fieldset>
    </div>
    <button id="start" class="go" disabled>Nahrát a dabovat</button>
    <p id="hint" class="formhint"></p>
  </section>

  <section class="panel">
    <h2>3 · Zakázky</h2>
    <div id="jobs" class="jobs"><p class="empty">Zatím žádné zakázky.</p></div>
  </section>
</main>

<div id="editmodal" class="modal hidden">
  <div class="modal-box ed-wide">
    <div class="modal-head">
      <b>✏️ Úprava titulků na videu</b>
      <span id="ed-counter" class="ed-counter"></span>
      <button id="ed-close" class="theme-btn" title="Zavřít">✕</button>
    </div>
    <div class="ed-video-wrap">
      <video id="ed-video" playsinline preload="metadata"></video>
      <div id="ed-overlay" class="ed-overlay" contenteditable="false" spellcheck="false" title="Klikni a piš přímo do titulku"></div>
      <div id="ed-srchint" class="ed-srchint"></div>
      <div id="ed-len" class="ed-len"></div>
    </div>
    <div class="ed-timeline">
      <button id="ed-prev" class="ed-play" title="Předchozí titulek">⏮</button>
      <button id="ed-play" class="ed-play" title="Přehrát / pauza">▶</button>
      <button id="ed-next" class="ed-play" title="Další titulek">⏭</button>
      <span id="ed-time" class="ed-time">0:00 / 0:00</span>
      <div id="ed-track" class="ed-track">
        <div id="ed-markers" class="ed-markers"></div>
        <div id="ed-playhead" class="ed-playhead"></div>
      </div>
    </div>
    <div class="ed-controls">
      <label>Velikost
        <select id="ed-size">
          <option value="">Auto</option>
          <option value="small">Malé</option>
          <option value="medium">Střední</option>
          <option value="large">Velké</option>
          <option value="xl">Obří</option>
        </select>
      </label>
      <label>Znaků/řádek
        <select id="ed-chars">
          <option value="0">Auto</option>
          <option value="12">12</option><option value="16">16</option>
          <option value="20">20</option><option value="24">24</option>
          <option value="30">30</option><option value="40">40</option>
        </select>
      </label>
      <span class="ed-lines-toggle">Řádky:
        <button id="ed-line1" class="ed-lbtn" type="button">1</button><button id="ed-line2" class="ed-lbtn active" type="button">2</button>
      </span>
      <button id="ed-srt" class="dlbtn" type="button" title="Stáhnout titulky (SRT) bez dabingu">⬇ SRT</button>
    </div>
    <div class="ed-controls ed-trans-row">
      <label>Překladač
        <select id="ed-trans">
          <option value="local">Lokální gemma3:12b</option>
          <option value="gemma31b">gemma4:31b</option>
          <option value="google">Google (online)</option>
        </select>
      </label>
      <button id="ed-retrans" class="dlbtn" type="button" title="Přeložit text znovu vybraným překladačem">🔄 Přeložit znovu</button>
    </div>
    <p class="modal-sub">Pusť ▶, na chybě zastav, <b>klikni na titulek na videu</b> a přepiš. ⏮ / ⏭ skáče. Vše se <b>ukládá</b> (i bez dabování).</p>
    <div class="modal-foot">
      <span id="ed-status" class="ed-status"></span>
      <button id="ed-cancel" class="lnk">Zavřít</button>
      <button id="ed-start" class="go">▶ Dabovat</button>
    </div>
  </div>
</div>

<div id="redubmodal" class="modal hidden">
  <div class="modal-box" style="width:min(520px,100%)">
    <div class="modal-head">
      <b>🔄 Předabovat s jiným nastavením</b>
      <button id="rd-close" class="theme-btn" title="Zavřít">✕</button>
    </div>
    <div class="aebody">
      <p class="modal-sub" style="padding:0 0 10px">Stejné video, nový dabing — změň hlas, engine nebo režim a spusť znovu (bez nahrávání).</p>
      <div class="grid" style="grid-template-columns:1fr 1fr;gap:12px">
        <label>Hlasový engine
          <select id="rd-engine">
            <option value="piper">Piper — offline</option>
            <option value="xtts" selected>XTTS — klonuje hlas (GPU)</option>
          </select>
        </label>
        <label><span class="cap">Hlas <span id="rd-voice-hint" class="hint"></span></span>
          <select id="rd-voice"></select>
        </label>
        <label>Cílový jazyk <select id="rd-target"></select></label>
        <label>Překladač
          <select id="rd-translator">
            <option value="local">gemma3:12b + glosář</option>
            <option value="gemma31b">gemma4:31b</option>
            <option value="google">Google (online)</option>
          </select>
        </label>
      </div>
      <fieldset class="seg" style="margin-top:12px">
        <legend>Zvuk originálu</legend>
        <label class="rad"><input type="radio" name="rd_audio" value="replace"> Nahradit dabingem</label>
        <label class="rad"><input type="radio" name="rd_audio" value="voiceover" checked> Voice-over přes ztlumený originál</label>
      </fieldset>
      <label class="chk" style="margin-top:10px"><input id="rd-burn" type="checkbox" checked> Zapéct titulky do videa</label>
      <label id="rd-preset-wrap" class="chk hidden">Styl titulků
        <select id="rd-preset">
          <option value="classic">16:9 klasické (dole)</option>
          <option value="reels">9:16 Reels — velké tučné</option>
          <option value="reels_box">9:16 + podklad (box)</option>
          <option value="word">⚡ Slovo po slově</option>
          <option value="karaoke">🎤 Karaoke žlutě</option>
          <option value="karaoke_green">🎤 Karaoke zeleně</option>
          <option value="karaoke_box">🎤 Karaoke v boxu</option>
        </select>
      </label>
    </div>
    <div class="modal-foot">
      <span id="rd-status" class="ed-status"></span>
      <button id="rd-cancel" class="lnk">Zavřít</button>
      <button id="rd-run" class="go">🔄 Spustit znovu</button>
    </div>
  </div>
</div>

<script src="app.js?v=19"></script>
</body>
</html>
