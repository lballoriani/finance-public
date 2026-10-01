# Personal Investment Manager (Claude Code + GitHub Actions + Telegram)

A personal investment tracking system powered by **Claude Code** as an AI assistant, with automated market monitoring via **GitHub Actions** and a **Telegram bot** for mobile access.

> This is a personal tool, not financial advice. All investment decisions remain with the user.

## What it does

- **PAC tracking**: monthly ETF accumulation plan with automatic rebalancing suggestions
- **Speculative watchlist**: thesis-based investing with bull/base/bear scenarios, expected value, and sizing discipline
- **Market watch** (bi-weekly, automated): Claude scans the watchlist and focus sectors, updates theses, writes a digest
- **Catalyst scanner** (bi-weekly, automated): finds 2-3 short-term plays (2-8 weeks) from a curated ~70-stock universe
- **IPO watch** (every 6h, automated): monitors EDGAR for public S-1 filings of tracked IPO candidates
- **Reliable prices** (weekdays, automated): quotes from Yahoo Finance + official ECB FX rates instead of web search, plus a mechanical "movers" filter on the universe
- **Honest scorecard**: does the speculative reserve actually beat simply buying the ETF with the same money on the same dates?
- **Parked limit orders**: tracks long-lived limit orders on the broker — distance to the limit, likely fills, expiry, cash they lock up
- **Telegram bot**: `stato` (portfolio snapshot), `cruscotto` (full dashboard), `aggiornami` (AI-powered digest on demand), order tickets with inline buttons

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub repo (source of truth)                              │
│  ├── data/          ← portfolio, watchlist, rules, calendar │
│  ├── market-watch/  ← digests, outbox, order tickets        │
│  └── reports/       ← operation reports, track record       │
└──────────────┬──────────────────────────────────────────────┘
               │
  ┌────────────┴──────────────────────────────────┐
  │  GitHub Actions (serverless, free tier)        │
  │  ├── market-watch    (Mon/Thu ~13:00 UTC)      │ ← Claude Code agent
  │  ├── catalyst-scanner(Mon/Thu ~13:30 UTC)      │ ← Claude Code agent
  │  ├── ipo-watch       (every 6h, no AI)         │
  │  ├── weekly-digest   (Sun 17:00 UTC, no AI)    │
  │  ├── telegram-command(every 5min, on-demand AI)│
  │  ├── telegram-webhook(on push, on-demand AI)   │
  │  ├── telegram-notify (on push to outbox.md)    │
  │  └── telegram-tickets(on push to order-tickets)│
  └──────────┬─────────────────────────┬───────────┘
             │                         │
    ┌────────┴──────┐        ┌─────────┴──────────┐
    │  Telegram bot  │        │  Cloudflare Worker  │
    │  (outgoing)    │        │  (webhook, instant) │
    └────────────────┘        └─────────────────────┘
```

**Key design principle**: Claude is invoked **only when needed** — not on every cron tick. The `aggiornami` AI digest fires only when the user explicitly requests it, keeping costs minimal (~3 times/week typical usage).

## Components

### Python scripts (`market-watch/`)

| Script | Role | AI? |
|---|---|---|
| `cruscotto.py` | Portfolio dashboard (patrimony, P&L, ETF vs target, emergency fund) | No |
| `tg-bridge.py` | Telegram bot: polling + webhook, command handler, order tickets with inline buttons | Triggers Claude only for `aggiornami` |
| `ipo-watch.py` | Monitors EDGAR full-text search for S-1/F-1 filings of tracked IPO candidates | No |
| `weekly-digest.py` | Sunday digest of upcoming catalysts from `data/calendar.json` | No |
| `notify.sh` | Helper to send Telegram messages (local use) | No |

### Python scripts (`scripts/`)

| Script | Role |
|---|---|
| `tr-sync.py` | Read-only sync with Trade Republic via unofficial `pytr` library — writes a staging snapshot, never modifies `portfolio.json` directly |
| `exit-check.py` | Distance to take-profit levels in `watchlist.json → alert.piano_uscita`; generates sell order ticket JSON |
| `set-telegram-webhook.py` | Register/delete the Telegram webhook (Cloudflare Worker URL) |
| `quotes.py` | Prices from Yahoo Finance (chart API, no key) converted to EUR at the ECB daily rate → `data/quotes.json`; flags stale feeds and one-day jumps. `movers` scans `universe.json` for big drops or jumps on high volume → `data/movers.json`, for the catalyst-scanner to check. Exists because web-search prices proved wrong often enough to fire false triggers |
| `pagella.py` | Scorecard: speculative reserve vs a "twin" that put the same cash flows, on the same dates, into the main ETF of `target.json`. Trades inside the reserve aren't flows; deposits and sweeps are. Monthly snapshot; after 6 months, trailing by more than 5 points means the method isn't adding value |
| `ranking.py` | Ranks active theses by **net monthly return**, `(1 + EV) ** (1 / months) - 1`: +15% in 6 weeks beats +40% in 18 months when capital can be recycled |
| `ordini.py` | Register of parked limit orders (`data/ordini-aperti.json`): distance to limit, "probably filled", expiring soon, still to place, cash locked by buy orders. Monitoring only — orders are placed by hand |

### GitHub Actions (`.github/workflows/`)

> **Note:** in this public copy the cron schedules are commented out, so nothing runs or commits here. The live instance runs in a private repo. In your fork, uncomment the `schedule:` lines to enable them.

| Workflow | Trigger | Role |
|---|---|---|
| `telegram-command.yml` | Every 5min (fallback poll) | Telegram command bridge: ping/stato/cruscotto/aggiornami. Poll deactivates automatically when webhook is active. |
| `telegram-webhook.yml` | `repository_dispatch` (from Cloudflare Worker) | Same as above but event-driven (~30-60s latency instead of 5-15min) |
| `telegram-notify.yml` | Push to `main` touching `outbox.md` | Delivers outbox lines to Telegram |
| `telegram-tickets.yml` | Push to `main` touching `order-tickets.json` | Sends order tickets with inline buttons |
| `weekly-digest.yml` | Sunday 17:00 UTC | Sends upcoming catalysts digest |
| `ipo-watch.yml` | Every 6h | Monitors EDGAR for new public IPO filings |
| `catalyst-scanner.yml` | Mon/Thu 13:33 UTC | Claude agent scans universe for short-term plays |
| `quotes.yml` | Weekdays 12:45 + 17:05 UTC | Refreshes `quotes.json` + `movers.json` (no AI) |

### Claude Code agents (`.claude/agents/`)

| Agent | Role |
|---|---|
| `market-watch` | Deep bi-weekly scan: watchlist theses, sector news, IPO candidates, digest writing |
| `catalyst-scanner` | Short-term play finder (2-8 weeks): post-event bottom, lockup expiry, imminent catalyst |
| `market-watch-daily` | Lightweight daily guardian: checks only "hot" theses near an entry/exit level |
| `tax-agent` | Italian tax calculation (26% capital gains, compensable losses) — mandatory before any sell |
| `allocation-agent` | Current vs target allocation, drift beyond ±5% |
| `rebalance-agent` | Monthly PAC allocation proposal |
| `goal-agent` | Portfolio coherence with the financial goal |

### Cloudflare Worker (`webhook/worker.js`)

Receives Telegram updates, immediately acks button taps (stops the spinner), and dispatches to GitHub Actions via `repository_dispatch`. Free tier, always on.

## Setup

### 1. Fork / clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/finance.git
cd finance
```

