// ============================================================
//  PZ AI DAB ALL — Cloudflare relay: sdílené helpery (D1 + R2 + auth).
//  Vystavuje stejný kontrakt jako Forpsi PHP relay (api.php / worker_api.php),
//  aby šel znovupoužít web/app.js i dab_worker.py (jen změna base_url).
// ============================================================

export const SOURCE_LANGS = ['auto', 'cs-CZ', 'en-US', 'uk-UA', 'ru-RU', 'de-DE',
  'pl-PL', 'sk-SK', 'es-ES', 'fr-FR', 'it-IT'];
export const TARGET_LANGS = ['cs-CZ', 'en-US', 'uk-UA', 'de-DE', 'pl-PL',
  'sk-SK', 'es-ES', 'fr-FR', 'it-IT', 'ru-RU'];
export const TTS_ENGINES = ['piper', 'xtts'];
export const AUDIO_MODES = ['replace', 'voiceover', 'subtitles'];
export const SUBS_PRESETS = ['classic', 'reels', 'reels_box', 'word', 'karaoke', 'karaoke_green', 'karaoke_box'];
export const TRANSLATORS = ['local', 'gemma31b', 'google', 'deepl'];
export const ALLOWED_EXT = ['mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v', 'ts', 'mpg', 'mpeg',
  'wav', 'mp3', 'm4a', 'aac', 'flac', 'ogg', 'opus'];
export const VIDEO_EXT = ['mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v', 'ts', 'mpg', 'mpeg'];
export const MAX_UPLOAD_MB = 100;   // "malý" režim: soubor teče přes Worker do R2 (limit 100 MB/request)

// ---------- odpovědi ----------
export function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status, headers: { 'content-type': 'application/json; charset=utf-8', ...headers },
  });
}

// ---------- drobnosti ----------
export function now() { return new Date().toISOString().slice(0, 19).replace('T', ' '); }
export function newId() {
  const b = new Uint8Array(6); crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
}
export function cleanId(id) { return String(id || '').replace(/[^a-f0-9]/g, ''); }
export function cleanExt(e) {
  e = String(e || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  return ALLOWED_EXT.includes(e) ? e : '';
}
export function extOf(name) { const m = /\.([a-z0-9]+)$/i.exec(String(name || '')); return m ? m[1] : ''; }
export function baseName(name) { return String(name || '').split(/[\\/]/).pop() || 'video'; }

// ---------- R2 klíče ----------
export const upKey = (id, ext) => `up/${cleanId(id)}.${ext}`;
export const outKey = (id, suffix) => `out/${cleanId(id)}.${suffix}`;
export const OUT_SUFFIX = { video: 'mp4', audio: 'mp3', srt_tgt: 'srt', srt_src: 'src.srt', seg: 'seg.json' };

// ---------- D1 joby ----------
export async function getJob(env, id) {
  id = cleanId(id);
  if (!id) return null;
  const r = await env.DB.prepare('SELECT data FROM jobs WHERE id=?').bind(id).first();
  return r ? JSON.parse(r.data) : null;
}
export async function saveJob(env, job) {
  await env.DB.prepare(
    `INSERT INTO jobs (id,status,created_at,updated_at,data) VALUES (?,?,?,?,?)
     ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at, data=excluded.data`)
    .bind(job.id, job.status || '', job.created_at || '', job.updated_at || now(), JSON.stringify(job))
    .run();
}
export async function allJobs(env) {
  const r = await env.DB.prepare('SELECT data FROM jobs ORDER BY created_at DESC').all();
  return (r.results || []).map((x) => JSON.parse(x.data));
}
export async function deleteJob(env, id) {
  await env.DB.prepare('DELETE FROM jobs WHERE id=?').bind(cleanId(id)).run();
}

// ---------- R2 helpery ----------
export async function r2Delete(env, key) { try { await env.R2.delete(key); } catch (_) {} }
export async function deleteJobFiles(env, job) {
  const id = cleanId(job.id);
  const ext = cleanExt(job.ext || '');
  const keys = [];
  if (ext) keys.push(upKey(id, ext));
  for (const s of ['mp4', 'mp3', 'src.srt', 'srt', 'seg.json']) keys.push(outKey(id, s));
  await Promise.all(keys.map((k) => r2Delete(env, k)));
}

// ---------- auth (podepsaná cookie) ----------
async function hmacHex(secret, msg) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', enc.encode(secret || 'x'),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(msg));
  return [...new Uint8Array(sig)].map((x) => x.toString(16).padStart(2, '0')).join('');
}
export function timingEq(a, b) {
  a = String(a); b = String(b);
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}
export async function makeSessionCookie(env) {
  const exp = Date.now() + 30 * 864e5;           // 30 dní
  const payload = String(exp);
  const sig = await hmacHex(env.SESSION_SECRET, payload);
  const val = encodeURIComponent(`${btoa(payload)}.${sig}`);
  return `dab_sess=${val}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${30 * 86400}`;
}
export function clearSessionCookie() {
  return 'dab_sess=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0';
}
export async function isAuthed(env, request) {
  const cookie = request.headers.get('cookie') || '';
  const m = /(?:^|;\s*)dab_sess=([^;]+)/.exec(cookie);
  if (!m) return false;
  const raw = decodeURIComponent(m[1]);
  const [b, sig] = raw.split('.');
  if (!b || !sig) return false;
  let payload;
  try { payload = atob(b); } catch (_) { return false; }
  const good = await hmacHex(env.SESSION_SECRET, payload);
  if (!timingEq(sig, good)) return false;
  const exp = parseInt(payload, 10);
  return Number.isFinite(exp) && exp > Date.now();
}
export function checkWorker(env, request) {
  const url = new URL(request.url);
  const tok = request.headers.get('x-worker-token') || url.searchParams.get('token') || '';
  return !!env.WORKER_TOKEN && timingEq(tok, env.WORKER_TOKEN);
}

// ---------- veřejná podoba jobu (pro UI, shodná s lib.php) ----------
export function publicJob(j) {
  const ext = cleanExt(j.ext || '');
  return {
    has_source: !!(ext && (j.size || 0) > 0 && j.status !== 'uploading'),
    id: j.id || '',
    filename: j.filename || '',
    source_lang: j.source_lang || 'auto',
    target_lang: j.target_lang || 'cs-CZ',
    tts_engine: j.tts_engine || 'piper',
    voice: j.voice || '',
    audio_mode: j.audio_mode || 'replace',
    burn_subs: !!j.burn_subs,
    review_text: !!j.review_text,
    llm_correct: j.llm_correct !== false,
    is_video: j.is_video !== false,
    status: j.status || 'pending',
    progress: parseInt(j.progress || 0, 10),
    created_at: j.created_at || '',
    finished_at: j.finished_at || null,
    error: j.error || null,
    duration: j.duration || 0,
    text_preview: j.text_preview || '',
    outputs: j.outputs || {},
    size: parseInt(j.size || 0, 10),
  };
}

// SRT z segmentů (pro export_srt)
export function srtTs(sec) {
  if (sec < 0) sec = 0;
  let ms = Math.round(sec * 1000);
  const h = Math.floor(ms / 3600000); ms %= 3600000;
  const m = Math.floor(ms / 60000); ms %= 60000;
  const s = Math.floor(ms / 1000); ms %= 1000;
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return `${p(h)}:${p(m)}:${p(s)},${p(ms, 3)}`;
}
