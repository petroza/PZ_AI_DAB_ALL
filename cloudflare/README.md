# PZ AI DAB ALL — Cloudflare relay

Cloudový **relay** (Pages + D1 + R2), aby šlo dabovat z mobilu odkudkoliv:

```
mobil ──upload video──▶  Cloudflare (R2 + D1)  ◀──polling── PC worker (doma, GPU)
mobil ◀──stáhni dabing── Cloudflare (R2)        ──výsledek─▶
```

- **Frontend** (mobilní stránka) + **klientské API** `/api.php` a **worker API** `/worker_api.php` běží na Cloudflare Pages.
- **R2** drží soubory (vstupní videa + výstupy), **D1** metadata zakázek.
- Doma běží `dab_worker.py`, který si zakázky vyzvedává (polling) — stejný kontrakt jako dosavadní Forpsi relay, takže **stačí přesměrovat `base_url`**.
- Přihlášení na stránku: jedno **sdílené heslo** (`UI_PASSWORD`), worker chrání `WORKER_TOKEN`.

> Režim „malá videa": soubory tečou přes Worker do R2, takže **limit je ~100 MB na soubor** (limit těla requestu Workeru). Pro větší videa by byl potřeba přímý upload do R2 přes presigned URL.

## Struktura

```
cloudflare/
  wrangler.toml            konfigurace Pages + bindingy (D1, R2)
  schema.sql               tabulka jobs v D1
  functions/
    _middleware.js         přihlášení (sdílené heslo, cookie)
    _lib.js                sdílené helpery (D1/R2/auth)
    api.php.js             → /api.php  (mobil/web)
    worker_api.php.js      → /worker_api.php  (PC worker)
  public/
    index.html  app.js  style.css   mobilní frontend
```

## Nasazení (jednorázově)

Vše spouštěj ze složky `cloudflare/`.

```bash
# 1) R2 bucket
wrangler r2 bucket create pz-dab-files

# 2) D1 databáze — do wrangler.toml doplň vypsané database_id
wrangler d1 create pz_dab

# 3) schéma do D1
wrangler d1 execute pz_dab --remote --file=schema.sql

# 4) Pages projekt + první deploy
wrangler pages deploy public --project-name pz-ai-dab-all

# 5) secrety (Pages projekt)
wrangler pages secret put WORKER_TOKEN     # STEJNÝ jako ve worker_config.json
wrangler pages secret put UI_PASSWORD      # heslo do mobilní stránky
wrangler pages secret put SESSION_SECRET   # náhodný dlouhý řetězec
```

Po nasazení máš adresu `https://pz-ai-dab-all.pages.dev`.

Další deploye už jen:
```bash
wrangler pages deploy public --project-name pz-ai-dab-all
```

## Napojení domácího workeru

V `O:\ALLDUB\worker_config.json` přepni relay na Cloudflare (viz
`worker_config.cloudflare.example.json`):

```json
{
  "base_url": "https://pz-ai-dab-all.pages.dev",
  "worker_token": "STEJNÝ_JAKO_WORKER_TOKEN_SECRET",
  "poll_interval_sec": 5
}
```

Pak doma spusť `tools\start\START_DABWORKER.bat` (a pro XTTS `START_XTTS.bat`).
`dab_worker.py` se nemění — jen čte tuhle konfiguraci.

## Ověření

- `https://…pages.dev/` → přihlašovací stránka (heslo = `UI_PASSWORD`).
- Po přihlášení nahraj krátké video → zakázka „Ve frontě".
- Doma běžící worker ji vyzvedne → „Zpracovává se" → „Hotovo" → přehraj/stáhni.
- `GET /worker_api.php?action=worker_claim` bez tokenu musí vrátit `403`.

## Poznámky / limity

- **~100 MB na soubor** (Worker request limit). Delší HD videa nahraj menší/komprimovaná.
- **D1 free**: ~5M čtení/den — polling à 5 s je pár tisíc/den, pohodlně se vejde.
- **R2 free**: 10 GB úložiště. Mazáním hotových zakázek se uvolňuje.
- Soubory ani secrety nejsou v repu — hodnoty jen přes `wrangler … secret put`.
