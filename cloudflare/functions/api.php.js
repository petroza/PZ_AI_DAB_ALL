// ============================================================
//  PZ AI DAB ALL — Cloudflare relay: uživatelské API (web/mobil).
//  Route: /api.php  (stejný kontrakt jako Forpsi api.php → app.js beze změny).
//  Auth řeší _middleware.js (podepsaná cookie); login/whoami jsou veřejné.
// ============================================================
import {
  SOURCE_LANGS, TARGET_LANGS, TTS_ENGINES, AUDIO_MODES, SUBS_PRESETS, TRANSLATORS,
  ALLOWED_EXT, VIDEO_EXT, MAX_UPLOAD_MB,
  json, now, newId, cleanId, cleanExt, extOf, baseName,
  upKey, outKey, getJob, saveJob, allJobs, deleteJob, deleteJobFiles,
  publicJob, srtTs, makeSessionCookie, clearSessionCookie, timingEq,
} from './_lib.js';

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  let action = url.searchParams.get('action') || '';

  // Některé akce (upload_*, delete) posílají `action` v těle formuláře, ne v query.
  // Přečteme formData jen jednou a nacacheujeme (tělo nejde číst dvakrát).
  let bodyForm = null;
  if (!action && request.method === 'POST') {
    const ct = request.headers.get('content-type') || '';
    if (ct.includes('form-data') || ct.includes('x-www-form-urlencoded')) {
      bodyForm = await request.formData().catch(() => null);
      if (bodyForm) action = String(bodyForm.get('action') || '');
    }
  }

  try {
    switch (action) {

    case 'login': {
      const form = await readForm(request);
      const pass = String(form.password || '');
      if (!env.UI_PASSWORD || !timingEq(pass, env.UI_PASSWORD)) {
        return json({ error: 'Špatné heslo' }, 403);
      }
      return json({ ok: true }, 200, { 'set-cookie': await makeSessionCookie(env) });
    }
    case 'logout':
      return json({ ok: true }, 200, { 'set-cookie': clearSessionCookie() });
    case 'whoami':
      return json({ ok: true, user: 'PetrZ' });

    case 'status':
      return json({
        ok: true,
        source_langs: SOURCE_LANGS, target_langs: TARGET_LANGS,
        tts_engines: TTS_ENGINES, audio_modes: AUDIO_MODES,
        max_upload_mb: MAX_UPLOAD_MB, user: 'PetrZ',
      });

    // ---------- CHUNKED UPLOAD → R2 multipart ----------
    case 'upload_init': {
      const form = await readForm(request, bodyForm);
      const orig = String(form.filename || '');
      const ext = cleanExt(extOf(orig));
      if (!ext) return json({ error: 'Nepodporovaný formát souboru' }, 400);
      const pick = (v, list, def) => (list.includes(String(v)) ? String(v) : def);
      const id = newId();
      const mpu = await env.R2.createMultipartUpload(upKey(id, ext));
      const job = {
        id, filename: baseName(orig), ext,
        source_lang: pick(form.source_lang, SOURCE_LANGS, 'auto'),
        target_lang: pick(form.target_lang, TARGET_LANGS, 'cs-CZ'),
        tts_engine: pick(form.tts_engine, TTS_ENGINES, 'piper'),
        voice: String(form.voice || '').trim(),
        audio_mode: pick(form.audio_mode, AUDIO_MODES, 'replace'),
        subs_preset: pick(form.subs_preset, SUBS_PRESETS, 'classic'),
        translator: pick(form.translator, TRANSLATORS, 'local'),
        subs_chars: Math.max(0, Math.min(60, parseInt(form.subs_chars || 0, 10) || 0)),
        subs_maxlines: parseInt(form.subs_maxlines || 2, 10) === 1 ? 1 : 2,
        subs_size: ['', 'small', 'medium', 'large', 'xl'].includes(String(form.subs_size || '')) ? String(form.subs_size || '') : '',
        burn_subs: String(form.burn_subs || '0') === '1',
        review_text: String(form.review_text || '0') === '1',
        llm_correct: String(form.llm_correct || '1') === '1',
        is_video: VIDEO_EXT.includes(ext),
        status: 'uploading', progress: 0,
        created_at: now(), updated_at: now(), finished_at: null, error: null,
        duration: 0, text_preview: '', outputs: {}, size: 0,
        _mpu: { key: upKey(id, ext), uploadId: mpu.uploadId, parts: [] },
      };
      await saveJob(env, job);
      return json({ ok: true, id });
    }

    case 'upload_chunk': {
      const form = bodyForm || await request.formData();
      const j = await getJob(env, String(form.get('id') || ''));
      if (!j || j.status !== 'uploading' || !j._mpu) return json({ error: 'Neplatné nahrávání' }, 400);
      const chunk = form.get('chunk');
      if (!chunk || typeof chunk === 'string') return json({ error: 'Chybí část souboru' }, 400);
      const buf = await chunk.arrayBuffer();
      if ((j.size || 0) + buf.byteLength > MAX_UPLOAD_MB * 1024 * 1024)
        return json({ error: `Soubor je příliš velký (limit ${MAX_UPLOAD_MB} MB)` }, 400);
      const mpu = env.R2.resumeMultipartUpload(j._mpu.key, j._mpu.uploadId);
      const partNumber = j._mpu.parts.length + 1;
      const up = await mpu.uploadPart(partNumber, buf);
      j._mpu.parts.push({ partNumber, etag: up.etag });
      j.size = (j.size || 0) + buf.byteLength;
      j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true, received: j.size });
    }

    case 'upload_finish': {
      const form = await readForm(request, bodyForm);
      const j = await getJob(env, String(form.id || ''));
      if (!j || j.status !== 'uploading' || !j._mpu) return json({ error: 'Neplatné nahrávání' }, 400);
      if (!j._mpu.parts.length) return json({ error: 'Žádná data nenahrána' }, 400);
      const mpu = env.R2.resumeMultipartUpload(j._mpu.key, j._mpu.uploadId);
      await mpu.complete(j._mpu.parts.map((p) => ({ partNumber: p.partNumber, etag: p.etag })));
      delete j._mpu;
      j.status = 'pending';
      j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true, job: publicJob(j) });
    }

    // ---------- LIST / DETAIL / DELETE ----------
    case 'list':
      return json({ jobs: (await allJobs(env)).map(publicJob) });

    case 'job': {
      const j = await getJob(env, url.searchParams.get('id') || '');
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      return json(publicJob(j));
    }

    case 'delete': {
      const form = await readForm(request, bodyForm);
      const j = await getJob(env, String(form.id || ''));
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      // rozdělaný multipart upload uklidit
      if (j._mpu) { try { env.R2.resumeMultipartUpload(j._mpu.key, j._mpu.uploadId).abort(); } catch (_) {} }
      await deleteJobFiles(env, j);
      await deleteJob(env, j.id);
      return json({ ok: true, deleted: j.id });
    }

    // ---------- REVIEW: úprava textu ----------
    case 'segments': {
      const j = await getJob(env, url.searchParams.get('id') || '');
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      const obj = await env.R2.get(outKey(j.id, 'seg.json'));
      if (!obj) return json({ error: 'Segmenty zatím nejsou připravené' }, 404);
      const d = JSON.parse(await obj.text());
      return json({
        id: j.id, status: j.status || '',
        subs_chars: parseInt(j.subs_chars || 0, 10),
        subs_maxlines: parseInt(j.subs_maxlines || 2, 10),
        subs_size: String(j.subs_size || ''),
        translator: String(j.translator || 'local'),
        segments: Array.isArray(d.segments) ? d.segments : [],
      });
    }

    case 'approve':
    case 'save_segments': {
      const inp = await readJson(request);
      const j = await getJob(env, String(inp.id || ''));
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      if (!['review', 'approved', 'error'].includes(j.status))
        return json({ error: 'Tento job není ve stavu k úpravě' }, 400);
      const segs = inp.segments;
      if (!Array.isArray(segs) || !segs.length) return json({ error: 'Chybí segmenty' }, 400);
      if (segs.length > 20000) return json({ error: 'Příliš mnoho segmentů' }, 400);
      const clean = [];
      for (const s of segs) {
        const txt = String(s.text || '').trim();
        const st = parseFloat(s.start || 0), en = parseFloat(s.end || 0);
        if (!txt || en <= st) continue;
        const row = { start: st, end: en, text: txt.slice(0, 2000) };
        if (s.src != null) row.src = String(s.src).slice(0, 2000);
        clean.push(row);
      }
      if (!clean.length) return json({ error: 'Po úpravě nezůstal žádný text' }, 400);
      await env.R2.put(outKey(j.id, 'seg.json'), JSON.stringify({ segments: clean }));
      if (inp.subs_chars != null) j.subs_chars = Math.max(0, Math.min(60, parseInt(inp.subs_chars, 10) || 0));
      if (inp.subs_maxlines != null) j.subs_maxlines = parseInt(inp.subs_maxlines, 10) === 1 ? 1 : 2;
      if (inp.subs_size != null) j.subs_size = ['small', 'medium', 'large', 'xl'].includes(String(inp.subs_size)) ? String(inp.subs_size) : '';
      if (action === 'approve') { j.status = 'approved'; j.progress = 50; } else { j.status = 'review'; }
      j.error = null; j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true, count: clean.length, mode: action });
    }

    case 'export_srt': {
      const j = await getJob(env, url.searchParams.get('id') || '');
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      const obj = await env.R2.get(outKey(j.id, 'seg.json'));
      if (!obj) return json({ error: 'Segmenty nejsou připravené' }, 404);
      const d = JSON.parse(await obj.text());
      const segs = Array.isArray(d.segments) ? d.segments : [];
      let srt = '', i = 1;
      for (const s of segs) {
        const txt = String(s.text || '').trim(); if (!txt) continue;
        srt += `${i++}\r\n${srtTs(parseFloat(s.start || 0))} --> ${srtTs(parseFloat(s.end || 0))}\r\n${txt}\r\n\r\n`;
      }
      const base = (baseName(j.filename || 'titulky').replace(/\.[^.]+$/, '')) || 'titulky';
      return new Response(srt, { headers: {
        'content-type': 'application/x-subrip; charset=utf-8',
        'content-disposition': `attachment; filename="${base}.cs.srt"`,
      } });
    }

    case 'retranslate': {
      const inp = await readJson(request);
      const j = await getJob(env, String(inp.id || ''));
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      if (!['review', 'error'].includes(j.status)) return json({ error: 'Job není ve stavu k překladu' }, 400);
      let tr = String(inp.translator || 'local');
      if (!['local', 'gemma31b', 'google'].includes(tr)) tr = 'local';
      const segKey = outKey(j.id, 'seg.json');
      const obj = await env.R2.get(segKey);
      if (!obj) return json({ error: 'Segmenty nejsou připravené' }, 404);
      j.translator = tr;
      if (tr === 'google') {
        const d = JSON.parse(await obj.text());
        const segs = Array.isArray(d.segments) ? d.segments : [];
        let sl = String(j.source_lang || 'auto').slice(0, 2).toLowerCase();
        if (sl === 'au' || sl === '') sl = 'auto';
        const tl = String(j.target_lang || 'cs').slice(0, 2).toLowerCase() || 'cs';
        let ok = 0;
        for (const s of segs) {
          const src = String(s.src || s.text || '').trim();
          if (!src) continue;
          const out = await googleTranslate(src, sl, tl);
          if (out) { s.text = out.slice(0, 2000); ok++; }
        }
        if (!ok) return json({ error: 'Google překlad se nezdařil (síť?)' }, 502);
        await env.R2.put(segKey, JSON.stringify({ segments: segs }));
        j.updated_at = now(); await saveJob(env, j);
        return json({ ok: true, done: true, count: ok });
      }
      // lokální / gemma31b → udělá worker (má Ollamu)
      j.retranslate = true; j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true, done: false, queued: true });
    }

    case 'reburn': {
      const inp = await readJson(request);
      const j = await getJob(env, String(inp.id || ''));
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      if (j.status !== 'done') return json({ error: 'Zapéct titulky lze jen u hotové zakázky' }, 400);
      if (!(j.outputs || {}).video) return json({ error: 'Tato zakázka nemá video' }, 400);
      const srt = await env.R2.head(outKey(j.id, 'srt'));
      if (!srt) return json({ error: 'K této zakázce nejsou titulky (SRT)' }, 400);
      let preset = String(inp.subs_preset || j.subs_preset || 'classic');
      if (!SUBS_PRESETS.includes(preset)) preset = 'classic';
      j.subs_preset = preset;
      if (inp.subs_size != null) j.subs_size = ['small', 'medium', 'large', 'xl'].includes(String(inp.subs_size)) ? String(inp.subs_size) : '';
      j.reburn = true; j.status = 'processing'; j.progress = 90; j.stage = 'burning';
      j.error = null; j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true, queued: true });
    }

    case 'redub': {
      const inp = await readJson(request);
      const j = await getJob(env, String(inp.id || ''));
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      if (j.status !== 'done') return json({ error: 'Předabovat lze jen hotovou zakázku' }, 400);
      const ext = cleanExt(j.ext || '');
      if (!ext || !(await env.R2.head(upKey(j.id, ext))))
        return json({ error: 'Zdrojové video už není k dispozici – nahraj ho prosím znovu' }, 400);
      if (TTS_ENGINES.includes(String(inp.tts_engine))) j.tts_engine = String(inp.tts_engine);
      if (inp.voice != null) j.voice = String(inp.voice).trim();
      if (AUDIO_MODES.includes(String(inp.audio_mode))) j.audio_mode = String(inp.audio_mode);
      if (['local', 'gemma31b', 'google'].includes(String(inp.translator))) j.translator = String(inp.translator);
      if (TARGET_LANGS.includes(String(inp.target_lang))) j.target_lang = String(inp.target_lang);
      if (inp.burn_subs != null) j.burn_subs = String(inp.burn_subs) === '1' || inp.burn_subs === true;
      if (SUBS_PRESETS.includes(String(inp.subs_preset))) j.subs_preset = String(inp.subs_preset);
      j.review_text = false; j.retranslate = false; delete j.reburn;
      j.status = 'pending'; j.progress = 0; j.phase = 'full';
      j.error = null; j.finished_at = null; j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true, queued: true });
    }

    // ---------- STREAM (Range) + DOWNLOAD ----------
    case 'stream':
      return streamOrDownload(env, request, url, false);
    case 'download':
      return streamOrDownload(env, request, url, true);

    default:
      return json({ error: 'Neznámá akce' }, 400);
    }
  } catch (e) {
    return json({ error: 'Chyba serveru: ' + (e && e.message || e) }, 500);
  }
}

