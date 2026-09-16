---
description: La mia lettura ragionata dei risultati del market watch del giorno (cosa tenere d'occhio, su cosa puntare)
---

# /aggiornami — Digest ragionato del market watch del giorno

La routine cloud "Market watch investimenti (bisettimanale)" gira ogni **lunedì e giovedì alle 15:00** (ora italiana) e scrive i risultati in file locali. Questo comando **non rifà la ricerca**: legge i risultati già prodotti e restituisce **il mio pensiero** su di essi.

## Cosa fare quando viene invocato

1. **Allinea PRIMA la copia locale al remoto**: esegui `git pull --rebase` (la routine cloud scrive i risultati sul
   remoto ogni lun/gio; la copia locale può essere indietro — es. al rientro dalle ferie o tra una sessione e l'altra).
   - Se il pull è pulito (fast-forward) → prosegui.
   - Se ci sono **commit locali non ancora pushati** (divergenza), il rebase li riapplica sopra il remoto; se emergono
     **conflitti**, **fermati e segnalali a Luca** invece di risolverli alla cieca.
   - Promemoria: a fine sessione **pusha** i commit locali, così il prossimo giro cloud parte dai dati giusti ed
     evitiamo disallineamenti futuri.

   **1-bis. Allinea il portafoglio ai numeri reali di TR.**
   - **In cloud** (variabile `GITHUB_ACTIONS` presente): salta **in silenzio** (mancano le credenziali TR — atteso).
   - **In locale, in due tempi:**
     1. **Prova rapida (resume-only, non blocca):**
        `TR_NONINTERACTIVE=1 timeout 90 ~/.venvs/tr/bin/python scripts/tr-sync.py sync`
        - **exit 0** → usa i valori/saldi veri (snapshot `data/portfolio-tr-snapshot.json` + diff vs `portfolio.json`)
          nel digest e segnala scostamenti rilevanti o posizioni nuove su TR (es. acquisto in `market-watch/trades.md`
          da riconciliare). **FINITO.**
        - errore **NON** di sessione (venv/pytr assente, rete, ecc.) → **avvisa esplicitamente** Luca (motivo +
          "i valori potrebbero non essere aggiornati") e prosegui coi valori in `portfolio.json`.
        - errore di **sessione scaduta** (exit 3, messaggio con "sessione TR scaduta") → passa al punto 2.
     2. **Re-login col 2FA, durante l'aggiornami:** DILLO a Luca prima di lanciare —
        "🔐 Sessione TR scaduta: ti arriva una notifica sull'app Trade Republic, **approvala** per riallineare il conto
        (hai ~2 min)" — poi esegui il login interattivo (fa partire la richiesta di approvazione sul telefono):
        `timeout 140 ~/.venvs/tr/bin/python scripts/tr-sync.py sync`
        - **exit 0** → "✅ conto TR riallineato": usa i valori veri.
        - **exit ≠ 0 / timeout** (non approvato in tempo o altro) → **avvisa esplicitamente** e prosegui coi valori in
          `portfolio.json`.
   - Il **merge** dei valori in `portfolio.json` resta un passo rivisto: se ci sono scostamenti importanti o un trade
     da riconciliare, proponilo a Luca — non riscrivere il file in automatico.

2. **Leggi il giro più recente** dal file `market-watch/ultimo.md` (è il digest dell'ultimo check: trigger, stato
   per tesi, macro, prossime date). Per un giro passato: `market-watch/INDICE.md` → poi il singolo `market-watch/storico/AAAA-MM-GG.md`.
   Non caricare mai tutto lo storico insieme.
   - Se **dopo il pull** la data del check in `ultimo.md` **non è ancora di oggi** (la routine non risulta girata oggi),
     segnalalo subito e chiedi se vuoi che lanci `market-watch` adesso prima di proseguire.

3. **Aggancia il contesto locale** leggendo: `data/portfolio.json`, `data/watchlist.json`,
   `data/calendar.json`, `data/rules.json`. Servono per fotografare la riserva disponibile e rispettare i vincoli.

4. **Restituisci il mio pensiero** in italiano, conciso ma motivato, con questa struttura:
   - **Sintesi del giorno** (3–5 punti): cosa ha trovato la routine, trigger scattati e cosa significano.
   - **Bucket lungo (🟢) — per ogni tesi con `bucket: "lungo"`**, una riga di giudizio: `TENERE D'OCCHIO` / `PUNTARE` / `RIDURRE PRIORITÀ` / `INVALIDATA`,
     con il razionale in 1–3 righe e le fonti citate (dallo storico datato del giro).
   - **Bucket breve (🟡) — per ogni tesi con `bucket: "breve"`** (trovate dal catalyst-scanner), mostra:
     - Giudizio su una riga: `ENTRARE` / `TENERE` / `USCIRE` / `SCADUTA`
     - Countdown alla `exit_hard_date` (es. "6 giorni alla scadenza massima")
     - Se `exit_hard_date` è entro ≤14 giorni e la tesi non ha ancora un trigger di ingresso/uscita chiaro → **flag esplicito** (urgency alert)
     - Se nessuna tesi breve è attiva: scrivere "Nessuna tesi breve attiva — il catalyst-scanner cercherà nuove opportunità al prossimo giro."
   - **Dove punterei oggi**: per ogni proposta (sia lungo che breve) indica sempre — **momento di ingresso**; **range di uscita** come scenario condizionale; **orizzonte** (specificando se lungo 12-24 mesi o breve max 8 sett.); **importo**. Numeri **netti post-tasse**.
   - **Drawdown / invalidazioni** da segnalare (senza stop loss automatico).
   - **Riserva disponibile**: mostra la liquidità totale su TR, poi i due bucket virtuali calcolati da `rules.json → budget_speculativo`:
     - 🟢 Lungo ~€X (40%)
     - 🟡 Breve ~€Y (60%)
     - Riporta anche il peso % del comparto speculativo sul patrimonio totale (consapevolezza, non limite).
     - Se la riserva è ferma da ~3 mesi o supera ~700€ senza trigger, segnala lo **sweep** (decisione di Luca, mai automatico).

## Vincoli da rispettare sempre (vedi `CLAUDE.md`)

- È un **parere/digest**, non un'esecuzione: ogni decisione operativa spetta a Luca.
- I range di uscita sono **scenari condizionali**, mai certezze; distingui i fatti (con fonte) dalle interpretazioni.
- **Nessuno stop loss automatico**: segnala drawdown e invalidazione, la decisione di uscita è di Luca.
- **Ogni ipotesi di vendita** va prima dal subagent `tax-agent` (plus lorda, imposta 26%, netto).
- Il **PAC ETF da 400€ non si tocca**: gli acquisti speculativi attingono solo alla riserva, entro il cap.
- Importi in EUR, date ISO (YYYY-MM-DD).
