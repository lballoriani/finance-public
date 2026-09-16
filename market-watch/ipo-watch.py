#!/usr/bin/env python3
"""IPO watch engine — sorveglia EDGAR per l'S-1 PUBBLICO delle IPO in watchlist.

Trade Republic non ci avvisa quando una società si quota; EDGAR (l'archivio
ufficiale della SEC) sì. Finché il prospetto (S-1/F-1) è depositato in modo
CONFIDENZIALE non compare nella ricerca pubblica: nell'istante in cui la società
lo rende PUBBLICO (fine fase confidenziale, ~1° passo verso il roadshow) il
filing appare nella full-text search. Questo è ESATTAMENTE il trigger d'ingresso
delle tesi IPO (anthropic-ipo, databricks-ipo, openai-ipo).

Cosa fa:
  1. interroga la full-text search di EDGAR per ogni società target;
  2. tiene solo i filing dove la società target è il DEPOSITANTE (non le migliaia
     di documenti che la citano soltanto) e con forma S-1/F-1/424B;
  3. alla PRIMA comparsa di un filing nuovo scrive una riga in
     market-watch/outbox.md -> notifica Telegram + promemoria della checklist IPO
     (aspetta 2-4 settimane, mai il picco del giorno 1, tranche piccola).

Serverless: pensato per girare in GitHub Actions (rete libera, vedi
.github/workflows/ipo-watch.yml). Lo stato (accession già viste) sta in
market-watch/.ipo-state.json così non si riavvisa due volte lo stesso filing.
Nessun costo AI: solo Python + una manciata di richieste a EDGAR.

Uso:
  python3 ipo-watch.py check      # interroga EDGAR e avvisa sui filing NUOVI
  python3 ipo-watch.py report     # mostra i match attuali senza toccare nulla
  python3 ipo-watch.py selftest   # test offline del riconoscimento (no rete)

Nota SEC: la full-text search indicizza SOLO i filing pubblici e richiede uno
User-Agent descrittivo con un contatto (impostato sotto). Rate limit generoso
(<10 req/s): qui facciamo pochissime richieste.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATE = os.path.join(HERE, ".ipo-state.json")
OUTBOX = os.path.join(HERE, "outbox.md")
WATCHLIST = os.path.join(REPO, "data", "watchlist.json")
CALENDAR = os.path.join(REPO, "data", "calendar.json")

# EDGAR pretende uno User-Agent con contatto (altrimenti 403).
USER_AGENT = os.environ.get("EDGAR_UA", "finance-ipo-watch your-email@example.com")
EFTS = "https://efts.sec.gov/LATEST/search-index"

# Forme che segnano l'uscita allo scoperto del prospetto (root form EDGAR).
TARGET_FORMS = ["S-1", "F-1"]
# Da quanti giorni indietro guardare (evita di ripescare vecchi filing omonimi).
LOOKBACK_DAYS = 150

# Alias per il nome del DEPOSITANTE su EDGAR (maiuscolo) quando non coincide col
# semplice nome-tesi in maiuscolo. Il match è sul depositante (non sul testo) per
# scartare i filing che citano soltanto la società.
NAME_ALIASES = {
    "OpenAI": ["OPENAI", "OPEN AI"],
}
# Le 3 IPO note restano garantite anche se un domani sparissero dai file.
CORE_IPOS = [("Anthropic", "anthropic-ipo"), ("Databricks", "databricks-ipo"),
             ("OpenAI", "openai-ipo")]


def _keys_for(asset):
    return NAME_ALIASES.get(asset, [asset.upper()])


def load_targets():
    """Costruisce la lista delle società da sorvegliare LEGGENDO i file del repo.

    Prende ogni tesi/evento il cui id/tesi_id finisce in '-ipo' da watchlist.json
    e calendar.json (così quando market-watch aggiunge una nuova IPO, l'engine la
    include da solo), più le 3 IPO 'core' garantite. Dedup per tesi_id.
    """
    by_id = {}

    def add(asset, tesi_id):
        if not asset or not tesi_id or tesi_id in by_id:
            return
        by_id[tesi_id] = {"asset": asset, "tesi_id": tesi_id, "keys": _keys_for(asset)}

    try:
        wl = json.load(open(WATCHLIST))
        for t in wl.get("tesi", []):
            if str(t.get("id", "")).endswith("-ipo"):
                add(t.get("asset"), t["id"])
    except Exception as e:
        log(f"watchlist non letta: {e}")
    try:
        cal = json.load(open(CALENDAR))
        for ev in cal.get("eventi", []):
            if str(ev.get("tesi_id", "")).endswith("-ipo"):
                add(ev.get("asset"), ev["tesi_id"])
    except Exception as e:
        log(f"calendar non letto: {e}")
    for asset, tesi_id in CORE_IPOS:
        add(asset, tesi_id)
    return list(by_id.values())


def log(msg):
    print(f"[ipo-watch] {msg}")


def load_state():
    try:
        with open(STATE) as f:
            s = json.load(f)
    except Exception:
        s = {}
    s.setdefault("seen", [])   # lista di accession già notificate
    return s


def save_state(state):
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def edgar_search(query, forms, startdt):
    """Interroga la full-text search di EDGAR. Ritorna la lista di hit grezzi."""
    params = {
        "q": f'"{query}"',
        "forms": ",".join(forms),
        "startdt": startdt,
        "enddt": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    url = f"{EFTS}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return data.get("hits", {}).get("hits", [])


def hit_matches(hit, target):
    """True se il DEPOSITANTE del filing è la società target (non una che la cita)."""
    src = hit.get("_source", {})
    names = " | ".join(src.get("display_names", [])).upper()
    return any(k in names for k in target["keys"])


def filing_url(hit):
    """Link alla pagina-indice del filing su EDGAR."""
    src = hit.get("_source", {})
    ciks = src.get("ciks") or ["0"]
    cik_int = int(ciks[0])
    adsh = src.get("adsh", "")
    nodash = adsh.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{nodash}/{adsh}-index.htm"


def collect_matches():
    """Interroga EDGAR per ogni target e ritorna i filing di cui è depositante.

    Ritorna lista di dict: asset, tesi_id, adsh, form, file_date, registrant, url.
    """
    startdt = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    found = []
    for t in load_targets():
        for key in t["keys"]:
            try:
                hits = edgar_search(key.title(), TARGET_FORMS, startdt)
            except Exception as e:
                log(f"ricerca '{key}' fallita: {e}")
                continue
            for h in hits:
                if not hit_matches(h, t):
                    continue
                src = h.get("_source", {})
                adsh = src.get("adsh", "")
                if any(f["adsh"] == adsh for f in found):
                    continue
                found.append({
                    "asset": t["asset"],
                    "tesi_id": t["tesi_id"],
                    "adsh": adsh,
                    "form": src.get("form", "?"),
                    "file_date": src.get("file_date", "?"),
                    "registrant": (src.get("display_names") or ["?"])[0],
                    "url": filing_url(h),
                })
            time.sleep(0.5)   # gentile con EDGAR
    return found


def alert_line(f):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return (
        f"- {ts} — 🟢 {f['asset']} IPO — PROSPETTO PUBBLICO su EDGAR! "
        f"Depositato il {f['file_date']} (form {f['form']}, {f['registrant']}). "
        f"Il trigger d'ingresso della tesi si è avvicinato: da qui parte la checklist IPO — "
        f"1) attendi la forbice di prezzo definitiva e il roadshow; "
        f"2) NON comprare sul picco del primo giorno; "
        f"3) valuta l'ingresso dopo le prime 2-4 settimane, a tranche PICCOLA dalla riserva. "
        f"Filing: {f['url']} — decidi tu.\n"
    )


def append_outbox(lines):
    with open(OUTBOX, "a") as f:
        for ln in lines:
            f.write(ln)


def cmd_check():
    state = load_state()
    seen = set(state["seen"])
    matches = collect_matches()
    new = [f for f in matches if f["adsh"] not in seen]
    state["last_check"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not new:
        log(f"nessun filing NUOVO ({len(matches)} match noti). Niente da notificare.")
        save_state(state)   # battito: registra comunque l'ultima verifica
        return 0
    lines = [alert_line(f) for f in new]
    append_outbox(lines)
    for f in new:
        seen.add(f["adsh"])
        log(f"NUOVO: {f['asset']} {f['form']} {f['file_date']} ({f['adsh']}) -> outbox")
    state["seen"] = sorted(seen)
    state["last_check"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    return 0


def cmd_report():
    matches = collect_matches()
    if not matches:
        print("Nessun prospetto pubblico trovato per le società sorvegliate "
              f"({', '.join(t['asset'] for t in load_targets())}).")
        print("È il quadro atteso finché restano in fase confidenziale.")
        return 0
    print(f"{len(matches)} filing trovati (depositante = società target):")
    for f in matches:
        print(f"  • {f['asset']:12} {f['form']:8} {f['file_date']}  {f['registrant']}")
        print(f"    {f['url']}")
    return 0


def cmd_selftest():
    """Verifica offline che il riconoscimento del depositante sia corretto."""
    sample = [
        # vero positivo: Anthropic è il depositante
        {"_source": {"display_names": ["Anthropic PBC (CIK 0001999999)"],
                     "form": "S-1", "file_date": "2026-10-01",
                     "adsh": "0001999999-26-000001", "ciks": ["0001999999"]}},
        # falso positivo da scartare: cita "Anthropic" ma il depositante è altro
        {"_source": {"display_names": ["Elements Ventures Group Inc.  (CIK 0001954227)"],
                     "form": "S-1", "file_date": "2026-07-17",
                     "adsh": "0002097570-26-000025", "ciks": ["0001954227"]}},
    ]
    anthropic = {"asset": "Anthropic", "tesi_id": "anthropic-ipo", "keys": ["ANTHROPIC"]}
    assert hit_matches(sample[0], anthropic) is True, "Anthropic depositante non riconosciuto"
    assert hit_matches(sample[1], anthropic) is False, "falso positivo non scartato"
    # URL ben formato
    url = filing_url(sample[0])
    assert url == ("https://www.sec.gov/Archives/edgar/data/1999999/"
                   "000199999926000001/0001999999-26-000001-index.htm"), url
    # riga di alert coerente col formato outbox (inizia con '- ' e cita la checklist)
    f = {"asset": "Anthropic", "tesi_id": "anthropic-ipo", "adsh": "x", "form": "S-1",
         "file_date": "2026-10-01", "registrant": "Anthropic PBC", "url": url}
    line = alert_line(f)
    assert line.startswith("- ") and "🟢" in line and "2-4 settimane" in line, line
    print("selftest OK: match depositante, scarto citazioni, URL e riga outbox corretti.")
    return 0


def cmd_targets():
    """Mostra chi è sorvegliato adesso (letto dai file)."""
    for t in load_targets():
        print(f"  {t['asset']:14} tesi={t['tesi_id']:18} keys={t['keys']}")
    return 0


COMMANDS = {"check": cmd_check, "report": cmd_report,
            "targets": cmd_targets, "selftest": cmd_selftest}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "check"
    fn = COMMANDS.get(action)
    if not fn:
        print(f"comando sconosciuto: {action}. Usa: {', '.join(COMMANDS)}")
        sys.exit(2)
    sys.exit(fn())