// ---------- pomocné ----------
async function readForm(request, cached) {
  let fd = cached;
  if (!fd) {
    const ct = request.headers.get('content-type') || '';
    if (ct.includes('application/json')) return await request.json().catch(() => ({}));
    fd = await request.formData().catch(() => null);
  }
  if (!fd) return {};
  const o = {};
  for (const [k, v] of fd.entries()) if (typeof v === 'string') o[k] = v;
  return o;
}
async function readJson(request) {
  try { return await request.json(); } catch (_) { return await readForm(request); }
}

async function googleTranslate(text, sl, tl) {
  text = String(text || '').trim();
  if (!text) return null;
  const u = 'https://translate.googleapis.com/translate_a/single?client=gtx'
    + `&sl=${encodeURIComponent(sl || 'auto')}&tl=${encodeURIComponent(tl || 'cs')}`
    + `&dt=t&q=${encodeURIComponent(text)}`;
  try {
    const r = await fetch(u, { headers: { 'user-agent': 'Mozilla/5.0' } });
    if (!r.ok) return null;
    const d = await r.json();
    if (!Array.isArray(d) || !Array.isArray(d[0])) return null;
    let out = '';
    for (const seg of d[0]) if (seg && seg[0]) out += seg[0];
    out = out.trim();
    return out || null;
  } catch (_) { return null; }
}

