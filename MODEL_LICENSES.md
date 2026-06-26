# Licence modelů a komponent třetích stran

Tento dokument shrnuje licenční podmínky **modelů a nástrojů třetích stran**, které
aplikace používá. **Licence vlastního kódu** projektu (MIT, viz [LICENSE](LICENSE))
se NEVZTAHUJE na tyto externí modely – ty mají vlastní, samostatné podmínky.

> ⚠️ **Upozornění:** Jde o obecný technický přehled, **nikoli právní radu**.
> Licenční podmínky se mohou měnit a u některých modelů se liší podle konkrétní
> verze/hlasu. Před jakýmkoli **produkčním, komerčním nebo veřejně šířeným**
> použitím (vč. vysílání či jiné distribuce výstupů) si aktuální znění licencí
> ověřte u zdroje a v případě potřeby konzultujte s právním oddělením.

---

## Přehled

| Komponenta | Role | Licence (dle zdroje) | Komerční / produkční použití |
|---|---|---|---|
| **XTTS v2** (engine `xtts`) | TTS + klonování hlasu | Coqui Public Model License (CPML) | **NE – pouze nekomerční** |
| **Piper** (engine `piper`) | TTS (offline) | engine MIT/Apache; **hlasy: licence se liší** | engine ano; **hlas ověřit jednotlivě** |
| **Chatterbox** (Voice Studio) | TTS | MIT | ano |
| **faster-whisper / Whisper** | přepis (ASR) | MIT | ano |
| **Gemma** (přes Ollamu) | překlad / korekce | Gemma Terms of Use (Google) | ano, se závaznou Use Policy |
| **Google Translate (web endpoint)** | překlad (volitelně) | neoficiální endpoint, podléhá ToS Google | **pro produkci nevhodné** – viz níže |
| **DeepL** (volitelně) | překlad (volitelně) | komerční API (placené plány) | ano dle zvoleného plánu/klíče |
| **ffmpeg** | zpracování AV | LGPL 2.1+ / GPL (dle buildu) | ano; při distribuci řešit (L)GPL |
| **rubberband** | time-stretch | GPL v2 / komerční (duální) | interně ano; jinak řešit licenci |
| **argostranslate** | offline překlad (fallback) | engine MIT; jazykové balíčky se liší | engine ano; balíček ověřit |
| **num2words** | čísla → slova (TTS) | LGPL | ano |

---

## Detaily

### XTTS v2 — pozor (nekomerční)
- Model `tts_models/multilingual/multi-dataset/xtts_v2` je licencován pod
  **Coqui Public Model License (CPML)**, která omezuje použití na **nekomerční**.
- Kód **automaticky odsouhlasí** podmínky proměnnou `COQUI_TOS_AGREED=1`
  (`tools/xtts_server.py`, `START_XTTS.bat`). Tím se akceptuje licence Coqui.
- **Doporučení:** XTTS používat pouze jako interní / náhledový / nekomerční nástroj.
  Pro jakékoli komerční či veřejně šířené výstupy zvolit jiný engine.

### Piper
- Samotný engine je permisivní (MIT/Apache). **Jednotlivé hlasy** (`.onnx`) ale
  pocházejí z různých datasetů a mají **různé licence** (např. MIT, CC0, CC-BY,
  CC-BY-SA, někdy jen pro výzkum). U každého použitého hlasu je nutné ověřit jeho
  vlastní licenci (model card daného hlasu) – zvlášť pro komerční/produkční výstup.

### Chatterbox, faster-whisper
- Permisivní (MIT). Komerční použití bez zvláštních omezení (vždy dodržet znění licence).

### Gemma
- Použití se řídí **Gemma Terms of Use** a **Gemma Prohibited Use Policy** (Google).
  Komerční použití je povoleno při dodržení těchto podmínek (nejde o OSI „open source",
  ale o licenci s pravidly užití).

### Překladové online služby
- **Google Translate** přes neoficiální webový endpoint (`translate_a/single`) je
  vhodný jen pro testování/osobní použití; pro **produkční/komerční** nasazení
  použít oficiální **Google Cloud Translation API** (placené, s vlastními podmínkami).
- **DeepL** vyžaduje API klíč a odpovídající (placený) plán.

### ffmpeg / rubberband
- **ffmpeg**: licence závisí na konkrétním buildu (LGPL 2.1+ vs GPL podle zapnutých
  knihoven). Pro interní použití zpravidla bez problému; při **distribuci** binárek
  je nutné dodržet (L)GPL povinnosti.
- **rubberband**: duální licence **GPL v2 / komerční**. Volání CLI nástroje interně
  je v pořádku; pro distribuci/komerční produkt případně řešit komerční licenci.

---

## Klonování hlasu — osobnostní práva
Funkce klonování hlasu (XTTS) může zpracovávat hlas **konkrétní fyzické osoby**.
Nad rámec licence modelu se zde uplatňují **osobnostní práva a ochrana osobních
údajů** (hlas jako biometrický/osobní údaj, právo na podobu/hlas, GDPR). Klonování
a šíření hlasu reálné osoby vyžaduje její **souhlas** a posouzení právního základu.
Vyhněte se vytváření obsahu, který by mohl **uvádět v omyl** ohledně toho, co daná
osoba skutečně řekla.

---

## Doporučení pro produkční / komerční / veřejně šířené výstupy
1. **Nepoužívat XTTS klon** – licenčně nekomerční a navíc rizikové u reálných hlasů.
2. Volit **titulky** (deterministický překlad, bez práv k hlasu) nebo **voice-over
   neutrálním hlasem** (Piper s ověřeným hlasem / Chatterbox) **přes ztlumený originál**.
3. U překladu pro produkci použít **oficiální placené API** (Google Cloud / DeepL),
   ne neoficiální webový endpoint.
4. U hlasu reálné osoby zajistit **souhlas** a neuvádět diváka v omyl.
5. Finální posouzení pro konkrétní nasazení nechat na **právním oddělení**.

*Tento přehled není právní rada a nezakládá žádné záruky. Aktuální znění licencí
má vždy přednost.*
