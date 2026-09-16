#!/usr/bin/env python3
"""Exit-check — vicinanza ai gradini del piano di uscita delle posizioni aperte.

Per ogni posizione aperta con un `alert.piano_uscita` (watchlist.json), confronta
il prezzo attuale (da portfolio.json) con i gradini di presa profitto: distanza %,
azioni da vendere, netto stimato. Segnala i gradini VICINI (entro soglia) o già
SUPERATI e, per il più vicino azionabile, produce la SCHEDA DI VENDITA pronta da
mettere in coda (`tg-bridge.py ticket`). Nessun costo AI, sola lettura.

Il netto è quello PRE-CALCOLATO nel piano (`netto_stimato_eur`): alla vendita vera
il netto reale lo ricalcola il tax-agent sui prezzi del giorno. Nessuna vendita
automatica: è solo un avviso, la decisione è di Luca.

Uso:
  python3 exit-check.py            # stampa la vicinanza ai gradini
  python3 exit-check.py ticket     # + JSON della scheda di vendita del gradino più vicino
  python3 exit-check.py selftest   # test offline
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA = os.path.join(REPO, "data")

ALERT_PCT = 5.0        # entro questa % sotto un gradino = "vicino"
ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")


def _load(name):
    return json.load(open(os.path.join(DATA, name)))


def eur(x):
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def price_of(pos):
    v, q = pos.get("valore_eur"), pos.get("quantita")
    return (v / q) if (v and q) else None


def find_position(pos_list, ticker, asset):
    """Aggancia la tesi alla posizione in portfolio via ticker o nome."""
    for p in pos_list:
        nome = p.get("nome", "")
        if (ticker and f"({ticker}," in nome) or (asset and asset.split()[0] in nome):
            return p
    return None


def analyze(watchlist=None, portfolio=None):
    wl = watchlist or _load("watchlist.json")
    pf = portfolio or _load("portfolio.json")
    positions = pf["posizioni"]
    rows = []   # una voce per tesi con piano_uscita
    for t in wl.get("tesi", []):
        piano = (t.get("alert") or {}).get("piano_uscita")
        if not piano:
            continue
        pos = find_position(positions, t.get("ticker"), t.get("asset"))
        price = price_of(pos) if pos else None
        isin_m = ISIN_RE.search(pos.get("nome", "")) if pos else None
        gradini = []
        for g in piano.get("gradini", []):
            tp = g.get("prezzo_eur")
            if tp is None or price is None:
                stato, dist = "?", None
            elif price >= tp:
                stato, dist = "SUPERATO", (price - tp) / tp * 100
            else:
                dist = (tp - price) / price * 100
                stato = "VICINO" if dist <= ALERT_PCT else "lontano"
            gradini.append({"prezzo_eur": tp, "vendi": g.get("vendi"),
                            "netto_stimato_eur": g.get("netto_stimato_eur"),
                            "stato": stato, "dist_pct": dist})
        rows.append({
            "tesi_id": t.get("id"), "asset": t.get("asset"), "ticker": t.get("ticker"),
            "isin": isin_m.group(0) if isin_m else None,
            "price": price, "gradini": gradini,
        })
    return rows


def nearest_actionable(rows):
    """Il gradino più vicino non ancora 'lontano' (VICINO o SUPERATO)."""
    best = None
    for r in rows:
        for g in r["gradini"]:
            if g["stato"] in ("VICINO", "SUPERATO"):
                key = 0 if g["stato"] == "SUPERATO" else (g["dist_pct"] or 0)
                if best is None or key < best[0]:
                    best = (key, r, g)
    return (best[1], best[2]) if best else (None, None)


def sell_ticket(row, g):
    ts_id = row["tesi_id"]
    return {
        "id": f"exit-{(row.get('ticker') or ts_id).lower()}-{int(g['prezzo_eur'])}",
        "lato": "sell", "titolo": row["asset"], "ticker": row.get("ticker"),
        "isin": row.get("isin"), "quantita": g.get("vendi"),
        "prezzo_limite_eur": g["prezzo_eur"], "netto_atteso_eur": g.get("netto_stimato_eur"),
        "tesi_id": ts_id,
        "nota": (f"Gradino di uscita a €{eur(g['prezzo_eur'])} ({g.get('vendi')}). "
                 f"Netto stimato ~€{eur(g['netto_stimato_eur'])} (il tax-agent lo ricalcola alla vendita). Decidi tu."),
    }


def render(rows):
    if not rows:
        return "Nessuna posizione aperta con piano di uscita."
    out = ["🔵 Piano di uscita — vicinanza ai gradini", ""]
    for r in rows:
        pr = f"€{eur(r['price'])}" if r["price"] is not None else "prezzo n/d"
        out.append(f"{r['asset']} ({r.get('ticker') or '?'}) @ {pr}:")
        for g in r["gradini"]:
            if g["dist_pct"] is None:
                d = ""
            elif g["stato"] == "SUPERATO":
                d = f" → SUPERATO (+{g['dist_pct']:.1f}% oltre)"
            elif g["stato"] == "VICINO":
                d = f" → ⚠️ VICINO (+{g['dist_pct']:.1f}% al gradino)"
            else:
                d = f" → +{g['dist_pct']:.1f}% (lontano)"
            netto = f", netto ~€{eur(g['netto_stimato_eur'])}" if g.get("netto_stimato_eur") is not None else ""
            out.append(f"  • €{eur(g['prezzo_eur'])} ({g.get('vendi')}{netto}){d}")
    row, g = nearest_actionable(rows)
    out.append("")
    out.append("👉 Gradino azionabile: nessuno entro soglia." if row is None
               else f"👉 Gradino azionabile: {row['asset']} a €{eur(g['prezzo_eur'])}. "
                    f"Usa 'exit-check.py ticket' per la scheda di vendita.")
    return "\n".join(out)


def cmd_report():
    print(render(analyze()))
    return 0


def cmd_ticket():
    row, g = nearest_actionable(analyze())
    if row is None:
        print("Nessun gradino azionabile: nessuna scheda di vendita.")
        return 0
    print(json.dumps(sell_ticket(row, g), ensure_ascii=False, indent=2))
    return 0


def cmd_selftest():
    wl = {"tesi": [{
        "id": "leonardo-ldo", "asset": "Leonardo", "ticker": "LDO",
        "alert": {"piano_uscita": {"gradini": [
            {"prezzo_eur": 70, "vendi": "1/3", "netto_stimato_eur": 518},
            {"prezzo_eur": 80, "vendi": "1/3", "netto_stimato_eur": 577},
        ]}}}]}
    # prezzo lontano dal 1° gradino
    pf_far = {"posizioni": [{"id": "leonardo", "nome": "Leonardo S.p.A. (LDO, IT0003856405)",
                             "valore_eur": 1220.40, "quantita": 24.128158}]}
    rows = analyze(wl, pf_far)
    assert rows[0]["gradini"][0]["stato"] == "lontano", rows
    assert nearest_actionable(rows) == (None, None)

    # prezzo appena sotto il 1° gradino (€68,6 -> +2% al gradino 70) => VICINO
    pf_near = {"posizioni": [{"id": "leonardo", "nome": "Leonardo S.p.A. (LDO, IT0003856405)",
                              "valore_eur": 68.6 * 24.128158, "quantita": 24.128158}]}
    rows = analyze(wl, pf_near)
    assert rows[0]["gradini"][0]["stato"] == "VICINO", rows[0]["gradini"][0]
    row, g = nearest_actionable(rows)
    assert row is not None and g["prezzo_eur"] == 70
    tk = sell_ticket(row, g)
    assert tk["lato"] == "sell" and tk["isin"] == "IT0003856405" and tk["netto_atteso_eur"] == 518, tk
    assert tk["prezzo_limite_eur"] == 70

    # prezzo sopra un gradino => SUPERATO
    pf_over = {"posizioni": [{"id": "leonardo", "nome": "Leonardo S.p.A. (LDO, IT0003856405)",
                              "valore_eur": 72 * 24.128158, "quantita": 24.128158}]}
    rows = analyze(wl, pf_over)
    assert rows[0]["gradini"][0]["stato"] == "SUPERATO", rows[0]["gradini"][0]
    print("selftest exit-check OK: lontano/vicino/superato, scheda di vendita, ISIN.")
    return 0


COMMANDS = {"report": cmd_report, "ticket": cmd_ticket, "selftest": cmd_selftest}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "report"
    fn = COMMANDS.get(action)
    if not fn:
        print(f"comando sconosciuto: {action}. Usa: {', '.join(COMMANDS)}")
        sys.exit(2)
    sys.exit(fn())
