#!/usr/bin/env python3
"""Ordini — registro degli ordini limite "parcheggiati" su Trade Republic.

Su TR un ordine limite può restare valido fino a 360 giorni: così l'ingresso o la
vendita al prezzo deciso scatta da solo, anche di notte o in ferie, senza aspettare
un nostro avviso. Questo script tiene il registro (data/ordini-aperti.json) e dice,
per ogni ordine, quanto è lontano il prezzo, se probabilmente è già stato eseguito,
se sta per scadere o se è ancora da piazzare in app.

Vincoli TR (supporto ufficiale): gli ordini limite accettano SOLO azioni intere
(niente frazioni) e un ordine d'acquisto BLOCCA la liquidità corrispondente finché
non viene eseguito o annullato. Nessuna esecuzione da qui: gli ordini li piazza e
li annulla l'utente in app; lo script li sorveglia soltanto.

Uso:
  python3 ordini.py                          # report
  python3 ordini.py set <id> <stato> [AAAA-MM-GG]
        stati: da_piazzare | piazzato | eseguito | annullato | scaduto
        (piazzato calcola la scadenza a +360 giorni dalla data)
  python3 ordini.py selftest                 # test offline
"""
import json
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from quotes import load_quotes  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "data")
REGISTRO = os.path.join(DATA, "ordini-aperti.json")

VICINO_PCT = 3.0         # entro questa distanza dal limite = "vicino"
SCADENZA_GG = 30         # avviso quando mancano meno giorni alla scadenza
VALIDITA_GG = 360        # validità massima di un ordine limite su TR
COMMISSIONE_EUR = 1.0
STATI = ("da_piazzare", "piazzato", "eseguito", "annullato", "scaduto")
APERTI = ("da_piazzare", "piazzato")


def eur(x):
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def prezzo_attuale(o, quotes, portfolio):
    """Prima i prezzi di data/quotes.json (freschi), poi il valore/quantità in portfolio."""
    q = quotes.get(o.get("ticker") or "")
    if q and q.get("prezzo_eur"):
        return q["prezzo_eur"], "quotes"
    for p in portfolio.get("posizioni", []):
        if o.get("isin") and o["isin"] in p.get("nome", "") and p.get("quantita"):
            return p["valore_eur"] / p["quantita"], "portfolio"
    return None, None


def valuta(o, prezzo, oggi):
    """Stato operativo di un ordine aperto rispetto al prezzo attuale."""
    flags = []
    lim = o["prezzo_limite_eur"]
    dist = None
    if o["stato"] == "da_piazzare":
        flags.append("DA PIAZZARE in app")
    if prezzo is not None:
        if o["lato"] == "buy":
            dist = (prezzo - lim) / lim * 100          # quanto deve ancora scendere
            if prezzo <= lim:
                flags.append("PREZZO RAGGIUNTO: probabilmente eseguito, controlla l'app / tr-sync"
                             if o["stato"] == "piazzato" else "PREZZO GIÀ IN ZONA: piazzarlo ora")
            elif dist <= VICINO_PCT:
                flags.append("VICINO")
        else:
            dist = (lim - prezzo) / prezzo * 100       # quanto deve ancora salire
            if prezzo >= lim:
                flags.append("PREZZO RAGGIUNTO: probabilmente eseguito, controlla l'app / tr-sync"
                             if o["stato"] == "piazzato" else "PREZZO GIÀ IN ZONA: valutare subito")
            elif dist <= VICINO_PCT:
                flags.append("VICINO")
    if o.get("scade_il"):
        gg = (date.fromisoformat(o["scade_il"]) - oggi).days
        if gg < 0:
            flags.append("SCADUTO su TR: rinnovare o chiudere")
        elif gg <= SCADENZA_GG:
            flags.append(f"SCADE tra {gg} giorni: rinnovare se la tesi regge")
    return dist, flags


def impegno_acquisti(ordini):
    """Liquidità bloccata da TR per gli ordini d'acquisto piazzati."""
    return sum(o["quantita"] * o["prezzo_limite_eur"] + COMMISSIONE_EUR
               for o in ordini if o["lato"] == "buy" and o["stato"] == "piazzato")


