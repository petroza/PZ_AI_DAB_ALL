// ============================================================
//  PZ AI DAB ALL — Cloudflare relay: přihlášení (sdílené heslo).
//  Vše kromě workerského API a login/whoami je za podepsanou cookie.
//  Nepřihlášená navigace dostane login stránku, API → 401.
// ============================================================
import { isAuthed } from './_lib.js';

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  const p = url.pathname;
  const action = url.searchParams.get('action') || '';

  // worker API má vlastní tokenovou ochranu
  if (p === '/worker_api.php') return next();
  // login/logout/whoami musí být dostupné bez cookie
  if (p === '/api.php' && ['login', 'logout', 'whoami'].includes(action)) return next();

  if (await isAuthed(env, request)) return next();

  // nepřihlášeno
  if (p === '/api.php') {
    return new Response(JSON.stringify({ error: 'Nepřihlášeno', login: true }), {
      status: 401, headers: { 'content-type': 'application/json; charset=utf-8' },
    });
  }
  return new Response(LOGIN_HTML, { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } });
}

const LOGIN_HTML = `<!doctype html><html lang="cs"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PZ AI DAB ALL — přihlášení</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1117;
    color:#e8e6f0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
  form{width:min(340px,90vw);padding:26px;border:1px solid #262b36;border-radius:16px;
    background:#161b22;box-shadow:0 20px 60px rgba(0,0,0,.5)}
  h1{margin:0 0 4px;font-size:18px}
  p{margin:0 0 18px;color:#9aa0ab;font-size:13px}
  .logo{font-size:30px}
  input{width:100%;padding:12px 14px;margin-bottom:12px;border:1px solid #2c333f;border-radius:10px;
    background:#0d1117;color:#e8e6f0;font-size:15px}
  button{width:100%;padding:12px;border:0;border-radius:10px;cursor:pointer;font-weight:700;
    background:linear-gradient(135deg,#8b6cf6,#6d4ff0);color:#fff;font-size:15px}
  .err{color:#f0787a;font-size:13px;min-height:18px;margin-top:8px;text-align:center}
</style></head><body>
<form id="f">
  <div class="logo">🎙️</div>
  <h1>PZ&nbsp;AI&nbsp;DAB&nbsp;ALL</h1>
  <p>Zadej heslo pro přístup.</p>
  <input id="p" type="password" placeholder="Heslo" autocomplete="current-password" autofocus>
  <button type="submit">Přihlásit</button>
  <div class="err" id="e"></div>
</form>
<script>
document.getElementById('f').addEventListener('submit', async (ev)=>{
  ev.preventDefault();
  const fd=new FormData(); fd.append('password', document.getElementById('p').value);
  const r=await fetch('/api.php?action=login',{method:'POST',body:fd});
  if(r.ok){ location.href='/'; } else { document.getElementById('e').textContent='Špatné heslo'; }
});
</script></body></html>`;
