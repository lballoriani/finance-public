# Investimenti — Contesto operativo

Progetto personale di gestione investimenti. Obiettivo: crescita costante di lungo periodo tramite **PAC mensile in ETF** (quota fissa mensile, vedere `rules.json → pac`), più una **riserva speculativa** per azioni singole alimentata con un surplus mensile. I parametri esatti (importi, broker, obiettivo) sono in `data/rules.json` e `data/portfolio.json`.

## Come rispondere (stile) — PRIORITARIO

**Non sono un esperto di finanza**: risposte **semplici, senza gergo tecnico e senza prolissità**.

**Formato standard** (per "aggiornami" e per ogni proposta operativa): sempre una **tabella** con queste colonne —

| Titolo | ISIN (per cercarlo su TR) | Comprare a | Vendere a | In una riga |

- **Nome + ISIN**: la ricerca su Trade Republic va fatta per ISIN. Se l'ISIN non è verificato (es. IPO nuova), scriverlo esplicitamente ("da confermare in app").
- **Comprare a / Vendere a**: prezzo di ingresso e prezzo di uscita/target, **sempre in EUR**. Se il titolo è quotato in valuta estera (es. USD), convertire in € e segnalare il rischio cambio.
- **In una riga**: il contesto essenziale del titolo (catalizzatore, rischio cambio, prossima data chiave).

Dopo la tabella, **un po' di contesto in più** con **tono leggermente discorsivo/colloquiale**: spiegare in parole semplici cosa tenere d'occhio, drawdown/invalidazioni, riserva disponibile. Discorsivo ma **senza diventare lungo o tecnico**.

## Fonti di verità

| File | Contenuto |
|---|---|
| `data/portfolio.json` | Posizioni correnti (aggiornare `as_of` a ogni modifica) |
| `data/target.json` | Allocazione target del PAC (sempre classi **Acc**) |
| `data/rules.json` | Vincoli fiscali, broker e di rischio — **leggerlo prima di proporre qualsiasi operazione** |
| `data/watchlist.json` | Tesi speculative ATTIVE/radar con trigger + scenari (EV netto); campo `bucket: "lungo"\|"breve"` |
| `data/watchlist-archivio.json` | Tesi chiuse, uscite dalla watchlist |
| `data/calendar.json` | Calendario unico dei catalizzatori datati, ordinato per data |
| `data/minusvalenze.json` | Registro minusvalenze compensabili e loro utilizzo |
| `data/previsioni.json` | Registro quantitativo delle previsioni: probabilità dichiarate, scadenze, Brier |
| `data/universe.json` | Universo curato ~70 nomi monitorati dal catalyst-scanner |
| `data/lockup-tracker.json` | Scadenze lockup delle IPO recenti |
| `reports/track-record.md` | Pagella narrativa delle previsioni: cosa si è azzeccato/mancato, calibrazione |
| `market-watch/ultimo.md` | Digest del giro di market watch più recente |
| `market-watch/storico/` + `INDICE.md` | Storico dei giri e indice navigabile |

## Regole non negoziabili

1. **Ogni ipotesi di vendita passa prima dal subagent `tax-agent`**: mostrare sempre plusvalenza lorda, imposta 26% e ricavo netto.
2. Preferire sempre classi ad **accumulazione (Acc)** per differire la tassazione.
3. Watchlist speculativa: il budget per le azioni singole è **la liquidità della riserva su Trade Republic** disponibile. Il controllo del rischio è la **disciplina di sizing per singola tesi**: mai concentrare tutto su un nome, entrare a tranche sui catalizzatori, mai mediare al ribasso oltre l'invalidazione.
4. Ogni proposta operativa indica il **momento di ingresso** (finestra legata a catalizzatori osservabili) e un **range di uscita/rivendita** come scenari condizionali con condizioni di invalidazione — mai certezze. Numeri sempre netti post-tasse, fonti citate.
5. **Nessuno stop loss automatico**: segnalare drawdown dall'ingresso e invalidazione della tesi, ma la decisione di uscita spetta sempre all'utente.
6. Importi in EUR, date in formato ISO (YYYY-MM-DD).
7. Dopo ogni operazione eseguita realmente, aggiornare `portfolio.json` e generare un report in `reports/`.
8. Il PAC ETF non si dirotta MAI su azioni singole: gli acquisti speculativi attingono solo alla riserva dedicata.

## Disciplina di previsione e sizing (5 pratiche)

1. **Minusvalenze come credito fiscale** (`data/minusvalenze.json`): le perdite realizzate su azioni singole/ETC/certificati si compensano per 4 anni con future plusvalenze della stessa categoria (NON con ETF, NON con crypto). Registrarle e usarle prima di realizzare plusvalenze speculative.
2. **Scenari + valore atteso** (`watchlist.json` → `scenari`): ogni tesi attiva ha bull/base/bear con probabilità grezze e rendimento **netto** post-26%, più l'EV pesato. Deve rendere già nel caso base.
3. **Track record** (`reports/track-record.md` + `data/previsioni.json`): ogni previsione risolvibile viene pagellata (✅/⚠️/❌) e registrata. Antidoto all'eccesso di sicurezza.
4. **Calendario catalizzatori** (`data/calendar.json`): tutte le date chiave in un elenco ordinato.
5. **Sizing per convinzione** (`watchlist.json` → `sizing`): size più grande quando prob×EV è alto e la tesi non è binaria; piccola sulle binarie; aggiungere sulle conferme, mai mediare al ribasso oltre l'invalidazione.

## Subagent (`.claude/agents/`)

- **tax-agent** — plusvalenze nette al 26%; dipendenza obbligatoria di ogni vendita.
- **allocation-agent** — allocazione attuale vs target, scostamenti oltre ±5%.
- **rebalance-agent** — proposta di allocazione del PAC mensile.
- **goal-agent** — coerenza del portafoglio con l'obiettivo finanziario.
- **market-watch** — ricerca web sui trigger della watchlist; aggiorna `watchlist.json` e i file in `market-watch/`.
- **catalyst-scanner** — scansiona `data/universe.json` (~70 nomi) alla ricerca di 2-3 opportunità breve termine (2-8 settimane). Gira bisettimanalmente dopo il market-watch.

## Workflow ibrido

- **Interattivo (qui)**: decisioni operative. Prompt tipici: "Prepara il PAC di questo mese", "Quanto incasso netto se chiudo X?", "Lancia market-watch".
- **Automatico**: routine bisettimanale (lunedì e giovedì) che esegue il market watch e scrive i risultati in `market-watch/` (`ultimo.md` + `storico/AAAA-MM-GG.md` + `INDICE.md`).

## Comando "aggiornami" (`/aggiornami`)

Dopo che la routine cloud ha girato, scrivere **"aggiornami"** per ricevere la **lettura ragionata dei risultati del giorno**: cosa tenere d'occhio, su cosa puntare, trigger scattati e cosa significano, drawdown/invalidazioni, riserva disponibile. Il comando **non rifà la ricerca**: legge `market-watch/ultimo.md` e il contesto locale. Istruzioni complete in `.claude/commands/aggiornami.md`.
