// ============================================================
//  PZ AI DAB ALL — Cloudflare relay: workerské API (PC worker).
//  Route: /worker_api.php  (kontrakt shodný s Forpsi worker_api.php →
//  dab_worker.py stačí přesměrovat na base_url této Pages appky).
//  Ochrana: X-Worker-Token (mimo login cookie).
// ============================================================
import {
  json, now, cleanId, cleanExt, upKey, outKey,
  getJob, saveJob, allJobs, checkWorker,
} from './_lib.js';

export async function onRequest(context) {
  const { request, env } = context;
  if (!checkWorker(env, request)) return json({ error: 'Neplatný worker token' }, 403);
  const url = new URL(request.url);
  const action = url.searchParams.get('action') || '';

  try {
    switch (action) {

    case 'worker_claim':
      return workerClaim(env);

    case 'worker_draft': {
      const form = await request.formData();
      const j = await getJob(env, String(form.get('id') || ''));
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      let raw;
      const segFile = form.get('segments');
      if (segFile && typeof segFile !== 'string') raw = await segFile.text();
      else raw = String(form.get('segments') || '');
      let data; try { data = JSON.parse(raw); } catch (_) { data = null; }
      const segs = Array.isArray(data?.segments) ? data.segments : (Array.isArray(data) ? data : []);
      if (!segs.length) return json({ error: 'Chybí segmenty' }, 400);
      await env.R2.put(outKey(j.id, 'seg.json'), JSON.stringify({ segments: segs }));
      const srcSrt = form.get('src_srt');
      if (srcSrt && typeof srcSrt !== 'string') {
        await env.R2.put(outKey(j.id, 'src.srt'), await srcSrt.arrayBuffer());
        j.outputs = { ...(j.outputs || {}), srt_src: true };
      }
      const dur = parseFloat(form.get('duration') || 0);
      if (dur > 0) j.duration = dur;
      j.text_preview = String(form.get('text_preview') || '');
      j.status = 'review'; j.progress = 50; j.error = null; j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true });
    }

    case 'worker_segments': {
      const j = await getJob(env, url.searchParams.get('id') || '');
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      const obj = await env.R2.get(outKey(j.id, 'seg.json'));
      if (!obj) return json({ error: 'Segmenty neexistují' }, 404);
      const d = JSON.parse(await obj.text());
      return json({ segments: Array.isArray(d.segments) ? d.segments : [] });
    }

    case 'worker_source': {
      const j = await getJob(env, url.searchParams.get('id') || '');
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      const ext = cleanExt(j.ext || '');
      if (!ext) return json({ error: 'Zdroj neexistuje' }, 404);
      const obj = await env.R2.get(upKey(j.id, ext));
      if (!obj) return json({ error: 'Zdroj neexistuje' }, 404);
      return new Response(obj.body, { headers: {
        'content-type': 'application/octet-stream', 'content-length': String(obj.size),
      } });
    }

    case 'worker_output': {
      const j = await getJob(env, url.searchParams.get('id') || '');
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      const kind = url.searchParams.get('kind') || 'video';
      const map = { video: 'mp4', audio: 'mp3', srt_tgt: 'srt' };
      if (!map[kind]) return json({ error: 'Neplatný typ' }, 400);
      const obj = await env.R2.get(outKey(j.id, map[kind]));
      if (!obj) return json({ error: 'Výstup neexistuje' }, 404);
      return new Response(obj.body, { headers: {
        'content-type': 'application/octet-stream', 'content-length': String(obj.size),
      } });
    }

    case 'worker_progress': {
      const form = await request.formData();
      const j = await getJob(env, String(form.get('id') || ''));
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      if (form.get('progress') != null) j.progress = Math.max(0, Math.min(100, parseInt(form.get('progress'), 10) || 0));
      if (form.get('stage') != null) j.stage = String(form.get('stage')).slice(0, 40);
      j.status = 'processing'; j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true });
    }

    case 'worker_result': {
      const form = await request.formData();
      const j = await getJob(env, String(form.get('id') || ''));
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      const fields = { video: 'mp4', audio: 'mp3', srt_tgt: 'srt', srt_src: 'src.srt' };
      const outputs = {};
      for (const [field, suffix] of Object.entries(fields)) {
        const f = form.get(field);
        if (f && typeof f !== 'string') {
          await env.R2.put(outKey(j.id, suffix), await f.arrayBuffer());
          outputs[field] = true;
        }
      }
      j.outputs = { ...(j.outputs || {}), ...outputs };  // reburn nahrává jen video → nesmaž SRT
      if (form.get('text_preview') != null) j.text_preview = String(form.get('text_preview'));
      if (form.get('duration') != null) j.duration = parseFloat(form.get('duration')) || 0;
      j.status = 'done'; j.progress = 100; j.error = null;
      j.finished_at = now(); j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true });
    }

    case 'worker_fail': {
      const form = await request.formData();
      const j = await getJob(env, String(form.get('id') || ''));
      if (!j) return json({ error: 'Job nenalezen' }, 404);
      j.status = 'error';
      j.error = String(form.get('error') || 'neznámá chyba').slice(0, 1000);
      j.finished_at = now(); j.updated_at = now();
      await saveJob(env, j);
      return json({ ok: true });
    }

    default:
      return json({ error: 'Neznámá akce' }, 400);
    }
  } catch (e) {
    return json({ error: 'Chyba serveru: ' + (e && e.message || e) }, 500);
  }
}

