<?php
// ============================================================
//  PZ AI DAB ALL — workerské API (token, MIMO Basic Auth).
//  PC worker sem chodí pro úkoly a nahrává výsledky.
//  Akce: worker_claim, worker_source, worker_progress, worker_result,
//        worker_fail.
// ============================================================
require_once __DIR__ . '/lib.php';
ensure_dirs();
require_worker();

$action = $_GET['action'] ?? $_POST['action'] ?? '';

switch ($action) {

case 'worker_claim':
    // Atomický claim přes zámek, ať dva workeři nevezmou tentýž job.
    $lf = @fopen(JOB_DIR . '/.claim.lock', 'c');
    if ($lf) flock($lf, LOCK_EX);
    $picked = null; $phase = 'full';
    $jobs = array_reverse(all_jobs());  // nejstarší první
    // 1) approved (uživatel schválil upravený text) → fáze 2: dabing
    foreach ($jobs as $j) {
        if (($j['status'] ?? '') === 'approved') { $picked = $j; $phase = 'dub'; break; }
    }
    // 2) pending (nový job) → buď příprava textu (review), nebo rovnou plný běh
    if (!$picked) {
        foreach ($jobs as $j) {
            if (($j['status'] ?? '') === 'pending') {
                $picked = $j;
                $phase = !empty($j['review_text']) ? 'prepare' : 'full';
                break;
            }
        }
    }
    // 3) osiřelé joby (worker spadl) – „processing" starší 15 min znovu zařaď
    //    (stav „review" je čekání na uživatele, ten se NEpřebírá)
    if (!$picked) {
        $nowts = time();
        foreach ($jobs as $j) {
            if (($j['status'] ?? '') === 'processing'
                && ($nowts - strtotime($j['updated_at'] ?? '1970-01-01 00:00:00')) > 900) {
                $picked = $j; $phase = $j['phase'] ?? 'full'; break;
            }
        }
    }
    if ($picked) {
        $picked['status'] = 'processing';
        $picked['progress'] = 3;
        $picked['phase'] = $phase;          // uloženo pro případnou obnovu osiřelého jobu
        $picked['updated_at'] = now();
        save_job($picked);
    }
    if ($lf) { flock($lf, LOCK_UN); fclose($lf); }
    if (!$picked) jsend(['job' => null]);
    jsend(['job' => [
        'id' => $picked['id'], 'filename' => $picked['filename'], 'ext' => $picked['ext'],
        'source_lang' => $picked['source_lang'], 'target_lang' => $picked['target_lang'],
        'tts_engine' => $picked['tts_engine'], 'voice' => $picked['voice'] ?? '',
        'audio_mode' => $picked['audio_mode'], 'subs_preset' => $picked['subs_preset'] ?? 'classic',
        'burn_subs' => (bool)$picked['burn_subs'],
        'subs_chars' => (int)($picked['subs_chars'] ?? 0),
        'subs_maxlines' => (int)($picked['subs_maxlines'] ?? 2),
        'llm_correct' => (bool)$picked['llm_correct'], 'is_video' => (bool)$picked['is_video'],
        'review_text' => (bool)($picked['review_text'] ?? false), 'phase' => $phase,
    ]]);

case 'worker_draft':
    // Fáze 1 → worker nahraje návrh segmentů (k editaci); job přejde do review.
    $j = load_job((string)($_POST['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    $id = clean_id($j['id']);
    if (!empty($_FILES['segments']) && is_uploaded_file($_FILES['segments']['tmp_name'] ?? ''))
        $raw = (string)file_get_contents($_FILES['segments']['tmp_name']);
    else
        $raw = (string)($_POST['segments'] ?? '');
    $data = json_decode($raw, true);
    $segs = is_array($data['segments'] ?? null) ? $data['segments']
          : (is_array($data) ? $data : []);
    if (!$segs) jsend(['error' => 'Chybí segmenty'], 400);
    file_put_contents(OUT_DIR . '/' . $id . '.seg.json',
        json_encode(['segments' => array_values($segs)], JSON_UNESCAPED_UNICODE), LOCK_EX);
    if (!empty($_FILES['src_srt']) && is_uploaded_file($_FILES['src_srt']['tmp_name'] ?? '')) {
        if (move_uploaded_file($_FILES['src_srt']['tmp_name'], OUT_DIR . '/' . $id . '.src.srt'))
            $j['outputs'] = array_merge((array)($j['outputs'] ?? []), ['srt_src' => true]);
    }
    if (isset($_POST['duration'])) $j['duration'] = (float)$_POST['duration'];
    $j['text_preview'] = (string)($_POST['text_preview'] ?? '');
    $j['status'] = 'review';
    $j['progress'] = 50;
    $j['error'] = null;
    $j['updated_at'] = now();
    save_job($j);
    jsend(['ok' => true]);

case 'worker_segments':
    // Fáze 2 → worker si stáhne uživatelem upravené segmenty.
    $j = load_job((string)($_GET['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    $p = OUT_DIR . '/' . clean_id($j['id']) . '.seg.json';
    if (!is_file($p)) jsend(['error' => 'Segmenty neexistují'], 404);
    $d = json_decode((string)file_get_contents($p), true);
    jsend(['segments' => is_array($d['segments'] ?? null) ? $d['segments'] : []]);

case 'worker_source':
    $j = load_job((string)($_GET['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    $ext = clean_ext($j['ext'] ?? '');
    $path = UP_DIR . '/' . clean_id($j['id']) . '.' . $ext;
    if (!$ext || !is_file($path)) jsend(['error' => 'Zdroj neexistuje'], 404);
    header('Content-Type: application/octet-stream');
    header('Content-Length: ' . filesize($path));
    readfile($path);
    exit;

case 'worker_progress':
    $j = load_job((string)($_POST['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    if (isset($_POST['progress'])) $j['progress'] = max(0, min(100, (int)$_POST['progress']));
    if (isset($_POST['stage'])) $j['stage'] = substr((string)$_POST['stage'], 0, 40);
    $j['status'] = 'processing';
    $j['updated_at'] = now();
    save_job($j);
    jsend(['ok' => true]);

case 'worker_result':
    $j = load_job((string)($_POST['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    $id = clean_id($j['id']);
    $fields = ['video' => 'mp4', 'audio' => 'mp3', 'srt_tgt' => 'srt', 'srt_src' => 'src.srt'];
    $outputs = [];
    foreach ($fields as $field => $suffix) {
        if (!empty($_FILES[$field]) && is_uploaded_file($_FILES[$field]['tmp_name'] ?? '')) {
            if (move_uploaded_file($_FILES[$field]['tmp_name'], OUT_DIR . '/' . $id . '.' . $suffix))
                $outputs[$field] = true;
        }
    }
    $j['outputs'] = $outputs;
    $j['text_preview'] = (string)($_POST['text_preview'] ?? '');
    if (isset($_POST['duration'])) $j['duration'] = (float)$_POST['duration'];
    $j['status'] = 'done';
    $j['progress'] = 100;
    $j['error'] = null;
    $j['finished_at'] = now();
    $j['updated_at'] = now();
    // zdrojové video už netřeba (šetří místo na hostingu)
    @unlink(UP_DIR . '/' . $id . '.' . clean_ext($j['ext'] ?? ''));
    save_job($j);
    jsend(['ok' => true]);

case 'worker_fail':
    $j = load_job((string)($_POST['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    $j['status'] = 'error';
    $j['error'] = mb_substr((string)($_POST['error'] ?? 'neznámá chyba'), 0, 1000);
    $j['finished_at'] = now();
    $j['updated_at'] = now();
    save_job($j);
    jsend(['ok' => true]);

default:
    jsend(['error' => 'Neznámá akce'], 400);
}
