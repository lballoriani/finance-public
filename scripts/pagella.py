#!/usr/bin/env python3
"""Pagella — la riserva speculativa rende davvero più dell'ETF?

Confronto onesto: il "comparto speculativo" (liquidità della riserva su TR + azioni
singole su TR) contro un gemello immaginario che avesse messo gli STESSI soldi, nelle
STESSE date, sull'ETF principale del PAC (la voce di data/target.json col peso più
alto). Se dopo 6-12 mesi il gemello vince, il nostro metodo non sta aggiungendo
valore e se ne parla con l'utente (mai decisioni automatiche). Antidoto misurabile all'eccesso di sicurezza
(Barber & Odean: chi scambia di più rende meno).

Regole del confronto:
- Le compravendite DENTRO il comparto (vendi un titolo, ne compri un altro) non sono flussi:
  spostano solo soldi da azioni a liquidità. Le tasse pagate su TR riducono il comparto.
- Sono flussi solo i soldi che ENTRANO (versamento mensile in riserva) o ESCONO
  (sweep verso gli ETF): il gemello compra/vende quote dell'ETF al prezzo di quel giorno.
- Una fotografia al mese (la prima utile): python3 pagella.py snapshot

Uso:
  python3 pagella.py report
  python3 pagella.py snapshot [AAAA-MM-GG]           # fotografia del mese (una sola)
  python3 pagella.py flusso <importo> <versamento|sweep> [AAAA-MM-GG] [prezzo_etf]
  python3 pagella.py selftest
"""
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from quotes import load_quotes  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "data")
REGISTRO = os.path.join(DATA, "pagella-riserva.json")
SOGLIA_MESI, SOGLIA_PUNTI = 6, -5.0   # dopo 6 mesi, sotto di oltre 5 punti → parlarne


def eur(x):
    x = 0.0 if abs(x) < 0.005 else x
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pc(x, segno=True):
    return (f"{x:+.1f}" if segno else f"{x:.1f}").replace(".", ",")


def valore_comparto(portfolio):
    """Liquidità riserva TR + azioni singole su TR (esclusi ETF, crypto, collezionabili)."""
    tot, righe = 0.0, []
    for p in portfolio.get("posizioni", []):
        su_tr = p.get("broker") == "Trade Republic"
        if su_tr and (p.get("id") == "tr_riserva_speculativa" or p.get("tipo") == "azione"):
            tot += p["valore_eur"]
            righe.append(p["id"])
    return round(tot, 2), righe


def benchmark(target=None):
    """(ISIN, simbolo Yahoo) dell'ETF di riferimento: la voce col peso più alto in target.json."""
    tgt = target or json.load(open(os.path.join(DATA, "target.json")))
    a = max(tgt["allocazione"], key=lambda x: x.get("peso", 0))
    return a["isin"], a.get("yahoo")


def prezzo_etf(portfolio=None, quotes=None, target=None):
    """Prezzo quota dell'ETF benchmark: quotes.json fresco, altrimenti snapshot TR, altrimenti None."""
    isin, sym = benchmark(target)
    q = (quotes if quotes is not None else load_quotes(max_age_hours=72)).get(sym)
    if q and q.get("prezzo_eur"):
        return q["prezzo_eur"], "quotes"
    try:
        snap = json.load(open(os.path.join(DATA, "portfolio-tr-snapshot.json")))
        p = snap["positions"][isin]
        return p["value_eur"] / p["qty"], "tr-snapshot"
    except Exception:
        return None, None


def quote_gemello(reg, fino_a=None):
    """Quote ETF del gemello: base + flussi (entrate comprano, uscite vendono)."""
    q = reg["base"]["quote_etf"]
    for f in reg.get("flussi", []):
        if fino_a is None or f["data"] <= fino_a:
            q += f["importo_eur"] / f["prezzo_etf"]
    return q


def capitale_netto(reg, fino_a=None):
    return reg["base"]["valore_eur"] + sum(f["importo_eur"] for f in reg.get("flussi", [])
                                           if fino_a is None or f["data"] <= fino_a)


