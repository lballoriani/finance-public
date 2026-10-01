#!/usr/bin/env python3
"""Quotes — prezzi affidabili da una fonte dati, non dalla ricerca web.

Perché: le ricerche web del cloud danno a volte prezzi sbagliati o vecchi (falsi
trigger, feed errati, variazioni col segno sbagliato rispetto al broker). Qui i prezzi arrivano da Yahoo Finance (chart API, gratuita, senza
chiave) e i cambi dalla BCE (tassi ufficiali giornalieri). Nessun costo AI.

Due prodotti:
  data/quotes.json  → prezzi di posizioni aperte, ETF del PAC e tesi in watchlist,
                      già convertiti in EUR (è il prezzo da usare nelle decisioni)
  data/movers.json  → filtro automatico sull'universo (data/universe.json): i titoli
                      con movimenti forti su volumi alti, da far verificare al
                      catalyst-scanner (calo brusco = possibile "post-event bottom",
                      balzo su volumi = possibile "post-earnings drift")

I prezzi in EUR dei titoli esteri sono convertiti al cambio BCE: su Trade Republic
il prezzo in euro può differire di qualche decimo di punto (spread, orario). TR resta
la fonte di verità per i valori del conto (scripts/tr-sync.py).

Uso:
  python3 quotes.py fetch      # aggiorna data/quotes.json
  python3 quotes.py movers     # aggiorna data/movers.json
  python3 quotes.py all        # entrambi (usato dalla GitHub Action)
  python3 quotes.py show       # stampa i prezzi salvati e la loro età
  python3 quotes.py selftest   # test offline
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
QUOTES = os.path.join(DATA, "quotes.json")
MOVERS = os.path.join(DATA, "movers.json")

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
ECB = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
UA = {"User-Agent": "Mozilla/5.0 (finance-quotes; personal use)"}

# ETF del PAC: il simbolo Yahoo sta in data/target.json (campo "yahoo" di ogni voce
# di "allocazione", es. quotazione Xetra in EUR). Verificarlo una volta contro il broker.

STALE_DAYS = 4          # quotazione più vecchia di così = avviso (feed fermo?)
JUMP_PCT = 15.0         # variazione in un giorno oltre cui chiedere una verifica
MOVER_5D_PCT = -10.0    # calo in 5 sedute → candidato post-event bottom
MOVER_1D_PCT = 7.0      # balzo/crollo in 1 seduta...
MOVER_VOL_X = 2.0       # ...con volumi almeno doppi della media 20 sedute


# ---------------------------------------------------------------- rete

def _get(url, timeout=15, tries=2):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # rete, 404, timeout
            last = e
            time.sleep(1 + i)
    raise last


def ecb_rates(xml=None):
    """Cambi BCE: quante unità di valuta per 1 EUR. Ritorna (rates, data)."""
    xml = xml if xml is not None else _get(ECB)
    rates = {c: float(r) for c, r in re.findall(r"currency='([A-Z]{3})' rate='([0-9.]+)'", xml)}
    m = re.search(r"time='(\d{4}-\d{2}-\d{2})'", xml)
    if "USD" not in rates:
        raise ValueError("BCE: cambio USD assente")
    rates["EUR"] = 1.0
    return rates, (m.group(1) if m else None)


def parse_chart(raw):
    """Estrae dal JSON di Yahoo i campi che servono. Funzione pura (testabile)."""
    res = json.loads(raw)["chart"]["result"][0]
    meta = res["meta"]
    q = (res.get("indicators") or {}).get("quote") or [{}]
    closes = [c for c in (q[0].get("close") or []) if c is not None]
    vols = [v for v in (q[0].get("volume") or []) if v is not None]
    price = meta.get("regularMarketPrice")
    cur = meta.get("currency")
    if cur == "GBp":  # Londra quota in pence
        price, cur = (price / 100 if price is not None else None), "GBP"
        closes = [c / 100 for c in closes]
    return {
        "prezzo": price, "valuta": cur,
        "nome": meta.get("longName") or meta.get("shortName"),
        "ts": meta.get("regularMarketTime"),
        "max_52s": meta.get("fiftyTwoWeekHigh") if meta.get("currency") != "GBp" else None,
        "min_52s": meta.get("fiftyTwoWeekLow") if meta.get("currency") != "GBp" else None,
        "closes": closes, "vols": vols,
    }


def fetch_chart(sym, rng="1mo"):
    return parse_chart(_get(YAHOO.format(sym=sym, rng=rng)))


def pct(a, b):
    return (a / b - 1) * 100 if (a is not None and b) else None


def day_change(c):
    """Variazione vs chiusura precedente (l'ultima candela è la seduta in corso/ultima)."""
    cl = c["closes"]
    return pct(c["prezzo"], cl[-2]) if len(cl) >= 2 else None


# ---------------------------------------------------------------- simboli

def etf_symbols(target):
    """ISIN → simbolo Yahoo degli ETF del PAC, da data/target.json."""
    return {a["isin"]: a["yahoo"] for a in target.get("allocazione", [])
            if a.get("isin") and a.get("yahoo")}


def symbols_for_quotes(watchlist, portfolio, target):
    """Simboli da quotare: tesi attive/in posizione/radar con ticker + ETF del PAC."""
    out = {}
    for t in watchlist.get("tesi", []):
        sym = t.get("yahoo") or t.get("ticker")
        if not sym or " " in sym or "/" in sym:
            continue
        out[sym] = {"tesi_id": t.get("id"), "status": t.get("status"), "tier": t.get("tier")}
    for p in portfolio.get("posizioni", []):
        for isin, sym in etf_symbols(target).items():
            if isin in p.get("nome", ""):
                out[sym] = {"portfolio_id": p.get("id"), "isin": isin, "tipo": "ETF"}
    return out


def universe_symbols(universe):
    seen = {}
    for settore, v in universe.get("settori", {}).items():
        for s in v.get("tickers", []):
            seen.setdefault(s, settore)
    return seen


# ---------------------------------------------------------------- comandi

def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def build_quotes(symbols, fetch=fetch_chart, rates=None, now=None):
    now = now or _now()
    rates, fx_date = rates or ecb_rates()
    quotes, avvisi, errori = {}, [], []

    def one(sym):
        try:
            return sym, fetch(sym), None
        except Exception as e:
            return sym, None, str(e)[:120]

    with cf.ThreadPoolExecutor(6) as ex:
        results = list(ex.map(one, symbols))
    for sym, c, err in results:
        if err or not c or c["prezzo"] is None:
            errori.append(f"{sym}: {err or 'prezzo assente'}")
            continue
        rate = rates.get(c["valuta"])
        eur = round(c["prezzo"] / rate, 4) if rate else None
        ts = datetime.fromtimestamp(c["ts"], timezone.utc) if c.get("ts") else None
        chg = day_change(c)
        q = {"prezzo": c["prezzo"], "valuta": c["valuta"], "prezzo_eur": eur,
             "variazione_1g_pct": round(chg, 2) if chg is not None else None,
             "quotazione_utc": ts.isoformat() if ts else None,
             "max_52s": c.get("max_52s"), "min_52s": c.get("min_52s"),
             "nome": c.get("nome")}
        q.update(symbols[sym])
        quotes[sym] = q
        if rate is None:
            avvisi.append(f"{sym}: valuta {c['valuta']} senza cambio BCE, prezzo in EUR non calcolato")
        if ts and (now - ts).days >= STALE_DAYS:
            avvisi.append(f"{sym}: quotazione vecchia ({ts.date()}), feed fermo o titolo sospeso?")
        if chg is not None and abs(chg) >= JUMP_PCT:
            avvisi.append(f"{sym}: {chg:+.1f}% in una seduta, verificare la notizia prima di agire")
    return {
        "aggiornato": now.isoformat(),
        "fonte": "Yahoo Finance chart API (prezzi) + BCE eurofxref (cambi)",
        "nota": "Prezzi in EUR = prezzo / cambio BCE del giorno. Su TR può differire di qualche decimo di punto. TR resta la verità per i valori del conto.",
        "cambi_bce": {"data": fx_date, **{k: v for k, v in rates.items() if k in ("USD", "GBP", "SEK", "DKK", "CHF")}},
        "quotes": dict(sorted(quotes.items())),
        "avvisi": avvisi, "errori": errori,
    }


def build_movers(universe_map, fetch=fetch_chart, now=None):
    now = now or _now()
    flagged, errori, n_ok = [], [], 0

    def one(sym):
        try:
            return sym, fetch(sym), None
        except Exception as e:
            return sym, None, str(e)[:120]

    with cf.ThreadPoolExecutor(6) as ex:
        results = list(ex.map(one, universe_map))
    for sym, c, err in results:
        if err or not c or c["prezzo"] is None or len(c["closes"]) < 7:
            errori.append(f"{sym}: {err or 'dati insufficienti'}")
            continue
        n_ok += 1
        cl, vols = c["closes"], c["vols"]
        r1 = day_change(c)
        r5 = pct(c["prezzo"], cl[-6])
        base = vols[-21:-1] if len(vols) >= 6 else []
        avg = sum(base) / len(base) if base else 0
        vx = (vols[-1] / avg) if (vols and avg) else None
        tipo = None
        if r1 is not None and vx is not None and vx >= MOVER_VOL_X and r1 >= MOVER_1D_PCT:
            tipo = "balzo_su_volumi"      # possibile post-earnings drift
        elif r1 is not None and vx is not None and vx >= MOVER_VOL_X and r1 <= -MOVER_1D_PCT:
            tipo = "crollo_su_volumi"     # possibile post-event bottom (evento puntuale?)
        elif r5 is not None and r5 <= MOVER_5D_PCT:
            tipo = "calo_5_sedute"        # possibile post-event bottom
        if tipo:
            flagged.append({"ticker": sym, "settore": universe_map[sym], "segnale": tipo,
                            "var_1g_pct": round(r1, 1) if r1 is not None else None,
                            "var_5g_pct": round(r5, 1) if r5 is not None else None,
                            "volume_x_media": round(vx, 1) if vx is not None else None,
                            "prezzo": c["prezzo"], "valuta": c["valuta"]})
    flagged.sort(key=lambda f: abs(f["var_5g_pct"] or f["var_1g_pct"] or 0), reverse=True)
    return {
        "aggiornato": now.isoformat(),
        "descrizione": ("Filtro meccanico sull'universo: NON sono segnali d'acquisto. Ogni nome va verificato dal "
                        "catalyst-scanner (qual è l'evento? la tesi regge? c'è un catalizzatore datato?)."),
        "soglie": {"balzo_o_crollo_1g_pct": MOVER_1D_PCT, "volume_x_media_20g": MOVER_VOL_X,
                   "calo_5_sedute_pct": MOVER_5D_PCT},
        "scansionati": n_ok, "segnalati": flagged, "errori": errori,
    }


def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def cmd_fetch():
    wl = json.load(open(os.path.join(DATA, "watchlist.json")))
    pf = json.load(open(os.path.join(DATA, "portfolio.json")))
    tgt = json.load(open(os.path.join(DATA, "target.json")))
    out = build_quotes(symbols_for_quotes(wl, pf, tgt))
    _write(QUOTES, out)
    print(f"quotes: {len(out['quotes'])} prezzi, {len(out['avvisi'])} avvisi, {len(out['errori'])} errori → {QUOTES}")
    for a in out["avvisi"] + out["errori"]:
        print("  ⚠️", a)
    return 0 if out["quotes"] else 1


def cmd_movers():
    uni = json.load(open(os.path.join(DATA, "universe.json")))
    out = build_movers(universe_symbols(uni))
    _write(MOVERS, out)
    print(f"movers: {out['scansionati']} scansionati, {len(out['segnalati'])} segnalati, {len(out['errori'])} errori → {MOVERS}")
    for f in out["segnalati"]:
        print(f"  • {f['ticker']} [{f['segnale']}] 1g {f['var_1g_pct']}% · 5g {f['var_5g_pct']}% · vol x{f['volume_x_media']}")
    return 0 if out["scansionati"] else 1


def load_quotes(max_age_hours=None):
    """Per gli altri script: {simbolo: quote} oppure {} se il file manca/è troppo vecchio."""
    try:
        q = json.load(open(QUOTES))
    except Exception:
        return {}
    if max_age_hours is not None:
        age = (_now() - datetime.fromisoformat(q["aggiornato"])).total_seconds() / 3600
        if age > max_age_hours:
            return {}
    return q.get("quotes", {})


def cmd_show():
    try:
        q = json.load(open(QUOTES))
    except FileNotFoundError:
        print("data/quotes.json assente: lancia 'quotes.py fetch'.")
        return 1
    age = (_now() - datetime.fromisoformat(q["aggiornato"])).total_seconds() / 3600
    print(f"Prezzi aggiornati {q['aggiornato']} (~{age:.0f} ore fa) · cambio BCE {q['cambi_bce']}")
    for sym, v in q["quotes"].items():
        chg = f"{v['variazione_1g_pct']:+.1f}%" if v.get("variazione_1g_pct") is not None else "n/d"
        eur = f"€{v['prezzo_eur']:.2f}" if v.get("prezzo_eur") is not None else "€n/d"
        print(f"  {sym:10} {v['prezzo']:>10.2f} {v['valuta']}  = {eur:>10}  ({chg})  {v.get('tesi_id') or v.get('portfolio_id') or ''}")
    for a in q.get("avvisi", []) + q.get("errori", []):
        print("  ⚠️", a)
    return 0


def cmd_selftest():
    def chart(price, cur, closes, vols, ts=1790000000):
        return json.dumps({"chart": {"result": [{"meta": {
            "regularMarketPrice": price, "currency": cur, "regularMarketTime": ts, "longName": "X"},
            "indicators": {"quote": [{"close": closes, "volume": vols}]}}]}})
    xml = "<Cube time='2026-09-29'><Cube currency='USD' rate='1.1378'/><Cube currency='GBP' rate='0.84'/></Cube>"
    rates = ecb_rates(xml)
    assert rates[0]["USD"] == 1.1378 and rates[1] == "2026-09-29"
    # conversione USD→EUR e pence→GBP
    c = parse_chart(chart(87.04, "USD", [88.0, 87.5, 88.07, None, 87.04], [1, 1, 1, 1, 1]))
    assert c["closes"] == [88.0, 87.5, 88.07, 87.04]
    assert abs(day_change(c) - (87.04 / 88.07 - 1) * 100) < 1e-9
    p = parse_chart(chart(1967.0, "GBp", [1900.0, 1967.0], [1, 1]))
    assert p["prezzo"] == 19.67 and p["valuta"] == "GBP"
    now = datetime.fromtimestamp(1790000000, timezone.utc)
    fake = {"EXPL": chart(87.04, "USD", [88.07, 87.04], [1, 1]),
            "OLD": chart(10.0, "USD", [10.0, 10.0], [1, 1], ts=1790000000 - 6 * 86400),
            "JMP": chart(12.0, "USD", [10.0, 12.0], [1, 1])}
    out = build_quotes({s: {} for s in fake}, fetch=lambda s: parse_chart(fake[s]), rates=rates, now=now)
    assert abs(out["quotes"]["EXPL"]["prezzo_eur"] - 76.4985) < 1e-3, out["quotes"]["EXPL"]
    assert any("OLD" in a for a in out["avvisi"]) and any("JMP" in a for a in out["avvisi"])
    # movers: crollo su volumi, balzo su volumi, calo 5 sedute, niente
    base = [100.0] * 20
    vol = [1000] * 20
    m = {"DOWN": chart(90.0, "USD", base + [90.0], vol + [3000]),
         "UP": chart(110.0, "USD", base + [110.0], vol + [2500]),
         "SLIDE": chart(88.0, "USD", base[:15] + [100.0, 96, 94, 92, 90, 88.0], vol + [1000]),
         "FLAT": chart(100.5, "USD", base + [100.5], vol + [1100])}
    mv = build_movers({s: "test" for s in m}, fetch=lambda s: parse_chart(m[s]), now=now)
    seg = {f["ticker"]: f["segnale"] for f in mv["segnalati"]}
    assert seg == {"DOWN": "crollo_su_volumi", "UP": "balzo_su_volumi", "SLIDE": "calo_5_sedute"}, seg
    # simboli: tesi con ticker + ETF del PAC, niente IPO senza ticker
    wl = {"tesi": [{"id": "a", "ticker": "EXPL"}, {"id": "b", "ticker": None},
                   {"id": "c", "ticker": "EXPL.MI"}, {"id": "d", "ticker": "X", "yahoo": "X.DE"}]}
    pf = {"posizioni": [{"id": "etf_world", "nome": "ETF World (WRLD, IE0000000011)"}]}
    tgt = {"allocazione": [{"id": "world", "isin": "IE0000000011", "yahoo": "WRLD.DE"}]}
    syms = symbols_for_quotes(wl, pf, tgt)
    assert set(syms) == {"EXPL", "EXPL.MI", "X.DE", "WRLD.DE"}, syms
    print("selftest quotes OK: cambi BCE, USD→EUR, pence, avvisi feed vecchio/salto, movers, simboli.")
    return 0


COMMANDS = {"fetch": cmd_fetch, "movers": cmd_movers, "show": cmd_show, "selftest": cmd_selftest,
            "all": lambda: max(cmd_fetch(), cmd_movers())}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "show"
    fn = COMMANDS.get(action)
    if not fn:
        print(f"comando sconosciuto: {action}. Usa: {', '.join(COMMANDS)}")
        sys.exit(2)
    sys.exit(fn())
