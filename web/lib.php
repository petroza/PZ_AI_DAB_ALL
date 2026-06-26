<?php
require_once __DIR__ . '/config.php';

function ensure_dirs(): void {
    foreach ([DATA_DIR, UP_DIR, OUT_DIR, JOB_DIR] as $d) {
        if (!is_dir($d)) @mkdir($d, 0775, true);
    }
}

function jsend($data, int $code = 200): void {
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function require_worker(): void {
    $tok = $_SERVER['HTTP_X_WORKER_TOKEN'] ?? ($_REQUEST['token'] ?? '');
    if (!is_string($tok) || !hash_equals(WORKER_TOKEN, $tok)) {
        jsend(['error' => 'Neplatný worker token'], 403);
    }
}

function clean_id(string $id): string  { return preg_replace('/[^a-f0-9]/', '', $id); }
function clean_ext(string $ext): string {
    $ext = strtolower(preg_replace('/[^a-z0-9]/i', '', $ext));
    return in_array($ext, ALLOWED_EXT, true) ? $ext : '';
}
function new_id(): string { return bin2hex(random_bytes(6)); }
function now(): string    { return date('Y-m-d H:i:s'); }

function job_path(string $id): string { return JOB_DIR . '/' . clean_id($id) . '.json'; }

function load_job(string $id): ?array {
    $p = job_path($id);
    if (!is_file($p)) return null;
    $j = json_decode((string)file_get_contents($p), true);
    return is_array($j) ? $j : null;
}
function save_job(array $job): void {
    file_put_contents(job_path($job['id']),
        json_encode($job, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), LOCK_EX);
}
function all_jobs(): array {
    $out = [];
    foreach (glob(JOB_DIR . '/*.json') as $f) {
        $j = json_decode((string)file_get_contents($f), true);
        if (is_array($j)) $out[] = $j;
    }
    usort($out, fn($a, $b) => strcmp($b['created_at'] ?? '', $a['created_at'] ?? ''));
    return $out;
}
function delete_job_files(array $job): void {
    $id = clean_id($job['id']);
    $ext = clean_ext($job['ext'] ?? '');
    if ($ext) @unlink(UP_DIR . '/' . $id . '.' . $ext);
    @unlink(UP_DIR . '/' . $id . '.part');
    foreach (['mp4','mp3','src.srt','srt','json','seg.json'] as $s) @unlink(OUT_DIR . '/' . $id . '.' . $s);
    @unlink(job_path($id));
}

function srt_ts(float $sec): string {
    if ($sec < 0) $sec = 0.0;
    $ms = (int)round($sec * 1000);
    $h = intdiv($ms, 3600000); $ms %= 3600000;
    $m = intdiv($ms, 60000);   $ms %= 60000;
    $s = intdiv($ms, 1000);    $ms %= 1000;
    return sprintf('%02d:%02d:%02d,%03d', $h, $m, $s, $ms);
}

function safe_filename_base(string $filename): string {
    $base = preg_replace('/[\\\\\/"\r\n]+/', '_', pathinfo($filename, PATHINFO_FILENAME));
    return ($base === '' || $base === null) ? 'download' : $base;
}

// Veřejná podoba jobu pro UI.
function public_job(array $j): array {
    return [
        'id'           => $j['id'] ?? '',
        'filename'     => $j['filename'] ?? '',
        'source_lang'  => $j['source_lang'] ?? 'auto',
        'target_lang'  => $j['target_lang'] ?? 'cs-CZ',
        'tts_engine'   => $j['tts_engine'] ?? 'piper',
        'voice'        => $j['voice'] ?? '',
        'audio_mode'   => $j['audio_mode'] ?? 'replace',
        'burn_subs'    => (bool)($j['burn_subs'] ?? false),
        'review_text'  => (bool)($j['review_text'] ?? false),
        'llm_correct'  => (bool)($j['llm_correct'] ?? true),
        'is_video'     => (bool)($j['is_video'] ?? true),
        'status'       => $j['status'] ?? 'pending',
        'progress'     => (int)($j['progress'] ?? 0),
        'created_at'   => $j['created_at'] ?? '',
        'finished_at'  => $j['finished_at'] ?? null,
        'error'        => $j['error'] ?? null,
        'duration'     => $j['duration'] ?? 0,
        'text_preview' => $j['text_preview'] ?? '',
        'outputs'      => $j['outputs'] ?? [],
        'size'         => (int)($j['size'] ?? 0),
    ];
}
