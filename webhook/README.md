# Webhook Telegram (risposte istantanee)

Sostituisce il polling (lento, cron GitHub) con un **webhook**: Telegram chiama un
piccolo Worker Cloudflare (gratis, sempre acceso) a ogni messaggio/tap; il Worker
risponde subito al tap e inoltra l'update a GitHub Actions, che fa il lavoro vero
col codice Python. Latenza ~30-60s invece di 5-15 min, ed è affidabile.

```
Telegram → Worker Cloudflare → GitHub repository_dispatch → Actions (Python) → risposta
```

## Setup (una volta sola, ~15 minuti)

### 1) Token GitHub (per far scattare le Actions dal Worker)
GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token:
- **Repository access**: Only select repositories → `finance`
- **Permissions** → Repository permissions → **Contents: Read and write**
- Genera e **copia** il token (lo incolli al passo 3 come `GH_PAT`).

### 2) Crea il Worker su Cloudflare
- Vai su dash.cloudflare.com (crea un account gratuito se non ce l'hai).
- **Workers & Pages** → **Create** → **Create Worker** → dagli un nome (es. `tg-finance`) → **Deploy**.
- **Edit code** → cancella tutto e incolla il contenuto di [`webhook/worker.js`](worker.js) → **Deploy**.
- Annota l'URL del Worker: `https://tg-finance.<tuo-sottodominio>.workers.dev`

### 3) Variabili del Worker
Worker → **Settings** → **Variables and Secrets** → aggiungi:

| Nome | Tipo | Valore |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Secret | il token del bot (come in `.secrets/telegram.json`) |
| `GH_PAT` | Secret | il token del passo 1 |
| `WEBHOOK_SECRET` | Secret | una stringa a caso lunga (te la inventi, es. 30 caratteri) |
| `GH_OWNER` | Text | `YOUR_GITHUB_USERNAME` |
| `GH_REPO` | Text | `finance` |
| `TELEGRAM_CHAT_ID` | Text | `YOUR_TELEGRAM_CHAT_ID` |

Poi **Deploy** di nuovo per applicarle.

### 4) Registra il webhook su Telegram (dal tuo pc)
Usa lo **stesso** `WEBHOOK_SECRET` del passo 3:
```bash
cd ~/progetti/finance
python3 scripts/set-telegram-webhook.py set "https://tg-finance.<tuo-sottodominio>.workers.dev" "IL_TUO_WEBHOOK_SECRET"
python3 scripts/set-telegram-webhook.py info   # deve mostrare l'url, senza errori
```

### 5) Prova
Scrivi **`ping`** al bot → entro ~30-60s arriva **`pong`**. Poi prova **`cruscotto`**.
(I segreti lato Actions — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CLAUDE_CODE_OAUTH_TOKEN` —
sono già configurati nel repo: il workflow `telegram-webhook.yml` li usa così com'è.)

## Come funziona (per riferimento)
- **Ricezione**: `webhook/worker.js` (Cloudflare) → `repository_dispatch` → `.github/workflows/telegram-webhook.yml` → `tg-bridge.py handle-update`.
- **Consegna schede-ordine**: `.github/workflows/telegram-tickets.yml` (su push di `order-tickets.json`) → `tg-bridge.py send-tickets` (bottoni).
- **Tap sui bottoni**: Worker risponde subito, poi il webhook aggiorna il messaggio.

## Convivenza col polling di fallback
Il cron di `telegram-command.yml` resta attivo come rete di sicurezza, ma **si fa
da parte da solo** quando c'è un webhook: `tg-bridge` controlla `getWebhookInfo` e,
se trova un webhook, non fa `deleteWebhook`/`getUpdates`. Quindi non devi
disabilitare niente a mano: appena registri il webhook (passo 4), il poll smette
di intervenire; se un giorno rimuovi il webhook, il poll riprende da solo.

## Rollback al polling
```bash
python3 scripts/set-telegram-webhook.py delete
```
Il cron di fallback (ogni 5 min) riprende automaticamente. Fine.
