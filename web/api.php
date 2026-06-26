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
    $preset = (string)($_POST['subs_preset'] ?? 'classic');
    if (!in_array($preset, ['classic','reels','reels_box','word','karaoke','karaoke_green','karaoke_box'], true))
        $preset = 'classic';
    $id = new_id();
    @file_put_contents(UP_DIR . '/' . $id . '.part', '');
    $job = [
        'id' => $id, 'filename' => basename($orig), 'ext' => $ext,
        'source_lang' => $sl, 'target_lang' => $tl, 'tts_engine' => $eng,
        'voice' => trim((string)($_POST['voice'] ?? '')),
        'audio_mode' => $am, 'subs_preset' => $preset,
        'subs_chars' => 0, 'subs_maxlines' => 2, 'subs_size' => '',
        'burn_subs' => ((string)($_POST['burn_subs'] ?? '0')) === '1',
        'review_text' => ((string)($_POST['review_text'] ?? '0')) === '1',
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

// ---------- REVIEW: úprava textu před dabingem ----------------------------
case 'segments':
    // Vrať návrh segmentů {start,end,text,src} k editaci + uložené volby titulků.
    $j = load_job((string)($_GET['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    $p = OUT_DIR . '/' . clean_id($j['id']) . '.seg.json';
    if (!is_file($p)) jsend(['error' => 'Segmenty zatím nejsou připravené'], 404);
    $d = json_decode((string)file_get_contents($p), true);
    jsend(['id' => $j['id'], 'status' => $j['status'] ?? '',
           'subs_chars' => (int)($j['subs_chars'] ?? 0),
           'subs_maxlines' => (int)($j['subs_maxlines'] ?? 2),
           'subs_size' => (string)($j['subs_size'] ?? ''),
           'segments' => is_array($d['segments'] ?? null) ? $d['segments'] : []]);

case 'approve':         // ulož + spusť dabing (fáze 2)
case 'save_segments':   // jen ulož (zůstává review – pro pozdější úpravu/SRT)
    $in = json_decode((string)file_get_contents('php://input'), true);
    if (!is_array($in)) $in = $_POST;
    $j = load_job((string)($in['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    if (!in_array($j['status'] ?? '', ['review', 'approved', 'error'], true))
        jsend(['error' => 'Tento job není ve stavu k úpravě'], 400);
    $segs = $in['segments'] ?? null;
    if (!is_array($segs) || !$segs) jsend(['error' => 'Chybí segmenty'], 400);
    if (count($segs) > 20000) jsend(['error' => 'Příliš mnoho segmentů'], 400);
    $clean = [];
    foreach ($segs as $s) {
        $txt = trim((string)($s['text'] ?? ''));
        $st = (float)($s['start'] ?? 0); $en = (float)($s['end'] ?? 0);
        if ($txt === '' || $en <= $st) continue;
        $row = ['start' => $st, 'end' => $en, 'text' => mb_substr($txt, 0, 2000)];
        if (isset($s['src'])) $row['src'] = mb_substr((string)$s['src'], 0, 2000);
        $clean[] = $row;
    }
    if (!$clean) jsend(['error' => 'Po úpravě nezůstal žádný text'], 400);
    file_put_contents(OUT_DIR . '/' . clean_id($j['id']) . '.seg.json',
        json_encode(['segments' => $clean], JSON_UNESCAPED_UNICODE), LOCK_EX);
    if (isset($in['subs_chars']))    $j['subs_chars'] = max(0, min(60, (int)$in['subs_chars']));
    if (isset($in['subs_maxlines'])) $j['subs_maxlines'] = ((int)$in['subs_maxlines'] === 1) ? 1 : 2;
    if (isset($in['subs_size'])) {
        $sz = (string)$in['subs_size'];
        $j['subs_size'] = in_array($sz, ['small','medium','large','xl'], true) ? $sz : '';
    }
    if ($action === 'approve') { $j['status'] = 'approved'; $j['progress'] = 50; }   // → dabing
    else { $j['status'] = 'review'; }                                                // zůstává k úpravě
    $j['error'] = null;
    $j['updated_at'] = now();
    save_job($j);
    jsend(['ok' => true, 'count' => count($clean), 'mode' => $action]);

case 'export_srt':
    // Stáhni aktuální (upravené) titulky jako SRT – bez nutnosti dabovat.
    $j = load_job((string)($_GET['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    $p = OUT_DIR . '/' . clean_id($j['id']) . '.seg.json';
    if (!is_file($p)) jsend(['error' => 'Segmenty nejsou připravené'], 404);
    $d = json_decode((string)file_get_contents($p), true);
    $segs = is_array($d['segments'] ?? null) ? $d['segments'] : [];
    $srt = ''; $i = 1;
    foreach ($segs as $s) {
        $txt = trim((string)($s['text'] ?? '')); if ($txt === '') continue;
        $srt .= $i++ . "\r\n" . srt_ts((float)($s['start'] ?? 0)) . ' --> '
              . srt_ts((float)($s['end'] ?? 0)) . "\r\n" . $txt . "\r\n\r\n";
    }
    $base = safe_filename_base($j['filename'] ?? 'titulky');
    header('Content-Type: application/x-subrip; charset=utf-8');
    header('Content-Disposition: attachment; filename="' . $base . '.cs.srt"');
    echo $srt; exit;

// ---------- STREAM (přehrávání ve webu, podpora Range pro přetáčení) -------
case 'stream':
    $j = load_job((string)($_GET['id'] ?? ''));
    if (!$j) jsend(['error' => 'Job nenalezen'], 404);
    $kind = (string)($_GET['kind'] ?? 'video');
    if ($kind === 'source') {
        // ZDROJOVÉ video (pro editor titulků ve stavu review – výstup ještě není)
        $ext = clean_ext($j['ext'] ?? '');
        $path = UP_DIR . '/' . clean_id($j['id']) . '.' . $ext;
        $vmime = ['mp4' => 'video/mp4', 'mov' => 'video/quicktime', 'webm' => 'video/webm',
                  'mkv' => 'video/x-matroska', 'm4v' => 'video/mp4', 'avi' => 'video/x-msvideo',
                  'mp3' => 'audio/mpeg', 'wav' => 'audio/wav', 'm4a' => 'audio/mp4'];
        $mime = $vmime[$ext] ?? 'video/mp4';
        if (!$ext || !is_file($path)) jsend(['error' => 'Zdroj neexistuje'], 404);
    } else {
        $smap = ['video' => ['mp4', 'video/mp4'], 'audio' => ['mp3', 'audio/mpeg']];
        if (!isset($smap[$kind])) jsend(['error' => 'Neplatný typ'], 400);
        [$suffix, $mime] = $smap[$kind];
        $path = OUT_DIR . '/' . clean_id($j['id']) . '.' . $suffix;
        if (!is_file($path)) jsend(['error' => 'Výstup neexistuje'], 404);
    }
    $size = filesize($path);
    $start = 0; $end = $size - 1;
    header('Content-Type: ' . $mime);
    header('Accept-Ranges: bytes');
    header('Content-Disposition: inline');
    header('Cache-Control: private, max-age=600');
    if (isset($_SERVER['HTTP_RANGE'])
            && preg_match('/bytes=(\d*)-(\d*)/', $_SERVER['HTTP_RANGE'], $m)) {
        if ($m[1] !== '') $start = (int)$m[1];
        if ($m[2] !== '') $end = (int)$m[2];
        if ($end >= $size) $end = $size - 1;
        if ($start > $end || $start >= $size) {
            header('HTTP/1.1 416 Range Not Satisfiable');
            header("Content-Range: bytes */$size");
            exit;
        }
        header('HTTP/1.1 206 Partial Content');
        header("Content-Range: bytes $start-$end/$size");
    }
    $len = $end - $start + 1;
    header('Content-Length: ' . $len);
    while (ob_get_level()) ob_end_clean();
    $fp = fopen($path, 'rb');
    fseek($fp, $start);
    $remaining = $len;
    while ($remaining > 0 && !feof($fp)) {
        $chunk = fread($fp, min(1 << 18, $remaining));
        if ($chunk === false) break;
        echo $chunk;
        $remaining -= strlen($chunk);
        flush();
    }
    fclose($fp);
    exit;

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
