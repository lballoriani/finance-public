#!/usr/bin/env python3
"""Cruscotto al volo — una foto sola del portafoglio.

Legge data/portfolio.json, data/target.json, data/rules.json e produce un
riepilogo leggibile: patrimonio e P&L, ETF core vs allocazione target (con
deriva), comparto speculativo e peso %, riserva disponibile, fondo emergenza,
posizioni speculative con P&L. Nessun costo AI, solo lettura.

`build_dashboard()` ritorna il testo: lo usa sia la CLI qui sotto sia il comando
Telegram 'cruscotto' del ponte (tg-bridge.py).

Uso:
  python3 cruscotto.py            # stampa il cruscotto
  python3 cruscotto.py selftest   # test offline
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA = os.path.join(REPO, "data")


def _load(name):
    return json.load(open(os.path.join(DATA, name)))


def eur(x):
    """12345.6 -> '12.345,60' (formato italiano)."""
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x):
    return f"{x:+.1f}".replace(".", ",")


ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")


def spec_ids(posizioni):
    """Id delle posizioni del comparto speculativo azionario (tipo 'azione')."""
    return [x["id"] for x in posizioni if x.get("tipo") == "azione"]


def etf_ids(posizioni, target):
    """Posizione ETF -> id in target.json, agganciati per ISIN (letto dal nome)."""
    by_isin = {a.get("isin"): a["id"] for a in target.get("allocazione", [])}
    out = {}
    for x in posizioni:
        m = ISIN_RE.search(x.get("nome", ""))
        if x.get("tipo") == "ETF" and m and m.group(0) in by_isin:
            out[x["id"]] = by_isin[m.group(0)]
    return out


def build_dashboard(portfolio=None, target=None, rules=None):
    p = portfolio or _load("portfolio.json")
    tgt = target or _load("target.json")
    rules = rules or _load("rules.json")
    pos = {x["id"]: x for x in p["posizioni"]}
    SPEC_IDS = spec_ids(p["posizioni"])
    ETF_IDS = etf_ids(p["posizioni"], tgt)

    tot = sum(x.get("valore_eur", 0) for x in p["posizioni"])
    versato = sum(x.get("versato_eur", 0) for x in p["posizioni"])
    pl = tot - versato

    reserve = pos.get("tr_riserva_speculativa", {}).get("valore_eur", 0)
    spec = reserve + sum(pos.get(i, {}).get("valore_eur", 0) for i in SPEC_IDS)

    out = [f"📊 Cruscotto — {p.get('as_of')}", ""]
    out.append(f"Patrimonio: €{eur(tot)}  (versato €{eur(versato)}, P&L {pct(pl)}€)")
    out.append(f"Comparto speculativo: €{eur(spec)}  ({pct(spec / tot * 100).lstrip('+')}% del totale)")
    out.append(f"Riserva spendibile su TR: €{eur(reserve)}")

    # ETF core vs target
    etf_vals = {tid: pos.get(pid, {}).get("valore_eur", 0) for pid, tid in ETF_IDS.items()}
    etf_tot = sum(etf_vals.values()) or 1
    target_pesi = {a["id"]: a["peso"] for a in tgt.get("allocazione", [])}
    soglia = rules.get("rischio", {}).get("soglia_ribilanciamento_pct", 0.05)
    out += ["", "🎯 ETF core (peso attuale vs target):"]
    for pid, tid in ETF_IDS.items():
        val = etf_vals[tid]
        cur = val / etf_tot
        tp = target_pesi.get(tid, 0)
        drift = cur - tp
        flag = "  ⚠️ oltre soglia" if abs(drift) > soglia else ""
        nome = pos.get(pid, {}).get("nome", pid).split(" (")[0]
        out.append(f"• {nome}: {cur * 100:.0f}% vs {tp * 100:.0f}% target ({pct(drift * 100)} pt){flag}")

    # Posizioni speculative con P&L
    out += ["", "🔫 Posizioni speculative:"]
    any_spec = False
    for i in SPEC_IDS:
        x = pos.get(i)
        if not x:
            continue
        any_spec = True
        v, c = x.get("valore_eur", 0), x.get("versato_eur", 0)
        p_pct = (v - c) / c * 100 if c else 0
        nome = x.get("nome", i).split(" (")[0]
        out.append(f"• {nome}: €{eur(v)}  ({pct(p_pct)}% sul versato)")
    if not any_spec:
        out.append("• (nessuna)")

    # Fondo emergenza
    cash = pos.get("cash", {}).get("valore_eur", 0)
    fe = rules.get("rischio", {}).get("fondo_emergenza_eur", {})
    fmin, fmax = fe.get("min"), fe.get("max")
    if fmin is not None:
        stato_fe = "coperto ✓" if cash >= fmin else f"sotto il minimo (mancano €{eur(fmin - cash)})"
        out += ["", "🛟 Fondo emergenza:",
                f"• Liquidità conto: €{eur(cash)} vs fondo emergenza €{eur(fmin)}-{eur(fmax)} → {stato_fe}"]
    return "\n".join(out)


def cmd_selftest():
    # portafoglio SINTETICO: numeri inventati, solo per verificare i calcoli
    portfolio = {"as_of": "2026-01-31", "posizioni": [
        {"id": "cash", "nome": "Liquidità conto corrente", "tipo": "cash", "valore_eur": 8000, "versato_eur": 8000},
        {"id": "etf_world", "nome": "ETF World (WRLD, IE0000000011)", "tipo": "ETF", "valore_eur": 4000, "versato_eur": 3800},
        {"id": "etf_em", "nome": "ETF Emergenti (EMER, IE0000000029)", "tipo": "ETF", "valore_eur": 1000, "versato_eur": 1000},
        {"id": "tr_riserva_speculativa", "nome": "Riserva", "tipo": "cash", "valore_eur": 500, "versato_eur": 500},
        {"id": "alpha", "nome": "Alpha Corp (ALPH, US0000000001)", "tipo": "azione", "valore_eur": 600, "versato_eur": 500},
        {"id": "beta", "nome": "Beta Inc (BETA, US0000000019)", "tipo": "azione", "valore_eur": 400, "versato_eur": 500},
    ]}
    target = {"allocazione": [{"id": "world", "isin": "IE0000000011", "peso": 0.80},
                              {"id": "em", "isin": "IE0000000029", "peso": 0.20}]}
    rules = {"rischio": {"soglia_ribilanciamento_pct": 0.05,
                         "fondo_emergenza_eur": {"min": 6000, "max": 9000}}}
    txt = build_dashboard(portfolio, target, rules)
    assert "Cruscotto" in txt and "Patrimonio" in txt, txt
    assert "Comparto speculativo" in txt and "10,3%" in txt, txt   # 1500/14500
    assert "Alpha Corp" in txt and "Beta Inc" in txt, txt
    assert "80% vs 80% target" in txt, txt                          # 4000/(4000+1000)
    assert "coperto ✓" in txt, txt                                  # cash 8000 >= 6000
    print("selftest cruscotto OK: patrimonio, speculativo %, ETF vs target, fondo emergenza.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(cmd_selftest())
    print(build_dashboard())