const VMIME = {
  mp4: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm', mkv: 'video/x-matroska',
  m4v: 'video/mp4', avi: 'video/x-msvideo', mp3: 'audio/mpeg', wav: 'audio/wav', m4a: 'audio/mp4',
};

async function streamOrDownload(env, request, url, isDownload) {
  const j = await getJob(env, url.searchParams.get('id') || '');
  if (!j) return json({ error: 'Job nenalezen' }, 404);
  const kind = url.searchParams.get('kind') || 'video';
  let key, mime, dlname;
  const base = (baseName(j.filename || 'download').replace(/\.[^.]+$/, '')) || 'download';
  if (kind === 'source') {
    const ext = cleanExt(j.ext || '');
    if (!ext) return json({ error: 'Zdroj neexistuje' }, 404);
    key = upKey(j.id, ext); mime = VMIME[ext] || 'video/mp4';
  } else {
    const map = {
      video: ['mp4', 'video/mp4', 'dubbed.mp4'],
      audio: ['mp3', 'audio/mpeg', 'dubbed.mp3'],
      srt_tgt: ['srt', 'application/x-subrip', 'cs.srt'],
      srt_src: ['src.srt', 'application/x-subrip', 'src.srt'],
    };
    if (!map[kind]) return json({ error: 'Neplatný typ' }, 400);
    key = outKey(j.id, map[kind][0]); mime = map[kind][1]; dlname = `${base}.${map[kind][2]}`;
  }
  const head = await env.R2.head(key);
  if (!head) return json({ error: 'Výstup neexistuje' }, 404);
  const size = head.size;

  if (isDownload) {
    const obj = await env.R2.get(key);
    return new Response(obj.body, { headers: {
      'content-type': mime, 'content-length': String(size),
      'content-disposition': `attachment; filename="${dlname}"`,
    } });
  }

  // stream s podporou Range (přehrávání/přetáčení)
  const range = request.headers.get('range');
  const baseHeaders = { 'content-type': mime, 'accept-ranges': 'bytes', 'content-disposition': 'inline', 'cache-control': 'private, max-age=600' };
  const m = range && /bytes=(\d*)-(\d*)/.exec(range);
  if (m) {
    let start = m[1] === '' ? 0 : parseInt(m[1], 10);
    let end = m[2] === '' ? size - 1 : parseInt(m[2], 10);
    if (end >= size) end = size - 1;
    if (start > end || start >= size) {
      return new Response(null, { status: 416, headers: { 'content-range': `bytes */${size}` } });
    }
    const len = end - start + 1;
    const obj = await env.R2.get(key, { range: { offset: start, length: len } });
    return new Response(obj.body, { status: 206, headers: {
      ...baseHeaders, 'content-length': String(len), 'content-range': `bytes ${start}-${end}/${size}`,
    } });
  }
  const obj = await env.R2.get(key);
  return new Response(obj.body, { headers: { ...baseHeaders, 'content-length': String(size) } });
}
