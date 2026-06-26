# PZ AI DAB ALL — předávací poznámky (handoff)

Stručný rozcestník pro pokračování práce. **Hesla nejsou v tomto souboru** —
dodá je Petr v chatu (viz sekce Přístupy). Tenhle soubor commitovat lze (žádná
tajemství).

## Co to je
Lokální AI **dabing videa** (cokoliv → čeština) + **titulky**. Pipeline:
`video → ffmpeg WAV → ASR (faster-whisper) → překlad → TTS (Piper/XTTS) →
time-stretch (rubberband) → mux do videa (+ volitelně zapečené titulky)`.

Architektura **RELAY** (dabing z mobilu/odkudkoliv):
- **Web** = `web/` na Forpsi `/www/ALLDUB/` → https://www.appcrate.cloud/ALLDUB/
  (za HTTP Basic Auth, login PetrZ). Mobil/web mluví JEN s Forpsi.
- **Worker** = `dab_worker.py` v **`O:\ALLDUB`** (kopie repa + `.venv`). Polluje
  Forpsi (`worker_api.php`, token), stáhne zdroj, nadabuje lokálně, nahraje zpět.

## Kde co je
- **Repo (zdroj):** `C:\Users\Petr\Downloads\PZ_AI_DAB_ALL` — tady se edituje + commituje.
- **Worker (běh):** `O:\ALLDUB` — sem se kopírují `app/`, `engines/`, `dab_worker.py`.
- **GitHub:** `petroza/PZ_AI_DAB_ALL`, větev **`claude/repository-setup-kedti2`** (default).
  Commituje sem i „druhý Claude" — před pushem `git pull --rebase`, ať se to nerozjede.

## Deploy workflow (DŮLEŽITÉ — dvě místa)
Po editaci souboru:
1. **Worker soubory** (`app/*.py`, `engines/**`, `dab_worker.py`): `cp` do `O:\ALLDUB`
   + **restart workeru** (jinak běží starý kód v paměti).
2. **Web soubory** (`web/*.php`, `web/app.js`, `web/index.php`, `web/style.css`):
   `cp` do `O:\ALLDUB\web` **A** nahrát na Forpsi přes FTP:
   ```
   curl -T web/SOUBOR "ftp://ftpx.forpsi.com/www/ALLDUB/SOUBOR" -u "USER:HESLO"
   ```
3. Po změně `app.js`/`style.css` **bumpni verzi** v `web/index.php`
   (`app.js?v=N`, `style.css?v=N`) — jinak prohlížeč cachuje starou.

## Běžící služby na PC
- **Worker:** `START_DABWORKER.bat` (nebo `cd /o/ALLDUB && .venv\Scripts\python -u dab_worker.py`).
- **XTTS server** (jen pro engine `xtts`): `START_XTTS.bat` (:7868, GPU).
- `START_ALL.bat` spustí obojí. Ollama (`gemma4:latest`) musí běžet pro překlad.

## Testování
- **Browser:** přes Claude-in-Chrome MCP na https://www.appcrate.cloud/ALLDUB/
  (prohlížeč je přihlášený Basic Auth → `javascript_tool` fetch na `api.php` funguje).
- **NEvolat `worker_api.php?action=worker_claim` ručně** (curl) — claimne reálný
  job a osiří ho! Na workera koukej přes jeho log soubor.
- **Worker log:** stdout běžícího procesu.

## Co umí editor titulků (review režim)
Volba „✏️ Upravit text před dabingem" → po přepisu+překladu job čeká v **review**.
Editor (modal) = velké video s titulkem v obraze (klik = edituj přímo),
**timeline** se značkami (⏮▶⏭), **Velikost / Znaků-řádek / Řádky(1-2)**,
**⬇ SRT** (export bez dabingu), **Překladač + 🔄 Přeložit znovu**, **▶ Dabovat**.
Vše se ukládá i bez dabování (zavření = uloží). Náhled = výstup (krátké cue,
ruční zalomení na N řádků).

## Překladače (volba ve formuláři i v editoru)
`translator`: `local` (gemma4:latest, offline) | `gemma31b` (offline kvalitnější)
| `google` (online, zdarma, bez klíče) | ~~deepl~~ (z UI odebráno, backend dormantní).
Re-překlad v editoru: **Google = hned z relay (PHP)**, gemma = přes worker (phase
`retranslate`, bez ASR/videa, bere uložené `src`).

## Stav (k poslednímu commitu)
- `web/app.js?v=15`, `web/style.css?v=8`.
- Téma: tmavě **modré, hranaté** (proměnné v `style.css :root` / `[data-theme=light]`).
- Vše ověřeno naživo (review→edit→dabing, presety, karaoke titulky, re-překlad).

## Hlavní soubory
- `app/pipeline.py` — orchestrace: `_prepare` (ASR+překlad), `run_dub(segments=)`
  (TTS+mux), `prepare_segments` (review fáze 1), `_burn_preset_opts`, zapékání titulků.
- `engines/asr/asr_engine.py` — `translate_text` (rozcestník překladačů),
  `llm_translate` (gemma + retry/anti-echo), `_google_translate`, `_deepl_translate`.
- `engines/asr/ffmpeg_tools.py` — zapékání titulků (`burn_subtitles`, ASS;
  módy normal/karaoke/word).
- `dab_worker.py` — relay worker, fáze: full | prepare | dub | retranslate.
- `web/api.php` (uživatel), `web/worker_api.php` (worker token), `web/lib.php`,
  `web/index.php`, `web/app.js`, `web/style.css`.

## Gotchas
- **Forpsi rate-limit** na .php → worker poll 20 s + backoff 90 s na HTTP 429.
  Nezahlcovat víc poll smyčkami / curl floodem (dočasný ban).
- **Job ID = čistý hex** (`clean_id` v lib.php strhne nehex → 404). `new_id()` to drží.
- **Windows konzole** (cp1250) padá na unicode v `print()` → `dab_worker.py` má
  `sys.stdout.reconfigure(utf-8)`. Nepiš „→"/„ů" do printů bez toho.
- **Překlad — gemma4 občas „echuje" originál** (vrátí ho nepřeložený) →
  `_seems_untranslated` + 4 pokusy s eskalací prompt/teplota.
- **Titulky nepřetékají:** zapékání zalomí na šířku videa (font dle výšky,
  znaky/řádek capnuté na to, co se vejde). Editor zalamuje ručně (`edWrapLines`).
- **CRLF:** git hlásí LF→CRLF warningy, neřeš (Windows).
- **Tajné tokeny** jen v `*.example` šablonách; reálné v `.gitignore`
  (`web/config.php`, `worker_config.json`). Před pushem nikdy necommitovat.