def report(reg, quotes, portfolio, oggi):
    ordini = reg.get("ordini", [])
    aperti = [o for o in ordini if o["stato"] in APERTI]
    out = ["📌 Ordini limite parcheggiati su Trade Republic", ""]
    if not aperti:
        out.append("Nessun ordine aperto o da piazzare.")
    for o in aperti:
        prezzo, fonte = prezzo_attuale(o, quotes, portfolio)
        dist, flags = valuta(o, prezzo, oggi)
        lato = "COMPRA" if o["lato"] == "buy" else "VENDI"
        pr = f"oggi €{eur(prezzo)} ({fonte})" if prezzo is not None else "prezzo n/d"
        d = ""
        if dist is not None and dist > 0:
            d = f", mancano {dist:.1f}% ({'deve scendere' if o['lato'] == 'buy' else 'deve salire'})".replace(".", ",", 1)
        out.append(f"• [{o['stato']}] {lato} {o['quantita']} az {o['titolo']} ({o['isin']}) "
                   f"a €{eur(o['prezzo_limite_eur'])} — {pr}{d}")
        for f in flags:
            out.append(f"    ⚠️ {f}")
        if o.get("scade_il"):
            out.append(f"    valido fino al {o['scade_il']}")
    imp = impegno_acquisti(ordini)
    cash = next((p["valore_eur"] for p in portfolio.get("posizioni", [])
                 if p.get("id") == "tr_riserva_speculativa"), None)
    out.append("")
    out.append(f"Liquidità impegnata dagli acquisti piazzati: €{eur(imp)}"
               + (f" (riserva €{eur(cash)} → libera ~€{eur(cash - imp)}; se il sync TR riporta già "
                  f"il saldo disponibile, l'impegno è già tolto)" if cash is not None else ""))
    return "\n".join(out)


def set_stato(reg, oid, stato, giorno):
    if stato not in STATI:
        raise ValueError(f"stato non valido: {stato} (usa {', '.join(STATI)})")
    for o in reg["ordini"]:
        if o["id"] == oid:
            o["stato"] = stato
            o.setdefault("storico", []).append({"data": giorno.isoformat(), "stato": stato})
            if stato == "piazzato":
                o["piazzato_il"] = giorno.isoformat()
                o["scade_il"] = (giorno + timedelta(days=VALIDITA_GG)).isoformat()
            return o
    raise KeyError(f"ordine non trovato: {oid}")


def _load():
    return json.load(open(REGISTRO))


def _save(reg):
    with open(REGISTRO, "w") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def cmd_report():
    pf = json.load(open(os.path.join(DATA, "portfolio.json")))
    print(report(_load(), load_quotes(max_age_hours=72), pf, date.today()))
    return 0


def cmd_set(args):
    if len(args) < 2:
        print("uso: ordini.py set <id> <stato> [AAAA-MM-GG]")
        return 2
    giorno = date.fromisoformat(args[2]) if len(args) > 2 else date.today()
    reg = _load()
    o = set_stato(reg, args[0], args[1], giorno)
    _save(reg)
    print(f"{o['id']} → {o['stato']}" + (f" (scade il {o['scade_il']})" if o.get("scade_il") else ""))
    return 0


def cmd_selftest():
    oggi = date(2026, 9, 29)
    reg = {"ordini": [
        {"id": "s", "titolo": "Alpha", "isin": "US0000000001", "ticker": "ALPH", "lato": "sell",
         "quantita": 10, "prezzo_limite_eur": 60, "stato": "da_piazzare"},
        {"id": "b", "titolo": "Beta", "isin": "US0000000019", "ticker": "BETA", "lato": "buy",
         "quantita": 3, "prezzo_limite_eur": 50, "stato": "da_piazzare"},
    ]}
    pf = {"posizioni": [{"id": "tr_riserva_speculativa", "valore_eur": 500.00}]}
    # lontani
    q = {"ALPH": {"prezzo_eur": 52.0}, "BETA": {"prezzo_eur": 52.4}}
    d, f = valuta(reg["ordini"][0], 52.0, oggi)
    assert round(d, 1) == 15.4 and f == ["DA PIAZZARE in app"], (d, f)
    d, f = valuta(reg["ordini"][1], 52.4, oggi)
    assert round(d, 1) == 4.8 and f == ["DA PIAZZARE in app"], (d, f)
    # piazzato + vicino + scadenza
    set_stato(reg, "b", "piazzato", date(2025, 10, 20))
    assert reg["ordini"][1]["scade_il"] == "2026-10-15"
    d, f = valuta(reg["ordini"][1], 51.2, oggi)
    assert "VICINO" in f and any("SCADE tra 16" in x for x in f), f
    # prezzo raggiunto su ordine piazzato → probabilmente eseguito
    d, f = valuta(reg["ordini"][1], 49.9, oggi)
    assert any("probabilmente eseguito" in x for x in f), f
    # impegno di liquidità solo sugli acquisti piazzati
    assert impegno_acquisti(reg["ordini"]) == 3 * 50 + 1
    txt = report(reg, q, pf, oggi)
    assert "libera ~€349,00" in txt, txt
    try:
        set_stato(reg, "b", "boh", oggi)
        raise AssertionError("stato non valido accettato")
    except ValueError:
        pass
    print("selftest ordini OK: distanze, da piazzare, vicino, scadenza, eseguito, liquidità impegnata.")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "report":
        sys.exit(cmd_report())
    if a[0] == "set":
        sys.exit(cmd_set(a[1:]))
    if a[0] == "selftest":
        sys.exit(cmd_selftest())
    print("comandi: report | set <id> <stato> [data] | selftest")
    sys.exit(2)
