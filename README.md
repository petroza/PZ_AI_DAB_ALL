# PZ AI DAB ALL

**Automatický dabing videa — lokálně, offline, čeština na prvním místě.**
Vezme video v jednom jazyce a vrátí ho kompletně **nadabované** v jiném:
přepíše řeč → přeloží → vygeneruje hlas → časově zarovná → vmíchá zpět do videa.

> Spojuje dva hotové projekty do jedné aplikace:
> [AutoSRT-EN-CZ-all](https://github.com/petroza/AutoSRT-EN-CZ-all) (přepis + překlad)
> a [PZ Voice Studio](https://github.com/petroza/PZ_AI_voice) (generování hlasu).
> Např. **ruština → angličtina**, a hlavně **cokoliv → čeština**.

Vše běží na tvém počítači (CPU), bez cloudu. Jediné připojení k internetu je při
instalaci (pip) a ručním stažení modelů/nástrojů/hlasů.

---

## Co aplikace dělá (pipeline)

```
video → [ffmpeg] WAV 16k → [parakeet.cpp] segmenty s časy → [LLM/Ollama] překlad
      → [Piper / PZ Voice Studio] TTS klipy → [rubberband/atempo] zarovnání na slot
      → [mixer] souvislá stopa (+ volitelný ducking) → [ffmpeg] mux do videa
      → (volitelně) zapečené titulky
```

**Výstup:** nadabované `*.dubbed.mp4` + titulky ve zdrojovém i cílovém jazyce
(`.src.srt`, `.<cíl>.srt`) + `.json` se segmenty. U čistě audio vstupu vznikne
nadabované `*.dubbed.mp3`.

---

## Rychlý start (Windows)

1. **Rozbal** projekt kamkoliv (např. `C:\PZ_AI_DAB_ALL`).
2. Spusť **`INSTALL.bat`** — ověří Python 3.11+, vytvoří `.venv`, nainstaluje
   závislosti a vypíše, co ještě chybí.
3. Doplň ručně (viz níže): **parakeet-cli + ASR model**, **ffmpeg**,
   **Piper + český hlas**. (Volitelně Ollama pro překlad, PZ Voice Studio pro Chatterbox.)
4. Spusť **`START.bat`** → otevře se prohlížeč na **http://127.0.0.1:8790**.
5. Diagnostika kdykoliv přes **`CHECK.bat`**.

---

## Co doplnit ručně (a kam)

### 1) ASR — parakeet.cpp + model  → `tools\parakeet\` a `models\`
- `parakeet-cli.exe` (Windows CPU build) z
  <https://github.com/mudler/parakeet.cpp/releases> → `tools\parakeet\`
- jeden `.gguf` model z <https://huggingface.co/mudler/parakeet-cpp-gguf>
  (doporučeno `tdt-0.6b-v3-q8_0.gguf`, 25 evropských jazyků vč. češtiny) → `models\`

### 2) ffmpeg  → `tools\ffmpeg\` (nebo PATH)
`ffmpeg.exe` + `ffprobe.exe` z <https://www.gyan.dev/ffmpeg/builds/>.

### 3) TTS — Piper + hlas  → `tools\piper\` a `voices\`
- `piper(.exe)` z <https://github.com/rhasspy/piper/releases> → `tools\piper\`
  (nebo `pip install piper-tts`)
- hlas `*.onnx` **+** `*.onnx.json` z
  <https://huggingface.co/rhasspy/piper-voices> → `voices\`
  Pro češtinu: `cs_CZ-jirka-medium`. Stačí vložit, app si hlas najde podle jazyka.

### 4) (volitelně) Překlad — Ollama
Nainstaluj <https://ollama.com> a model (`ollama pull gemma4`). Bez Ollamy se
překlad přeskočí (dabing pak zůstane ve zdrojovém jazyce).

### 5) (volitelně) Chatterbox / klonování hlasu — PZ Voice Studio
Spusť samostatně [PZ Voice Studio](https://github.com/petroza/PZ_AI_voice)
(port 7867) a v nastavení dabingu zvol engine **PZ Voice Studio**. DAB ALL pak
hlas generuje přes jeho HTTP API (Piper i Chatterbox, referenční klonování).

### Rubberband (volitelné, kvalitnější time-stretch)  → `tools\rubberband\`
`rubberband.exe` z <https://breakfastquay.com/rubberband/>. Když chybí, použije
se `atempo` z ffmpeg (taky zachová výšku, jen o něco hůř u velkých roztažení).

---

## Použití

1. Přetáhni video (nebo audio) do drop zóny.
2. Vyber **zdrojový** a **cílový** jazyk (cíl = čeština jako výchozí).
3. Zvol **hlasový engine** (Piper = offline, výchozí), případně konkrétní hlas.
4. Zvol režim zvuku: **Nahradit** dabingem, nebo **Voice-over** přes ztlumený originál.
5. Volitelně zaškrtni **zapečení titulků** a **LLM korekci** přepisu.
6. **Spustit dabing** → sleduj progres → stáhni výsledné video / titulky.

---

## Konfigurace (proměnné prostředí)

| Proměnná | Výchozí | Význam |
|---|---|---|
| `DAB_PORT` | `8790` | port webu |
| `DAB_TTS_ENGINE` | `piper` | výchozí TTS (`piper` / `voicestudio`) |
| `DAB_AUDIO_MODE` | `replace` | `replace` / `voiceover` |
| `DAB_DUCK_DB` | `-14` | ztlumení originálu ve voice-over [dB] |
| `DAB_TIMESTRETCH` | `auto` | `auto` / `rubberband` / `atempo` / `off` |
| `DAB_MAX_TEMPO` / `DAB_MIN_TEMPO` | `1.5` / `0.75` | meze zrychlení/zpomalení klipu |
| `DAB_VOICESTUDIO_URL` | `http://127.0.0.1:7867` | adresa PZ Voice Studia |
| `DAB_VS_ENGINE` | `Piper` | engine ve Studiu (např. `Chatterbox 500M - quality`) |
| `DAB_PIPER_EXE` / `DAB_RUBBERBAND_EXE` | (auto) | přímé cesty k binárkám |
| `PZ_MODEL_PATH` / `PZ_PARAKEET_EXE` / `PZ_FFMPEG_EXE` | (auto) | ASR nástroje (z AutoSRT) |
| `PZ_OLLAMA_URL` / `PZ_OLLAMA_MODEL` | (viz engines/asr) | překlad + korekce |

---

## Architektura

```
PZ_AI_DAB_ALL/
├─ app/            FastAPI UI + API, fronta jobů, ORCHESTRÁTOR pipeline
│   ├─ main.py         endpointy + statika
│   ├─ pipeline.py     řetěz: extrakce→ASR→překlad→TTS→zarovnání→mix→mux
│   ├─ job_manager.py  stav jobů (JSON na disk)
│   └─ config.py       centrální konfigurace
├─ engines/
│   ├─ asr/         přepis + překlad (vendorováno z AutoSRT-EN-CZ-all)
│   └─ tts/         hlas: piper.py (offline) + voicestudio.py (HTTP klient)
├─ dub/            JÁDRO DABINGU (nové)
│   ├─ align.py        time-stretch klipu na délku slotu (zachová výšku)
│   ├─ mixer.py        klipy na časovou osu + ducking
│   └─ mux.py          vmíchání zvuku do videa
├─ frontend/       index.html + app.js + style.css
├─ models/ tools/ voices/   ← sem binárky/modely/hlasy (mimo git)
├─ uploads/ outputs/ jobs/ logs/ work/
└─ INSTALL.bat  START.bat  CHECK.bat  requirements.txt
```

Princip: **ASR a překlad se přebírají z AutoSRT, hlas z Piperu/PZ Voice Studia;
nové je jen jádro `dub/` a orchestrátor.** Když parakeet/ffmpeg/Ollama změní
rozhraní, mění se to v `engines/asr/` (jeden zdroj pravdy).

---

## Volba hlasu: Piper vs PZ Voice Studio

- **Piper** (výchozí) — rychlý, plně offline, CPU, pevné hlasy. Český hlas
  `cs_CZ-jirka`. Drží slib „bez cloudu, na CPU".
- **PZ Voice Studio** — připoj běžící Studio a využij **Chatterbox**
  (vyšší kvalita, **klonování hlasu** původního mluvčího, multilingual vč.
  češtiny a ruštiny). Ideálně s GPU.

---

## Řešení častých chyb

- **„parakeet-cli nebyl nalezen" / „žádný .gguf model"** → viz krok 1.
- **„Piper hlas … nenalezen"** → vlož `*.onnx` + `*.onnx.json` do `voices\` (krok 3).
- **Dabing zůstal v původním jazyce** → neběží Ollama (překlad se přeskočil), viz krok 4.
- **Řeč zní uspěchaně** → překlad je delší než slot; zvyš `DAB_MAX_TEMPO` níž
  (méně zrychlení) nebo použij rubberband; lze i zkrátit překlad.
- **Port 8790 obsazený** → `set DAB_PORT=8791` a pak `START.bat`.

---

## Soukromí

Přepis, překlad i syntéza běží lokálně. Žádné audio ani text neopouští tvůj
počítač (pokud výslovně nezvolíš cloudový překladač/hlas, který tu není výchozí).

## Zdroje

- parakeet.cpp — <https://github.com/mudler/parakeet.cpp>
- GGUF modely — <https://huggingface.co/mudler/parakeet-cpp-gguf>
- Piper — <https://github.com/rhasspy/piper> · hlasy <https://huggingface.co/rhasspy/piper-voices>
- Chatterbox — <https://github.com/resemble-ai/chatterbox>
- Ollama — <https://ollama.com>

## Licence

MIT © 2026 Petr Závorka — viz [LICENSE](LICENSE).
