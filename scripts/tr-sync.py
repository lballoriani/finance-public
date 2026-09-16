#!/usr/bin/env python3
"""Sync Trade Republic — lettura del conto in SOLA LETTURA (mai ordini).

Perché: Trade Republic non ha un'API ufficiale, e piazzare ordini via codice è
impossibile/contro le condizioni — oltre che contro la regola d'oro del progetto
("ogni decisione operativa è di Luca"). Ma la LETTURA del conto sì: la libreria
non ufficiale `pytr` legge posizioni e liquidità. Questo elimina l'aggiornamento
manuale di portfolio.json e i falsi trigger da prezzi web sbagliati (lezione
Cheniere 03/08): i numeri arrivano dal conto vero.

FILOSOFIA DI SICUREZZA: questo strumento NON tocca mai portfolio.json da solo.
Gira in dry-run, scrive uno snapshot di staging (data/portfolio-tr-snapshot.json)
e stampa il CONFRONTO con le posizioni attuali. Il merge vero in portfolio.json
è un passo separato e rivisto (con Luca / in sessione), con report in reports/.
Si sposa col flusso "ordine a un tap": una volta confermato un trade su Telegram
(market-watch/trades.md), il prossimo sync riempie i numeri esatti (quantità,
prezzo, fee) leggendoli dal conto.

SETUP (una volta sola, in locale — vedi scripts/README-tr-sync.md):
  python3 -m venv ~/.venvs/tr            # Python di sistema protetto (PEP 668): serve un venv
  ~/.venvs/tr/bin/pip install pytr
  ~/.venvs/tr/bin/python scripts/tr-sync.py sync   # primo login: 2FA interattiva (SMS/app)
La sessione viene messa in cache da pytr (~/.pytr): i sync successivi non chiedono
il 2FA finché il cookie è valido. Le credenziali NON stanno nel repo.

Uso:
  python3 scripts/tr-sync.py sync         # legge TR, scrive snapshot, stampa il diff
  python3 scripts/tr-sync.py selftest      # test offline di parsing/diff (no rete, no pytr)
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PORTFOLIO = os.path.join(REPO, "data", "portfolio.json")
SNAPSHOT = os.path.join(REPO, "data", "portfolio-tr-snapshot.json")

# ISIN valido: 2 lettere paese + 9 alfanumerici + 1 cifra di controllo.
ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")
# Soglia oltre cui segnalare uno scostamento nel diff (aggiornamento significativo).
DELTA_ALERT_EUR = 5.0


def log(msg):
    print(f"[tr-sync] {msg}")


def load_portfolio():
    with open(PORTFOLIO) as f:
        return json.load(f)


def isin_to_posid(portfolio):
    """Mappa ISIN -> id posizione, leggendo l'ISIN dal nome di ogni posizione."""
    out = {}
    for p in portfolio.get("posizioni", []):
        m = ISIN_RE.search(p.get("nome", ""))
        if m:
            out[m.group(0)] = p["id"]
    return out


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize_tr(positions_list, cash):
    """Riduce posizioni/liquidità di pytr (classe Portfolio) a una forma stabile.

    positions_list: pf.positions — ogni voce ha instrumentId (o isin), netSize,
    netValue (Decimal, calcolato da pytr come prezzo×quantità), averageBuyIn, name.
    cash: pf.cash — lista, cash[0] = {amount, currencyId}.
    Tollerante sui nomi dei campi tra versioni.
    """
    positions = {}
    for p in (positions_list or []):
        isin = p.get("instrumentId") or p.get("isin")
        if not isin:
            continue
        positions[isin] = {
            "qty": _num(p.get("netSize") or p.get("size") or p.get("quantity")),
            "value_eur": _num(p.get("netValue") or p.get("value")),
            "avg_eur": _num(p.get("averageBuyIn") or p.get("avgBuyIn")),
            "name": p.get("name"),
        }
    cash_eur, cash_ccy = None, "EUR"
    if isinstance(cash, list) and cash:
        c0 = cash[0]
        cash_eur = _num(c0.get("amount") or c0.get("cashAmount"))
        cash_ccy = c0.get("currencyId") or c0.get("currency") or "EUR"
    elif isinstance(cash, dict):
        cash_eur = _num(cash.get("amount") or cash.get("cashAmount"))
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cash_eur": cash_eur,
        "cash_ccy": cash_ccy,
        "positions": positions,
    }


def build_report(portfolio, tr_state):
    """Confronta lo snapshot TR con portfolio.json. Ritorna (righe, isin_sconosciuti)."""
    isin_map = isin_to_posid(portfolio)
    pos_by_id = {p["id"]: p for p in portfolio["posizioni"]}
    rows = []
    for isin, posid in isin_map.items():
        cur = pos_by_id[posid].get("valore_eur")
        trp = tr_state["positions"].get(isin)
        tr_val = trp["value_eur"] if trp else None
        delta = (tr_val - cur) if (tr_val is not None and cur is not None) else None
        rows.append({"id": posid, "isin": isin, "attuale_eur": cur,
                     "tr_eur": tr_val, "delta_eur": delta})
    unknown = sorted(i for i in tr_state["positions"] if i not in isin_map)
    return rows, unknown