Edit `data/portfolio.json`, `data/target.json`, `data/rules.json` with your actual data.

### 2. Create a Telegram bot

1. Chat with [@BotFather](https://t.me/BotFather) on Telegram → `/newbot`
2. Save the `BOT_TOKEN`
3. Get your `CHAT_ID` (e.g., via [@userinfobot](https://t.me/userinfobot))

### 3. Configure GitHub Secrets

In your repo → **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your personal chat ID |
| `CLAUDE_CODE_OAUTH_TOKEN` | From `claude setup-token` (uses your Claude plan) **OR** |
| `ANTHROPIC_API_KEY` | Anthropic API key (alternative to OAuth token) |

Without `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`: `aggiornami` falls back to the quick portfolio snapshot; all other commands work fine.

### 4. (Optional) Cloudflare Worker for instant responses

Follow [`webhook/README.md`](webhook/README.md) to set up the Cloudflare Worker. Reduces Telegram response latency from ~5-15 min to ~30-60 sec.

### 5. Trade Republic sync (local only)

```bash
python3 -m venv ~/.venvs/tr
~/.venvs/tr/bin/pip install pytr
~/.venvs/tr/bin/python scripts/tr-sync.py sync   # first login: 2FA via app
```

See [`scripts/README-tr-sync.md`](scripts/README-tr-sync.md) for details. The sync is **read-only** (no orders ever); it writes a staging snapshot for review, never modifies `portfolio.json` directly.

## Usage examples

Interactive session with Claude Code (`claude` in the repo directory):

```
Prepara il PAC di questo mese          → rebalance-agent proposes allocation
Quanto incasso netto se chiudo X?      → tax-agent: gross gain, 26% tax, net
Com'è messa l'allocazione?             → allocation-agent: current vs target
aggiornami                             → /aggiornami: reads ultimo.md + context
Ho comprato X azioni di Y a €Z         → updates portfolio.json + generates report
```

Telegram commands (via bot):
- `ping` — bot is alive
- `stato` — quick snapshot (reserve 🟢long/🟡short, positions, next events)
- `cruscotto` — full dashboard (total portfolio, P&L, ETF vs target, emergency fund)
- `aggiornami` — AI-powered digest (~1 min, uses Claude)
- `aiuto` — command list

## Data files

> All amounts, holdings and ETFs in `data/` are **made-up examples**. Replace them with your own.

| File | Edit manually? | Updated by |
|---|---|---|
| `data/portfolio.json` | Yes (after each trade) | `tr-sync.py` (staging), user review |
| `data/target.json` | Yes (allocation; `yahoo` symbol per ETF for `quotes.py`) | — |
| `data/rules.json` | Yes (to change strategy) | — |
| `data/watchlist.json` | Yes (add/edit theses) | `market-watch`, `catalyst-scanner` |
| `data/calendar.json` | Yes (add events) | `market-watch` |
| `data/universe.json` | Yes (quarterly refresh) | — |
| `data/minusvalenze.json` | On realized losses | `market-watch` |
| `data/previsioni.json` | — | `market-watch` |
| `data/lockup-tracker.json` | — | `catalyst-scanner` |
| `data/ordini-aperti.json` | Yes (via `ordini.py set`) | `ordini.py` |
| `data/pagella-riserva.json` | Flows via `pagella.py flusso` | `pagella.py snapshot` |
| `data/quotes.json`, `data/movers.json` | — (generated, not committed here) | `quotes.py` / `quotes.yml` |

## Security notes

- The Telegram bot **only responds to the authorized `TELEGRAM_CHAT_ID`** — all other chats are ignored.
- `tg-bridge.py` ignores messages older than 2 hours (no backlog replay).
- `tr-sync.py` is **strictly read-only**: it never places orders and never modifies `portfolio.json` directly.
- Secrets are stored as GitHub Actions secrets, never committed.
- `market-watch/.secrets/` is in `.gitignore`.

## License

MIT