// Vyber a atomicky zabookuj další zakázku (stejná priorita jako Forpsi relay).
async function workerClaim(env) {
  const jobs = (await allJobs(env)).reverse();   // nejstarší první
  let picked = null, phase = 'full';

  for (const j of jobs) if (j.status === 'review' && j.retranslate) { picked = j; phase = 'retranslate'; break; }
  if (!picked) for (const j of jobs) if (j.reburn) { picked = j; phase = 'burn'; break; }
  if (!picked) for (const j of jobs) if (j.status === 'approved') { picked = j; phase = 'dub'; break; }
  if (!picked) for (const j of jobs) if (j.status === 'pending') { picked = j; phase = j.review_text ? 'prepare' : 'full'; break; }
  if (!picked) {
    const nowts = Date.now();
    for (const j of jobs) {
      if (j.status === 'processing' && (nowts - Date.parse((j.updated_at || '1970-01-01') + 'Z')) > 900000) {
        picked = j; phase = j.phase || 'full'; break;
      }
    }
  }
  if (!picked) return json({ job: null });

  // atomický přechod: uspěj jen když se status od načtení nezměnil
  const prevStatus = picked.status;
  picked.status = 'processing'; picked.progress = 3; picked.phase = phase;
  if (phase === 'retranslate') delete picked.retranslate;
  if (phase === 'burn') delete picked.reburn;
  picked.updated_at = now();
  const res = await env.DB.prepare(
    'UPDATE jobs SET status=?, updated_at=?, data=? WHERE id=? AND status=?')
    .bind('processing', picked.updated_at, JSON.stringify(picked), picked.id, prevStatus)
    .run();
  if (!res.meta || res.meta.changes === 0) return json({ job: null });  // jiný poll to vzal → zkusí příště

  return json({ job: {
    id: picked.id, filename: picked.filename, ext: picked.ext,
    source_lang: picked.source_lang, target_lang: picked.target_lang,
    tts_engine: picked.tts_engine, voice: picked.voice || '',
    translator: picked.translator || 'local',
    audio_mode: picked.audio_mode, subs_preset: picked.subs_preset || 'classic',
    burn_subs: !!picked.burn_subs,
    subs_chars: parseInt(picked.subs_chars || 0, 10),
    subs_maxlines: parseInt(picked.subs_maxlines || 2, 10),
    subs_size: String(picked.subs_size || ''),
    llm_correct: !!picked.llm_correct, is_video: !!picked.is_video,
    review_text: !!picked.review_text, phase,
  } });
}
