<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PZ AI DAB ALL — automatický dabing</title>
<script>!function(){var t=localStorage.getItem('theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t)}();</script>
<link rel="stylesheet" href="style.css?v=2">
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
    <div class="grid">
      <label>Zdrojový jazyk <select id="source_lang"></select></label>
      <label>Cílový jazyk <select id="target_lang"></select></label>
      <label>Hlasový engine
        <select id="tts_engine">
          <option value="piper">Piper — offline (výchozí)</option>
          <option value="xtts">XTTS — kvalitní, klonuje hlas (GPU)</option>
          <option value="voicestudio">PZ Voice Studio — Chatterbox</option>
        </select>
      </label>
      <label>Hlas <span id="voice-hint" class="hint">(volitelné)</span>
        <input id="voice" type="text" list="voices-list" placeholder="prázdné = klon / např. Daisy Studious">
        <datalist id="voices-list">
          <option value="Daisy Studious"><option value="Alison Dietlinde"><option value="Gracie Wise">
          <option value="Alexandra Hisakawa"><option value="Damien Black"><option value="Aaron Dreschner">
          <option value="Baldur Sanjin"><option value="Viktor Eka"><option value="cs_CZ-jirka-medium">
        </datalist>
      </label>
    </div>
    <div class="row">
      <fieldset class="seg">
        <legend>Zvuk originálu</legend>
        <label class="rad"><input type="radio" name="audio_mode" value="replace" checked> Nahradit dabingem</label>
        <label class="rad"><input type="radio" name="audio_mode" value="voiceover"> Voice-over přes ztlumený originál</label>
      </fieldset>
      <div class="checks">
        <label class="chk"><input id="burn_subs" type="checkbox"> Zapéct titulky do videa</label>
        <label id="preset-wrap" class="chk hidden" style="display:block">Styl titulků
          <select id="subs_preset">
            <option value="classic">16:9 klasické (dole)</option>
            <option value="reels">9:16 Reels/Stories — velké tučné (trendy)</option>
            <option value="reels_box">9:16 + podklad (box)</option>
          </select>
        </label>
        <label class="chk"><input id="llm_correct" type="checkbox" checked> LLM korekce přepisu</label>
        <label class="chk"><input id="review_text" type="checkbox"> ✏️ Upravit text před dabingem</label>
      </div>
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
    <div class="modal-head"><b>✏️ Úprava titulků na videu</b><button id="ed-close" class="theme-btn" title="Zavřít">✕</button></div>
    <div class="ed-video-wrap">
      <video id="ed-video" playsinline preload="metadata"></video>
      <div id="ed-overlay" class="ed-overlay" title="Klikni pro úpravu tohoto titulku"></div>
    </div>
    <p class="modal-sub">Pusť video ▶, uprav text v seznamu níže (klik na čas = skok na místo). Náhled titulku se mění živě. Pak dej <b>Dabovat</b>.</p>
    <div id="ed-segs" class="ed-segs"></div>
    <div class="modal-foot">
      <span id="ed-status" class="ed-status"></span>
      <button id="ed-cancel" class="lnk">Zrušit</button>
      <button id="ed-start" class="go">▶ Dabovat</button>
    </div>
  </div>
</div>

<script src="app.js?v=6"></script>
</body>
</html>
