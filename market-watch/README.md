# market-watch/ — storico e digest del market watch

Tutto in locale, versionato in git, ricercabile con `grep`.

## Struttura

| File | Ruolo | Chi lo legge/scrive |
|---|---|---|
| `ultimo.md` | Solo il giro più recente. **È ciò che `/aggiornami` legge.** | Scritto da `market-watch` a ogni run; sovrascritto |
| `storico/AAAA-MM-GG.md` | Un file per giro, immutabile. Dettaglio + fonti. | Scritto da `market-watch`; append di un nuovo file |
| `INDICE.md` | Una riga per giro, per ritrovare al volo un check passato. | Aggiornato da `market-watch` a ogni run |
| `outbox.md` | Righe `- ...` in attesa di spedizione su Telegram (via `telegram-notify.yml`). | Scritto da agents/routines; consumato dal workflow |

## Regola di lettura (per risparmiare token)
1. Per il digest del giorno → leggi **solo** `ultimo.md`.
2. Per un giro passato → cerca in `INDICE.md`, poi apri **solo** il file datato che serve.
3. Non caricare mai tutto lo storico insieme.

## Ponte Telegram bidirezionale

Due workflow GitHub, stessi secret (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), nessuna macchina accesa:

| Direzione | Workflow | Come funziona |
|---|---|---|
| **Uscita** (repo → Telegram) | `telegram-notify.yml` | A ogni push che tocca `outbox.md`, invia a Telegram le righe nuove `- ...` |
| **Entrata** (Telegram → repo) | `telegram-command.yml` + `tg-bridge.py` | Ogni ~5 min interroga il bot (`getUpdates`) ed esegue i comandi; risponde **solo** al chat autorizzato |

**Comandi in entrata** (senza AI): `ping`, `stato`, `cruscotto`, `aggiornami`, `aiuto`.

**Parere ragionato via Telegram** (`aggiornami`): gira **"a chiamata"** solo quando arriva il comando (non a timer). Lancia `claude -p` headless in sola lettura e invia il testo su Telegram. Serve un secret: `CLAUDE_CODE_OAUTH_TOKEN` (da `claude setup-token`) oppure `ANTHROPIC_API_KEY`. Senza il secret, `aggiornami` ripiega sulla foto rapida.

**Webhook istantaneo** (opzionale): vedi `webhook/README.md` per configurare un Cloudflare Worker che riduce la latenza da ~5-10 min a ~30-60 sec.

## Regola di archiviazione (a ogni run di `market-watch`)
- **Tesi chiuse** (`tier: chiusa` in `watchlist.json`) → spostate in `data/watchlist-archivio.json`.
- **Eventi passati** in `data/calendar.json` → spostati nell'array `archiviati`.
- **Note delle tesi**: si tengono gli ultimi **2 check** inline; i più vecchi restano solo nello storico datato.
- **Tesi attive**: massimo 8 (`rules.json → max_tesi_attive`); le più deboli si demotano a radar/archivio.
