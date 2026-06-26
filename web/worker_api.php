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
    $picked = null;
    $jobs = array_reverse(all_jobs());  // nejstarší pending první
    foreach ($jobs as $j) {
        if (($j['status'] ?? '') === 'pending') { $picked = $j; break; }
    }
    // osiřelé joby (worker spadl) – průběžný stav starší 15 min znovu zařaď
    if (!$picked) {
        $nowts = time();
        foreach ($jobs as $j) {
            $st = $j['status'] ?? '';
            if (!in_array($st, ['pending','done','error','uploading'], true)
                && ($nowts - strtotime($j['updated_at'] ?? '1970-01-01 00:00:00')) > 900) {
                $picked = $j; break;
            }
        }
    }
    if ($picked) {
        $picked['status'] = 'processing';
        $picked['progress'] = 3;
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
        'llm_correct' => (bool)$picked['llm_correct'], 'is_video' => (bool)$picked['is_video'],
    ]]);

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
