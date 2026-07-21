<?php
// ============================================================
//  PZ AI DAB ALL — web relay konfigurace (Forpsi /www/ALLDUB/)
//  Web (i mobil) mluví jen s tímto serverem; PC worker si sem chodí
//  pro úkoly (polling) přes worker token. Funguje odkudkoliv.
//  Přihlášení lidí řeší HTTP Basic Auth (.htaccess + .htpasswd, login PetrZ),
//  worker_api.php je z Basic Auth vyjmuté a chráněné tímto tokenem.
// ============================================================

// Token musí být STEJNÝ jako "worker_token" ve worker_config.json na PC.
const WORKER_TOKEN = 'REPLACE_WITH_YOUR_OWN_RANDOM_TOKEN';

// --- Limity / povolené hodnoty -------------------------------------------
const MAX_UPLOAD_MB = 2048;   // chunked upload obchází limit těla requestu
const ALLOWED_EXT = ['mp4','mov','mkv','webm','avi','m4v','ts','mpg','mpeg',
                     'wav','mp3','m4a','aac','flac','ogg','opus'];

const SOURCE_LANGS = ['auto','cs-CZ','en-US','uk-UA','ru-RU','de-DE',
                      'pl-PL','sk-SK','es-ES','fr-FR','it-IT'];
const TARGET_LANGS = ['cs-CZ','en-US','uk-UA','de-DE','pl-PL',
                      'sk-SK','es-ES','fr-FR','it-IT','ru-RU'];
const TTS_ENGINES  = ['piper','xtts'];
const AUDIO_MODES  = ['replace','voiceover'];

// --- Cesty (data mimo přímý web přístup, chráněno .htaccess) -------------
define('DATA_DIR', __DIR__ . '/data');
define('UP_DIR',   DATA_DIR . '/uploads');
define('OUT_DIR',  DATA_DIR . '/outputs');
define('JOB_DIR',  DATA_DIR . '/jobs');
