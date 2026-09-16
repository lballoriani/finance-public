#!/usr/bin/env python3
"""Digest settimanale 'la settimana che viene' -> Telegram.

Ogni domenica sera manda a Luca i catalizzatori in arrivo (da data/calendar.json):
i prossimi eventi con DATA CERTA (con quanti giorni mancano) e quelli a data
STIMATA ancora sul radar. Serve a non farsi trovare impreparati sulle finestre.

Gira su GitHub Actions (rete libera): invia DIRETTAMENTE via api.telegram.org
(come telegram-notify), quindi niente outbox. Nessun costo AI.

Uso:
  python3 weekly-digest.py preview   # stampa il messaggio, non invia
  python3 weekly-digest.py send      # invia via TELEGRAM_BOT_TOKEN/CHAT_ID (env)
  python3 weekly-digest.py selftest  # test offline (date, filtri, formattazione)
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CALENDAR = os.path.join(REPO, "data", "calendar.json")

MAX_ESATTI = 6      # quanti eventi datati elencare
MAX_STIMATI = 4     # quante stime elencare
BRIEF_CHARS = 110   # taglio delle descrizioni lunghe


def _today():
    return datetime.now(timezone.utc).date()


def brief(txt):
    """Prima frase/segmento di una descrizione lunga, con cap di lunghezza."""
    txt = (txt or "").strip()
    for sep in (" — ", ". ", " (", ";"):
        i = txt.find(sep)
        if 0 < i <= BRIEF_CHARS:
            return txt[:i].strip()
    return txt if len(txt) <= BRIEF_CHARS else txt[:BRIEF_CHARS].rstrip() + "…"


def parse_exact(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _last_day(y, mo):
    return date(y, 12, 31) if mo == 12 else date(y, mo + 1, 1) - timedelta(days=1)


def estimate_end(s):
    """Data ULTIMA plausibile per una label stimata (YYYY / YYYY-MM / YYYY-Qn[/Qm])."""
    s = (s or "").strip()
    m = re.match(r"^(\d{4})$", s)
    if m:
        return date(int(m.group(1)), 12, 31)
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        return _last_day(int(m.group(1)), int(m.group(2)))
    m = re.match(r"^(\d{4})-Q([1-4])(?:/Q?([1-4]))?$", s)
    if m:
        y = int(m.group(1)); q = int(m.group(3) or m.group(2))
        return _last_day(y, q * 3)
    return None


def load_events():
    return json.load(open(CALENDAR)).get("eventi", [])


def build_message(today=None, events=None):
    today = today or _today()
    events = events if events is not None else load_events()
    esatti, stimati = [], []
    for e in events:
        d = parse_exact(e.get("data", ""))
        if d is not None:
            if (d - today).days >= 0:
                esatti.append(((d - today).days, d, e))
        else:
            end = estimate_end(e.get("data", ""))
            if end is None or end >= today:      # scarta le stime ormai passate
                stimati.append((end or date.max, e))
    esatti.sort(key=lambda x: x[0])
    stimati.sort(key=lambda x: x[0])
    esatti = esatti[:MAX_ESATTI]
    stimati = stimati[:MAX_STIMATI]

    if not esatti and not stimati:
        return None

    out = ["🗓 Catalizzatori in arrivo (la settimana che viene)", ""]
    if esatti:
        out.append("📌 Con data certa:")
        for days, d, e in esatti:
            tick = f" ({e['ticker']})" if e.get("ticker") else ""
            mark = "⚠️ " if days <= 7 else ("🔸 " if days <= 14 else "")
            quando = "oggi" if days == 0 else ("domani" if days == 1 else f"tra {days}g")
            out.append(f"• {mark}{quando} ({d.strftime('%d/%m')}) — {e['asset']}{tick}: {brief(e.get('evento'))}")
    if stimati:
        out.append("")
        out.append("🔭 Sul radar (date stimate):")
        for _end, e in stimati:
            tick = f" ({e['ticker']})" if e.get("ticker") else ""
            out.append(f"• ~{e['data']} — {e['asset']}{tick}: {brief(e.get('evento'))}")
    out.append("")
    out.append("Gli ingressi restano legati ai trigger, non alle date. Decidi tu.")
    return "\n".join(out)


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("[weekly-digest] secrets TELEGRAM mancanti: non invio.")
        return 1
    data = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    r = json.load(urllib.request.urlopen(req, timeout=20))
    print("[weekly-digest] inviato." if r.get("ok") else f"[weekly-digest] errore: {r}")
    return 0 if r.get("ok") else 1


def cmd_preview():
    msg = build_message()
    print(msg if msg else "(nessun catalizzatore in arrivo)")
    return 0


def cmd_send():
    msg = build_message()
    if not msg:
        print("[weekly-digest] nessun catalizzatore in arrivo: niente da inviare.")
        return 0
    return send(msg)


def cmd_selftest():
    sample = [
        {"data": "2026-11-22", "asset": "Capricor", "ticker": "CAPR",
         "evento": "PDUFA deramiocel (DMD) — evento regolatorio binario. Data estesa."},
        {"data": "2026-11-27", "asset": "BridgeBio", "ticker": "BBIO",
         "evento": "PDUFA BBP-418 (LGMD2I/R9) — evento regolatorio binario"},
        {"data": "2026-Q3", "asset": "BridgeBio", "ticker": "BBIO",
         "evento": "NDA infigratinib (Q3 2026) — catalizzatore aggiuntivo"},
        {"data": "2026-Q3/Q4", "asset": "Anthropic", "ticker": None,
         "evento": "IPO su Nasdaq (ottobre citato come riferimento)"},
        {"data": "2020-01-01", "asset": "Vecchio", "ticker": None, "evento": "passato"},
    ]
    # a metà novembre: Capricor entro 7g (⚠️), BBIO entro 14g (🔸), stime Q3 scartate
    msg = build_message(today=date(2026, 11, 20), events=sample)
    assert "Capricor" in msg and "⚠️" in msg, msg
    assert "tra 2g" in msg and "tra 7g" in msg, msg
    assert "2026-Q3/Q4" in msg and "Anthropic" in msg, msg           # Q4 ancora futuro
    assert "NDA infigratinib" not in msg, "una stima Q3 passata non va mostrata a metà nov"
    assert "Vecchio" not in msg, "un evento datato passato non va mostrato"
    # brief() accorcia le descrizioni lunghe
    assert brief("PDUFA deramiocel (DMD) — evento binario") == "PDUFA deramiocel (DMD)"
    # nessun evento -> nessun messaggio
    assert build_message(today=date(2030, 1, 1), events=sample) is None
    print("selftest weekly-digest OK: filtri date, imminenza, brief, vuoto.")
    return 0


COMMANDS = {"preview": cmd_preview, "send": cmd_send, "selftest": cmd_selftest}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "preview"
    fn = COMMANDS.get(action)
    if not fn:
        print(f"comando sconosciuto: {action}. Usa: {', '.join(COMMANDS)}")
        sys.exit(2)
    sys.exit(fn())
