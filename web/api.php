<?php
// ============================================================
//  PZ AI DAB ALL — uživatelské API (web/mobil).
//  Přihlášení lidí řeší HTTP Basic Auth (.htaccess). Tady se auth neřeší.
//  Akce: status, upload_init, upload_chunk, upload_finish, list, job,
//        download, delete.
//  Workerské akce jsou v samostatném worker_api.php (token, mimo Basic Auth).
// ============================================================
require_once __DIR__ . '/lib.php';
ensure_dirs();

$action = $_GET['action'] ?? $_POST['action'] ?? '';
$VIDEO_EXT = ['mp4','mov','mkv','webm','avi','m4v','ts','mpg','mpeg'];

switch ($action) {

case 'status':
    jsend([
        'ok' => true,
        'source_langs' => SOURCE_LANGS, 'target_langs' => TARGET_LANGS,
        'tts_engines' => TTS_ENGINES, 'audio_modes' => AUDIO_MODES,
        'max_upload_mb' => MAX_UPLOAD_MB,
        'user' => $_SERVER['PHP_AUTH_USER'] ?? ($_SERVER['REMOTE_USER'] ?? 'PetrZ'),
    ]);

// ---------- CHUNKED UPLOAD (obchází limit těla na hostingu) ---------------
case 'upload_init':
    $orig = (string)($_POST['filename'] ?? '');
    $ext  = clean_ext(pathinfo($orig, PATHINFO_EXTENSION));
    if (!$ext) jsend(['error' => 'Nepodporovaný formát souboru'], 400);
    $sl = (string)($_POST['source_lang'] ?? 'auto');
    if (!in_array($sl, SOURCE_LANGS, true)) $sl = 'auto';
    $tl = (string)($_POST['target_lang'] ?? 'cs-CZ');
    if (!in_array($tl, TARGET_LANGS, true)) $tl = 'cs-CZ';
    $eng = (string)($_POST['tts_engine'] ?? 'piper');
    if (!in_array($eng, TTS_ENGINES, true)) $eng = 'piper';
    $am = (string)($_POST['audio_mode'] ?? 'replace');
    if (!in_array($am, AUDIO_MODES, true)) $am = 'replace';
    $id = new_id();
    @file_put_contents(UP_DIR . '/' . $id . '.part', '');
    $job = [
        'id' => $id, 'filename' => basename($orig), 'ext' => $ext,
        'source_lang' => $sl, 'target_lang' => $tl, 'tts_engine' => $eng,
        'voice' => trim((string)($_POST['voice'] ?? '')),
        'audio_mode' => $am,
        'burn_subs' => ((string)($_POST['burn_subs'] ?? '0')) === '1',
        'llm_correct' => ((string)($_POST['llm_correct'] ?? '1')) === '1',
        'is_video' => in_array($ext, $VIDEO_EXT, true),
        'status' => 'uploading', 'progress' => 0,
        'created_at' => now(), 'updated_at' => now(),
        'finished_at' => null, 'error' => null,
        'duration' => 0, 'text_preview' => '', 'outputs' => [], 'size' => 0,
    ];
    save_job($job);
    jsend(['ok' => true, 'id' => $id]);

case 'upload_chunk':
    $j = load_job((string)($_POST['id'] ?? ''));
    if (!$j || ($j['status'] ?? '') !== 'uploading') jsend(['error' => 'Neplatné nahrávání'], 400);
    if (empty($_FILES['chunk']) || !is_uploaded_file($_FILES['chunk']['tmp_name'] ?? ''))
        jsend(['error' => 'Chybí část souboru'], 400);
    $part = UP_DIR . '/' . clean_id($j['id']) . '.part';
    $cur = is_file($part) ? filesize($part) : 0;
    if ($cur + (int)$_FILES['chunk']['size'] > MAX_UPLOAD_MB * 1024 * 1024)
        jsend(['error' => 'Soubor je příliš velký (limit ' . MAX_UPLOAD_MB . ' MB)'], 400);
    $in = fopen($_FILES['chunk']['tmp_name'], 'rb');
    $out = fopen($part, 'ab');
    if (!$in || !$out) jsend(['error' => 'Nelze zapsat část souboru'], 500);
    while (!feof($in)) fwrite($out, fread($in, 1 << 18));
    fclose($in); fclose($out);
    jsend(['ok' => true, 'received' => filesize($part)]);

case 'upload_finish':
    $j = load_job((string)($_POST['id'] ?? ''));
    if (!$j || ($j['status'] ?? '') !== 'uploading') jsend(['error' => 'Neplatné nahrávání'], 400);
    $part = UP_DIR . '/' . clean_id($j['id']) . '.part';
    $dest = UP_DIR . '/' . clean_id($j['id']) . '.' . clean_ext($j['ext']);
    if (!is_file($part) || filesize($part) === 0) jsend(['error' => 'Žádná data nenahrána'], 400);
    if (!@rename($part, $dest)) jsend(['error' => 'Nelze dokončit nahrávání'], 500);
    $j['size'] = filesize($dest);
    $j['status'] = 'pending';
    $j['updated_at'] = now();
    save_job($j);
    jsend(['ok' => true, 'job' => public_job($j)]);

// ---------- LIST / DETAIL / DELETE ----------------------------------------
case 'list':
    jsend(['jobs' => array_map('public_job', all_jobs())]);

case 'job':
    $j = load_job((string)($_GET['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    jsend(public_job($j));

case 'delete':
    $j = load_job((string)($_POST['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    delete_job_files($j);
    jsend(['ok' => true, 'deleted' => $j['id']]);

// ---------- DOWNLOAD VÝSTUPU ----------------------------------------------
case 'download':
    $j = load_job((string)($_GET['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    $kind = (string)($_GET['kind'] ?? 'video');
    $map = [
        'video'   => ['mp4', 'video/mp4', 'dubbed.mp4'],
        'audio'   => ['mp3', 'audio/mpeg', 'dubbed.mp3'],
        'srt_tgt' => ['srt', 'application/x-subrip', 'srt'],
        'srt_src' => ['src.srt', 'application/x-subrip', 'src.srt'],
    ];
    if (!isset($map[$kind])) jsend(['error' => 'Neplatný výstup'], 400);
    [$suffix, $mime, $dlsuffix] = $map[$kind];
    $path = OUT_DIR . '/' . clean_id($j['id']) . '.' . $suffix;
    if (!is_file($path)) jsend(['error' => 'Výstup neexistuje'], 404);
    $base = safe_filename_base($j['filename']);
    header('Content-Type: ' . $mime);
    header('Content-Disposition: attachment; filename="' . $base . '.' . $dlsuffix . '"');
    header('Content-Length: ' . filesize($path));
    readfile($path);
    exit;

default:
    jsend(['error' => 'Neznámá akce'], 400);
}
