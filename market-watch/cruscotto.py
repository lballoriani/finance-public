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


# id delle posizioni che compongono il comparto speculativo azionario
SPEC_IDS = {"leonardo", "cameco", "bbio"}
ETF_IDS = {"etf_fwia": "fwra", "etf_sp500": "sp500_synth"}


def build_dashboard(portfolio=None, target=None, rules=None):
    p = portfolio or _load("portfolio.json")
    tgt = target or _load("target.json")
    rules = rules or _load("rules.json")
    pos = {x["id"]: x for x in p["posizioni"]}

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

    # Fondo emergenza (l'obiettivo casa si finanzia con TFR + fondo emergenza)
    cash = pos.get("cash", {}).get("valore_eur", 0)
    fe = rules.get("rischio", {}).get("fondo_emergenza_eur", {})
    fmin, fmax = fe.get("min"), fe.get("max")
    if fmin is not None:
        stato_fe = "coperto ✓" if cash >= fmin else f"sotto il minimo (mancano €{eur(fmin - cash)})"
        out += ["", f"🏠 Obiettivo casa (TFR + fondo emergenza):",
                f"• Liquidità conto: €{eur(cash)} vs fondo emergenza €{eur(fmin)}-{eur(fmax)} → {stato_fe}",
                "• Il PAC NON si de-riska per la casa (si attinge prima a TFR + fondo)."]
    return "\n".join(out)


def cmd_selftest():
    portfolio = {"as_of": "2026-09-04", "posizioni": [
        {"id": "cash", "nome": "Liquidità conto corrente", "valore_eur": 7266, "versato_eur": 7266},
        {"id": "etf_fwia", "nome": "Invesco FTSE All-World (FWIA)", "valore_eur": 3429.01, "versato_eur": 3355},
        {"id": "etf_sp500", "nome": "Invesco S&P 500", "valore_eur": 2509.42, "versato_eur": 2440},
        {"id": "tr_riserva_speculativa", "nome": "Riserva", "valore_eur": 806.55, "versato_eur": 803.79},
        {"id": "leonardo", "nome": "Leonardo (LDO)", "valore_eur": 1220.40, "versato_eur": 1200},
        {"id": "cameco", "nome": "Cameco (CCJ)", "valore_eur": 271.46, "versato_eur": 250},
        {"id": "bbio", "nome": "BridgeBio (BBIO)", "valore_eur": 129.02, "versato_eur": 138},
        {"id": "pokemon", "nome": "Carte Pokémon", "valore_eur": 2652.42, "versato_eur": 2500},
        {"id": "coinshares", "nome": "CoinShares", "valore_eur": 739.34, "versato_eur": 1082.27},
    ]}
    target = {"allocazione": [{"id": "fwra", "peso": 0.59}, {"id": "sp500_synth", "peso": 0.41}]}
    rules = {"rischio": {"soglia_ribilanciamento_pct": 0.05,
                         "fondo_emergenza_eur": {"min": 5000, "max": 6000}}}
    txt = build_dashboard(portfolio, target, rules)
    assert "Cruscotto" in txt and "Patrimonio" in txt, txt
    assert "Comparto speculativo" in txt and "12,8%" in txt, txt   # 2427,43/19023,62
    assert "Leonardo" in txt and "BridgeBio" in txt, txt
    assert "58% vs 59% target" in txt, txt                          # FWIA 3429/(3429+2509)
    assert "coperto ✓" in txt, txt                                  # cash 7266 >= 5000
    print("selftest cruscotto OK: patrimonio, speculativo %, ETF vs target, fondo emergenza.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(cmd_selftest())
    print(build_dashboard())