def print_report(portfolio, tr_state):
    rows, unknown = build_report(portfolio, tr_state)
    print(f"\n=== Confronto TR vs portfolio.json (as_of attuale {portfolio.get('as_of')}) ===")
    for r in rows:
        cur = "—" if r["attuale_eur"] is None else f"€{r['attuale_eur']:.2f}"
        trv = "assente su TR" if r["tr_eur"] is None else f"€{r['tr_eur']:.2f}"
        if r["delta_eur"] is None:
            flag = ""
        elif abs(r["delta_eur"]) >= DELTA_ALERT_EUR:
            flag = f"  ⚠️ Δ {r['delta_eur']:+.2f}"
        else:
            flag = f"  (Δ {r['delta_eur']:+.2f})"
        print(f"  • {r['id']:24} attuale {cur:>12}   TR {trv:>16}{flag}")
    # Liquidità / riserva
    reserve = next((p for p in portfolio["posizioni"]
                    if p["id"] == "tr_riserva_speculativa"), None)
    if reserve is not None and tr_state.get("cash_eur") is not None:
        cur = reserve.get("valore_eur")
        d = tr_state["cash_eur"] - cur if cur is not None else None
        dtxt = "" if d is None else f"  (Δ {d:+.2f})"
        print(f"  • {'liquidità TR (=riserva)':24} attuale €{cur:>10.2f}   "
              f"TR €{tr_state['cash_eur']:>10.2f}{dtxt}")
        print("    ℹ️  su TR la liquidità è un unico salvadanaio: qui la leggo come "
              "riserva speculativa. Conferma tu la ripartizione se hai versato PAC di recente.")
    if unknown:
        print("\n  ⚠️ Posizioni su TR NON presenti in portfolio.json (nuovo acquisto?):")
        for isin in unknown:
            trp = tr_state["positions"][isin]
            print(f"     - {isin}  ~€{(trp['value_eur'] or 0):.2f}  qty {trp['qty']}")
    print("\nNessun file modificato: questo è un dry-run. Snapshot in "
          f"{os.path.relpath(SNAPSHOT, REPO)}. Il merge in portfolio.json è un passo a parte.")


def write_snapshot(tr_state):
    with open(SNAPSHOT, "w") as f:
        json.dump(tr_state, f, indent=2, ensure_ascii=False)
        f.write("\n")
    log(f"snapshot scritto: {os.path.relpath(SNAPSHOT, REPO)}")


def _resume_only_session(phone, pin, v2):
    """Riprende SOLO una sessione pytr già valida (niente prompt 2FA).

    Usato in modalità non interattiva (es. dentro /aggiornami): se non c'è una
    sessione valida solleva un errore chiaro invece di restare appeso su una
    richiesta di login interattiva.
    """
    from pytr.api import TradeRepublicApi
    try:
        from pytr.account import CREDENTIALS_FILE
    except Exception:
        CREDENTIALS_FILE = None
    if (not phone or not pin) and CREDENTIALS_FILE and os.path.exists(CREDENTIALS_FILE):
        try:
            lines = open(CREDENTIALS_FILE).read().splitlines()
            phone = phone or (lines[0].strip() if len(lines) > 0 else None)
            pin = pin or (lines[1].strip() if len(lines) > 1 else None)
        except Exception:
            pass
    tr = TradeRepublicApi(phone_no=phone, pin=pin, save_cookies=True,
                          waf_token="default", use_v2_login=v2)
    if not tr.resume_websession():
        raise RuntimeError("sessione TR scaduta o assente: serve un nuovo login "
                           "(rilancia 'scripts/tr-sync.py sync' e approva sull'app TR).")
    return tr