def mesi_tra(a, b):
    a, b = date.fromisoformat(a), date.fromisoformat(b)
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30


def fotografia(reg, giorno, valore, prezzo):
    g = giorno.isoformat()
    gem = quote_gemello(reg, g) * prezzo
    cap = capitale_netto(reg, g)
    return {"data": g, "valore_comparto_eur": round(valore, 2), "prezzo_etf": round(prezzo, 4),
            "valore_gemello_etf_eur": round(gem, 2), "capitale_netto_eur": round(cap, 2),
            "rend_comparto_pct": round((valore / cap - 1) * 100, 2),
            "rend_gemello_pct": round((gem / cap - 1) * 100, 2),
            "differenza_eur": round(valore - gem, 2)}


def verdetto(reg, f):
    mesi = mesi_tra(reg["base"]["data"], f["data"])
    punti = f["rend_comparto_pct"] - f["rend_gemello_pct"]
    if mesi < SOGLIA_MESI:
        return f"Troppo presto per giudicare ({pc(mesi, False)} mesi su {SOGLIA_MESI}): si guarda la tendenza."
    if punti < SOGLIA_PUNTI:
        return (f"⚠️ Dopo {mesi:.0f} mesi la riserva rende {pc(abs(punti), False)} punti MENO dell'ETF: "
                f"il metodo non sta aggiungendo valore → parlarne con l'utente (più ETF? meno operazioni?).")
    if punti < 0:
        return f"Dopo {mesi:.0f} mesi la riserva è leggermente sotto l'ETF ({pc(punti)} punti): da tenere d'occhio."
    return f"Dopo {mesi:.0f} mesi la riserva batte l'ETF di {pc(punti)} punti."


def render(reg):
    fs = reg.get("fotografie", [])
    if not fs:
        return "Pagella: nessuna fotografia ancora."
    f = fs[-1]
    out = [f"📊 Pagella riserva vs ETF del PAC (dal {reg['base']['data']}, ultima foto {f['data']})",
           f"  Riserva + azioni: €{eur(f['valore_comparto_eur'])} ({pc(f['rend_comparto_pct'])}%)",
           f"  Stessi soldi sull'ETF: €{eur(f['valore_gemello_etf_eur'])} ({pc(f['rend_gemello_pct'])}%)",
           f"  Differenza: {'-' if f['differenza_eur'] < -0.005 else '+'}€{eur(abs(f['differenza_eur']))} · soldi netti messi: €{eur(f['capitale_netto_eur'])}",
           "  " + verdetto(reg, f)]
    return "\n".join(out)


def _load():
    return json.load(open(REGISTRO))


