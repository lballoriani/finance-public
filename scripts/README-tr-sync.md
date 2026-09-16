# Sync Trade Republic (sola lettura)

`scripts/tr-sync.py` legge il conto Trade Republic **in sola lettura** e lo
confronta con `data/portfolio.json`. Serve a togliere l'aggiornamento manuale del
portafoglio e a uccidere i falsi trigger da prezzi web sbagliati: i numeri
arrivano dal conto vero.

> **Non piazza mai ordini.** TR non ha un'API ufficiale e comandare ordini via
> codice è impossibile/contro le condizioni — oltre che contro la regola d'oro
> del progetto (ogni decisione operativa è di Luca). Qui si **legge e basta**.
> Gira in dry-run: scrive uno snapshot di staging e stampa il confronto, senza
> mai sovrascrivere `portfolio.json` da solo.

## Setup (una volta sola, in locale — NON in CI)

Le credenziali TR non vanno su GitHub: questo strumento si usa dal tuo pc.

Il Python di sistema (Debian/Ubuntu, PEP 668) è "protetto": `pip install` diretto
dà `externally-managed-environment`. La via pulita è un **ambiente virtuale**
dedicato, una volta sola:

```bash
python3 -m venv ~/.venvs/tr           # crea il venv (serve python3-full: sudo apt install python3-full)
~/.venvs/tr/bin/pip install pytr      # installa pytr solo lì dentro
~/.venvs/tr/bin/python scripts/tr-sync.py sync
```

Al **primo avvio** `pytr` chiede numero di telefono + PIN e fa partire la **2FA**
(codice via SMS o tap sull'app TR). Fatto il login, la sessione resta in cache in
`~/.pytr/`: i sync successivi **non** richiedono più il 2FA finché il cookie è
valido. Puoi anche passare le credenziali via variabili d'ambiente per non
digitarle ogni volta (restano sul tuo pc, mai nel repo):

```bash
export TR_PHONE="+39..."   # opzionale
export TR_PIN="1234"       # opzionale
~/.venvs/tr/bin/python scripts/tr-sync.py sync
```

> In alternativa al venv: `pipx install pytr` (gestisce il venv da solo), oppure
> — sconsigliato — `pip install --break-system-packages pytr`. Il venv resta
> l'opzione più sicura perché isola pytr dal Python di sistema.

## Cosa fa un `sync`

1. Legge posizioni e liquidità da TR.
2. Scrive lo snapshot in `data/portfolio-tr-snapshot.json` (ignorato da git).
3. Stampa il **confronto** con `portfolio.json`: per ogni posizione mostra valore
   attuale vs valore TR con lo scostamento (Δ), segnala la liquidità/riserva e le
   eventuali **posizioni nuove** presenti su TR ma non ancora nel portafoglio
   (es. un acquisto appena eseguito dopo un "Comprato ✅" su Telegram).

Non modifica nulla: il **merge** dei numeri veri in `portfolio.json` (e il report
in `reports/`) è un passo separato e rivisto in sessione con Claude. Così i trade
confermati su Telegram (`market-watch/trades.md`) vengono riconciliati con
quantità, prezzo e fee reali.

## Primo collaudo

La logica di parsing/diff è già testata (`python3 scripts/tr-sync.py selftest`),
ma i **nomi esatti dei campi** che TR restituisce possono variare tra versioni di
`pytr`. Al primo `sync` reale, se qualche valore risultasse vuoto, condividi
l'output (o lo snapshot) e si sistema al volo il mapping in `normalize_tr()`.

## Note

- **Login**: lo script usa il flusso **v2** (approvazione dall'app TR o codice
  authenticator), che evita la dipendenza da `playwright`/WAF del login web
  classico. Al primo `sync` conferma la richiesta sull'app Trade Republic. Se
  proprio volessi il login classico serve `pip install 'pytr[playwright]'` +
  `playwright install chromium` e `TR_V2=0`, ma non è necessario.
- `pytr` è una libreria **non ufficiale** (reverse-engineering dell'app): può
  smettere di funzionare se TR cambia le API. È un aiuto alla lettura, non una
  dipendenza critica: il portafoglio resta gestibile a mano.
- La liquidità su TR è un **unico salvadanaio**: lo script la legge come "riserva
  speculativa". Se hai appena versato il PAC, conferma tu la ripartizione tra
  quota ETF e riserva.
