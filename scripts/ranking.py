#!/usr/bin/env python3
"""Ranking — classifica le tesi per RENDIMENTO NETTO AL MESE.

Obiettivo della riserva speculativa: moltiplicare il capitale nel minor tempo
possibile. Un +15% in 6 settimane batte un +40% in 18 mesi se i soldi si possono
riciclare, quindi le occasioni si confrontano sul rendimento mensile composto:

    mensile = (1 + ev_netto_pct) ** (1 / orizzonte_mesi) - 1

Input da watchlist.json → tesi attive con `scenari.ev_netto_pct` e un orizzonte
(`scenari.orizzonte_mesi`, oppure `orizzonte_settimane` per le tesi breve).
Nessun costo AI, sola lettura.

Uso:
  python3 ranking.py            # classifica tesi attive
  python3 ranking.py selftest   # test offline
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
WEEKS_PER_MONTH = 4.33


def monthly(ev_pct, months):
    """Rendimento netto mensile composto (in %) da EV netto totale e orizzonte."""
    if ev_pct is None or not months or months <= 0:
        return None
    base = 1 + ev_pct / 100
    if base <= 0:
        return -100.0
    return (base ** (1 / months) - 1) * 100


def horizon_months(t):
    s = t.get("scenari") or {}
    if s.get("orizzonte_mesi"):
        return float(s["orizzonte_mesi"])
    weeks = t.get("orizzonte_settimane") or s.get("orizzonte_settimane")
    return float(weeks) / WEEKS_PER_MONTH if weeks else None


def rank(watchlist=None):
    wl = watchlist or json.load(open(os.path.join(DATA, "watchlist.json")))
    rows, missing = [], []
    for t in wl.get("tesi", []):
        if t.get("tier") != "attiva":
            continue
        ev = (t.get("scenari") or {}).get("ev_netto_pct")
        m = horizon_months(t)
        mo = monthly(ev, m)
        if mo is None:
            missing.append(t.get("id"))
            continue
        rows.append({"id": t.get("id"), "ticker": t.get("ticker"), "bucket": t.get("bucket"),
                     "status": t.get("status"), "ev": ev, "mesi": m, "mensile": mo})
    rows.sort(key=lambda r: r["mensile"], reverse=True)
    return rows, missing


def render(rows, missing):
    out = ["📈 Classifica per rendimento netto al mese (tasse tolte)", ""]
    for i, r in enumerate(rows, 1):
        tag = "🟡" if r["bucket"] == "breve" else "🟢"
        pos = " [in posizione]" if r["status"] == "in_posizione" else ""
        out.append(f"{i}. {tag} {r['ticker'] or r['id']}: ~{r['mensile']:.1f}%/mese "
                   f"(EV {r['ev']:+.0f}% in ~{r['mesi']:.1f} mesi){pos}")
    if missing:
        out += ["", "Senza EV o orizzonte (da completare): " + ", ".join(missing)]
    return "\n".join(out)


def cmd_selftest():
    # 15% in 6 settimane deve battere 40% in 18 mesi
    short = monthly(15, 6 / WEEKS_PER_MONTH)
    long_ = monthly(40, 18)
    assert short > long_, (short, long_)
    assert abs(monthly(12.68, 12) - 1.0) < 0.01
    assert monthly(None, 12) is None and monthly(10, 0) is None
    wl = {"tesi": [
        {"id": "a", "ticker": "A", "tier": "attiva", "bucket": "lungo",
         "scenari": {"ev_netto_pct": 27, "orizzonte_mesi": 18}},
        {"id": "b", "ticker": "B", "tier": "attiva", "bucket": "breve", "orizzonte_settimane": 5,
         "scenari": {"ev_netto_pct": 15}},
        {"id": "c", "ticker": "C", "tier": "attiva", "scenari": {"ev_netto_pct": 10}},
        {"id": "d", "ticker": "D", "tier": "radar", "scenari": {"ev_netto_pct": 90, "orizzonte_mesi": 1}},
    ]}
    rows, missing = rank(wl)
    assert [r["id"] for r in rows] == ["b", "a"], rows
    assert missing == ["c"], missing
    print("selftest ranking OK: breve batte lungo, radar esclusi, mancanti segnalati.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(cmd_selftest())
    print(render(*rank()))