def _save(reg):
    with open(REGISTRO, "w") as fh:
        json.dump(reg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def cmd_snapshot(args):
    giorno = date.fromisoformat(args[0]) if args else date.today()
    reg = _load()
    mese = giorno.isoformat()[:7]
    if any(f["data"][:7] == mese for f in reg.get("fotografie", [])):
        print(f"Fotografia di {mese} già presente: nulla da fare.")
        print(render(reg))
        return 0
    pf = json.load(open(os.path.join(DATA, "portfolio.json")))
    valore, _ = valore_comparto(pf)
    prezzo, fonte = prezzo_etf(pf)
    if prezzo is None:
        print("⚠️ Prezzo ETF non disponibile (quotes.json vecchio e snapshot TR assente): fotografia NON scattata.")
        return 1
    reg.setdefault("fotografie", []).append(fotografia(reg, giorno, valore, prezzo))
    reg["fotografie"][-1]["fonte_prezzo_etf"] = fonte
    reg["fotografie"][-1]["portfolio_as_of"] = pf.get("as_of")
    _save(reg)
    print(render(reg))
    return 0


def cmd_flusso(args):
    if len(args) < 2 or args[1] not in ("versamento", "sweep"):
        print("uso: pagella.py flusso <importo> <versamento|sweep> [AAAA-MM-GG] [prezzo_etf]")
        return 2
    imp = abs(float(args[0].replace(",", ".")))
    giorno = args[2] if len(args) > 2 else date.today().isoformat()
    if len(args) > 3:
        prezzo, fonte = float(args[3].replace(",", ".")), "manuale"
    else:
        prezzo, fonte = prezzo_etf()
    if prezzo is None:
        print("⚠️ Prezzo ETF non disponibile: passalo a mano come quarto argomento.")
        return 1
    reg = _load()
    reg.setdefault("flussi", []).append({
        "data": giorno, "tipo": args[1], "importo_eur": imp if args[1] == "versamento" else -imp,
        "prezzo_etf": round(prezzo, 4), "fonte_prezzo_etf": fonte})
    reg["flussi"].sort(key=lambda f: f["data"])
    _save(reg)
    print(f"Registrato {args[1]} di €{eur(imp)} il {giorno} (ETF €{prezzo:.4f}).")
    return 0


def cmd_selftest():
    reg = {"base": {"data": "2026-09-29", "valore_eur": 1000.0, "prezzo_etf": 10.0, "quote_etf": 100.0},
           "flussi": [{"data": "2026-10-15", "tipo": "versamento", "importo_eur": 250.0, "prezzo_etf": 12.5}]}
    assert quote_gemello(reg) == 120.0 and quote_gemello(reg, "2026-10-01") == 100.0
    assert capitale_netto(reg) == 1250.0
    # ETF a 11: gemello = 120 x 11 = 1320; comparto 1400 → batte di 80€
    f = fotografia(reg, date(2026, 11, 2), 1400.0, 11.0)
    assert f["valore_gemello_etf_eur"] == 1320.0 and f["differenza_eur"] == 80.0, f
    assert f["rend_comparto_pct"] == 12.0 and f["rend_gemello_pct"] == 5.6, f
    assert verdetto(reg, f).startswith("Troppo presto")
    # dopo 7 mesi, sotto di 8 punti → avviso
    f2 = fotografia(reg, date(2027, 4, 30), 1250.0, 11.0)
    assert "MENO dell'ETF" in verdetto(reg, f2), verdetto(reg, f2)
    # sweep: esce denaro → il gemello vende quote
    reg["flussi"].append({"data": "2026-12-01", "tipo": "sweep", "importo_eur": -110.0, "prezzo_etf": 11.0})
    assert quote_gemello(reg) == 110.0 and capitale_netto(reg) == 1140.0
    # valore comparto: solo riserva TR + azioni TR
    pf = {"posizioni": [
        {"id": "tr_riserva_speculativa", "broker": "Trade Republic", "tipo": "cash", "valore_eur": 500},
        {"id": "alpha", "broker": "Trade Republic", "tipo": "azione", "valore_eur": 600},
        {"id": "etf_world", "broker": "Trade Republic", "tipo": "ETF", "valore_eur": 4000},
        {"id": "cash", "broker": "Conto corrente", "tipo": "cash", "valore_eur": 8000},
        {"id": "crypto_etp", "broker": "Altro broker", "tipo": "crypto", "valore_eur": 300}]}
    assert valore_comparto(pf) == (1100.0, ["tr_riserva_speculativa", "alpha"])
    tgt = {"allocazione": [{"isin": "IE0000000029", "yahoo": "EMER.DE", "peso": 0.2},
                           {"isin": "IE0000000011", "yahoo": "WRLD.DE", "peso": 0.8}]}
    assert benchmark(tgt) == ("IE0000000011", "WRLD.DE")
    assert prezzo_etf(quotes={"WRLD.DE": {"prezzo_eur": 100.5}}, target=tgt) == (100.5, "quotes")
    print("selftest pagella OK: gemello ETF, flussi in/out, rendimenti, verdetto, perimetro comparto.")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else "report"
    if cmd == "report":
        print(render(_load()))
        sys.exit(0)
    fn = {"snapshot": cmd_snapshot, "flusso": cmd_flusso}.get(cmd)
    if fn:
        sys.exit(fn(a[1:]))
    if cmd == "selftest":
        sys.exit(cmd_selftest())
    print("comandi: report | snapshot [data] | flusso <importo> <versamento|sweep> [data] [prezzo] | selftest")
    sys.exit(2)