def fetch_tr_state():
    """Login pytr + lettura posizioni/liquidità con la classe Portfolio di pytr.

    Portfolio.portfolio_loop() fa da solo: posizioni (compactPortfolioByType) +
    dettagli strumenti + prezzi (ticker) -> netValue calcolato -> liquidità (cash).
    Con TR_NONINTERACTIVE=1 NON apre mai un login interattivo: se la sessione è
    scaduta fallisce subito con un errore chiaro (così l'aggiornami può avvisare).
    """
    try:
        from pytr.account import login
        from pytr.portfolio import Portfolio
    except ImportError:
        raise RuntimeError("pytr non installato. In un venv dedicato: "
                           "~/.venvs/tr/bin/pip install pytr (vedi scripts/README-tr-sync.md).")
    import asyncio

    phone = os.environ.get("TR_PHONE")   # se assenti, pytr li chiede a schermo + 2FA
    pin = os.environ.get("TR_PIN")
    # store_credentials=True mette in cache sessione+cookie in ~/.pytr: i sync
    # successivi non richiedono più il 2FA. Disattivabile con TR_STORE=0.
    store = os.environ.get("TR_STORE", "1").lower() not in ("0", "false", "no", "")
    # v2=True: login con APPROVAZIONE dall'app TR (o codice authenticator). EVITA
    # la dipendenza da playwright/WAF del login web classico. Disattivabile: TR_V2=0.
    v2 = os.environ.get("TR_V2", "1").lower() not in ("0", "false", "no", "")
    noninteractive = os.environ.get("TR_NONINTERACTIVE", "").lower() in ("1", "true", "yes")
    if noninteractive:
        # resume-only: mai un prompt di login; se scaduta -> errore chiaro
        tr = _resume_only_session(phone, pin, v2)
    else:
        # waf_token="default" + v2 -> WAF saltato (niente playwright); 2FA la 1a volta
        tr = login(phone_no=phone, pin=pin, store_credentials=store,
                   waf_token="default", v2=v2)

    pf = Portfolio(tr, instruments_to_ignore=[])
    asyncio.run(pf.portfolio_loop())
    return normalize_tr(pf.positions, getattr(pf, "cash", None))


def cmd_sync():
    portfolio = load_portfolio()
    log("leggo il conto Trade Republic (sola lettura)…")
    try:
        tr_state = fetch_tr_state()
    except Exception as e:
        print(f"[tr-sync] ERRORE nel sync: {e}")
        return 3   # exit ≠ 0: l'aggiornami lo intercetta e avvisa Luca esplicitamente
    write_snapshot(tr_state)
    print_report(portfolio, tr_state)
    return 0


def cmd_selftest():
    """Verifica offline: parsing ISIN, normalizzazione TR, diff e snapshot."""
    portfolio = load_portfolio()
    isin_map = isin_to_posid(portfolio)
    # gli ISIN chiave devono essere agganciati alle posizioni giuste
    assert isin_map.get("CA13321L1085") == "cameco", isin_map
    assert isin_map.get("IT0003856405") == "leonardo", isin_map
    assert isin_map.get("US10806X1028") == "bbio", isin_map
    assert "IE000716YHJ7" in isin_map and "IE00B3YCGJ38" in isin_map, isin_map

    # forma pytr reale: netValue è un Decimal (calcolato da Portfolio) + un acquisto NUOVO
    from decimal import Decimal
    positions = [
        {"instrumentId": "CA13321L1085", "netSize": "3.13755",
         "netValue": Decimal("271.40"), "averageBuyIn": "79.68", "name": "Cameco"},
        {"instrumentId": "IT0003856405", "netSize": "24.13",
         "netValue": Decimal("1201.00"), "averageBuyIn": "49.74", "name": "Leonardo"},
        {"isin": "US00000NEW01", "netSize": "1",
         "netValue": Decimal("150.00"), "averageBuyIn": "150.00"},   # nuovo, sconosciuto
    ]
    cash = [{"currencyId": "EUR", "amount": "803.79"}]
    tr_state = normalize_tr(positions, cash)
    assert tr_state["cash_eur"] == 803.79, tr_state
    assert abs(tr_state["positions"]["CA13321L1085"]["value_eur"] - 271.40) < 1e-6

    # diff su un portafoglio SINTETICO (deterministico, non dipende da portfolio.json):
    # Cameco 250 vs TR 271,40 -> Δ +21,40 (> soglia); l'ISIN nuovo risulta sconosciuto.
    synth = {"posizioni": [
        {"id": "cameco", "nome": "Cameco (CCJ, CA13321L1085)", "valore_eur": 250, "quantita": 3.13755},
        {"id": "leonardo", "nome": "Leonardo (LDO, IT0003856405)", "valore_eur": 1200, "quantita": 24.13},
        {"id": "tr_riserva_speculativa", "nome": "Riserva", "valore_eur": 803.79},
    ]}
    rows, unknown = build_report(synth, tr_state)
    cam = next(r for r in rows if r["id"] == "cameco")
    assert cam["tr_eur"] == 271.40 and cam["delta_eur"] is not None, cam
    assert abs(cam["delta_eur"]) >= DELTA_ALERT_EUR, "scostamento significativo non rilevato"
    assert "US00000NEW01" in unknown, unknown

    # snapshot su file temporaneo
    import tempfile
    global SNAPSHOT
    SNAPSHOT = os.path.join(tempfile.mkdtemp(), "snap.json")
    write_snapshot(tr_state)
    reread = json.load(open(SNAPSHOT))
    assert reread["cash_eur"] == 803.79
    print("selftest tr-sync OK: ISIN->posizione, parsing pytr, diff con Δ, nuovi acquisti, snapshot.")
    return 0


COMMANDS = {"sync": cmd_sync, "selftest": cmd_selftest}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "sync"
    fn = COMMANDS.get(action)
    if not fn:
        print(f"comando sconosciuto: {action}. Usa: {', '.join(COMMANDS)}")
        sys.exit(2)
    sys.exit(fn())
