#!/usr/bin/env python3
"""Ponte Telegram -> repo (comandi IN ENTRATA), modalita' "a chiamata".

Rovescio del relay in uscita (telegram-notify.yml): quello manda i messaggi
DA noi A Luca; questo legge i messaggi CHE Luca scrive al bot ed esegue comandi.
Gira in GitHub Actions ogni ~10 min (serverless: nessuna macchina accesa,
riusa TELEGRAM_BOT_TOKEN/CHAT_ID).

Costo contenuto ("a chiamata"): il polling e i comandi ping/stato/aiuto NON
usano AI (solo Python). Il parere ragionato completo ("aggiornami") fa partire
un ragionamento Claude nel workflow SOLO quando Luca lo chiede davvero (raro:
~3 volte a settimana in ferie), non a timer.

Sicurezza: risponde SOLO al chat autorizzato (TELEGRAM_CHAT_ID). Ignora tutto
il resto e i messaggi piu' vecchi di FRESH_SECONDS (niente backlog).

Uso:
  python3 tg-bridge.py poll          # interroga Telegram: comandi + tap sulle schede
  python3 tg-bridge.py send FILE     # invia a Telegram il contenuto di FILE (chunked)
  python3 tg-bridge.py ticket FILE   # mette in coda una scheda-ordine (JSON) -> Telegram
  python3 tg-bridge.py tickets       # elenca le schede-ordine in coda
  python3 tg-bridge.py handle-update FILE  # webhook: processa un singolo update (JSON)
  python3 tg-bridge.py send-tickets  # invia le schede-ordine in coda (workflow su push)
  python3 tg-bridge.py selftest      # test offline del flusso schede (no Telegram)

Comandi:
  ping            -> il ponte e' vivo
  stato / status  -> foto rapida del portafoglio (no AI)
  aiuto / help    -> elenco comandi
  aggiornami      -> se il parere AI e' abilitato (env DIGEST_ENABLED=1): ack +
                     segnala al workflow di generarlo (need_digest); altrimenti
                     ripiega sulla foto rapida.
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
STATE = os.path.join(HERE, ".tg-state.json")
REQUESTS = os.path.join(HERE, "requests.md")
TICKETS = os.path.join(HERE, "order-tickets.json")   # coda schede-ordine
TRADES = os.path.join(HERE, "trades.md")             # operazioni confermate (da riconciliare)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"

FRESH_SECONDS = 7200   # ignora messaggi piu' vecchi di 2h (tollera i ritardi del cron GitHub;
                       # oltre la 1a run l'offset in .tg-state.json evita comunque i duplicati)
MAXLEN = 3800          # Telegram taglia a ~4096: spezziamo prima


def api(method, params=None, timeout=30):
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def send(text):
    text = text.strip() or "(vuoto)"
    for i in range(0, len(text), MAXLEN):
        try:
            api("sendMessage", {
                "chat_id": CHAT,
                "text": text[i:i + MAXLEN],
                "disable_web_page_preview": "true",
            })
        except Exception as e:
            print(f"[tg-bridge] invio fallito: {e}")


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {"offset": 0}


def save_state(state):
    with open(STATE, "w") as f:
        json.dump(state, f)


def gha_output(key, value):
    """Espone un output di step al workflow (file $GITHUB_OUTPUT)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{key}={value}\n")


def eur(x):
    """12345.6 -> '12.345,60' (formato italiano)."""
    s = f"{x:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def portfolio_snapshot():
    p = json.load(open(os.path.join(REPO, "data", "portfolio.json")))
    pos = {x["id"]: x for x in p["posizioni"]}
    out = [f"📊 Stato al {p['as_of']}", ""]

    # Riserva + split soft 40/60
    r = pos.get("tr_riserva_speculativa", {}).get("valore_eur")
    if r is not None:
        try:
            rules = json.load(open(os.path.join(REPO, "data", "rules.json")))
            bs = rules.get("budget_speculativo", {})
            pct_l = bs.get("bucket_lungo_pct", 0.40)
            pct_b = bs.get("bucket_breve_pct", 0.60)
            b_lungo = r * pct_l
            b_breve = r * pct_b
            out.append(
                f"💶 Riserva: €{eur(r)} | "
                f"🟢 Lungo ~€{eur(b_lungo)} ({int(pct_l*100)}%) | "
                f"🟡 Breve ~€{eur(b_breve)} ({int(pct_b*100)}%)"
            )
        except Exception:
            out.append(f"💶 Riserva speculativa: €{eur(r)} (tutta spendibile)")

    # Posizioni lungo termine (in_posizione, bucket lungo o non specificato)
    try:
        wl = json.load(open(os.path.join(REPO, "data", "watchlist.json")))
        long_pos = [t for t in wl.get("tesi", [])
                    if t.get("status") == "in_posizione" and t.get("bucket", "lungo") == "lungo"]
        short_pos = [t for t in wl.get("tesi", [])
                     if t.get("status") == "in_posizione" and t.get("bucket") == "breve"]
    except Exception:
        long_pos, short_pos = [], []

    # Posizioni aperte (mostra dal portfolio.json per i valori aggiornati)
    shown_ids = set()
    for pid, nome in [("leonardo", "Leonardo"), ("cameco", "Cameco"), ("bbio", "BridgeBio")]:
        x = pos.get(pid)
        if not x:
            continue
        v, c = x.get("valore_eur", 0), x.get("versato_eur", 0)
        pl = (v - c) / c * 100 if c else 0
        seg = "+" if pl >= 0 else ""
        pls = f"{pl:.1f}".replace(".", ",")
        out.append(f"• {nome}: €{eur(v)} a mercato ({seg}{pls}%)")
        shown_ids.add(pid)

    # Posizioni breve termine aperte (da watchlist)
    if short_pos:
        out += ["", "🟡 Breve termine:"]
        for t in short_pos:
            exit_d = t.get("exit_hard_date", "?")
            out.append(f"• {t['asset']} ({t['ticker']}) — max uscita {exit_d}")

    try:
        cal = json.load(open(os.path.join(REPO, "data", "calendar.json")))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        up = [e for e in cal.get("eventi", []) if str(e.get("data", "")) >= today][:3]
        if up:
            out += ["", "📅 Prossimi eventi:"]
            for e in up:
                tick = f" ({e['ticker']})" if e.get("ticker") else ""
                out.append(f"• {e['data']} — {e['asset']}{tick}")
    except Exception as e:
        print(f"[tg-bridge] calendario non letto: {e}")
    return "\n".join(out)


def build_cruscotto():
    """Cruscotto ricco via market-watch/cruscotto.py; ripiega sulla foto rapida."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cruscotto", os.path.join(HERE, "cruscotto.py"))
        cru = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cru)
        return cru.build_dashboard()
    except Exception as e:
        print(f"[tg-bridge] cruscotto non disponibile ({e}): uso la foto rapida.")
        return portfolio_snapshot()


HELP = (
    "🤖 Comandi disponibili:\n"
    "• stato — foto rapida del portafoglio (riserva 🟢lungo/🟡breve, posizioni, eventi)\n"
    "• cruscotto — foto completa (patrimonio, P&L, ETF vs target, fondo emergenza)\n"
    "• aggiornami — la mia lettura ragionata del giro (arriva in ~1 min)\n"
    "• ping — verifica che il ponte sia attivo\n"
    "• aiuto — questo elenco\n"
    "\nSegnali automatici: 🟢 ingresso lungo (12-24 mesi) · 🟡 ingresso breve (max 8 sett.) "
    "· 🔵 presa profitto · 🟠 brief pre-evento · 🔴 rischio posizione aperta.\n"
    "Quando scatta un trigger arriva una SCHEDA-ORDINE coi bottoni "
    "Comprato ✅ / Salta ❌ / Rimanda ⏰: esegui l'ordine su TR e conferma con un tap."
)


def handle(text, digest_enabled):
    """Ritorna (risposta_da_inviare_o_None, need_digest_bool)."""
    cmd = text.strip().lower().lstrip("/")
    if cmd == "ping":
        return "🏓 pong — il ponte Telegram è attivo.", False
    if cmd in ("aiuto", "help", "start"):
        return HELP, False
    if cmd in ("stato", "status"):
        return portfolio_snapshot(), False
    if cmd in ("cruscotto", "dashboard"):
        return build_cruscotto(), False
    if cmd in ("aggiornami", "update"):
        if digest_enabled:
            return ("📩 Ricevuto 'aggiornami' — sto preparando la mia lettura "
                    "ragionata, arriva tra ~1 minuto.", True)
        return (portfolio_snapshot() + "\n\n📩 (Il parere ragionato completo non è "
                "ancora attivo: manca il token nei secret GitHub. Intanto sopra la "
                "foto rapida.)", False)
    return ("Non ho capito 🤔. Scrivi 'aiuto' per l'elenco dei comandi.", False)


def record_request(update_id, text):
    line = (f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}Z "
            f"upd={update_id} — 'aggiornami' via Telegram (servito in linea dal workflow)\n")
    with open(REQUESTS, "a") as f:
        f.write(line)


# --- Schede-ordine "a un tap" -------------------------------------------------
# Quando scatta un trigger, invece di un messaggio da leggere mando una scheda
# con i bottoni Comprato/Salta/Rimanda: Luca esegue l'ordine a mano su TR (io NON
# tocco il conto) e con un tap conferma. "Comprato" registra l'operazione in
# trades.md; i numeri esatti (quantita'/prezzo/fee) li riconcilia poi il sync TR.

def load_tickets():
    try:
        with open(TICKETS) as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.setdefault("tickets", [])
    return data


def save_tickets(data):
    with open(TICKETS, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def eur1(x):
    """84.5 -> '84,50' (formato italiano)."""
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def ticket_text(t):
    buy = t.get("lato", "buy") == "buy"
    head = "🟢 SCHEDA ORDINE — ACQUISTO" if buy else "🔴 SCHEDA ORDINE — VENDITA"
    out = [head, "", f"• {t['titolo']} ({t.get('ticker', '?')})"]
    if t.get("isin"):
        out.append(f"• ISIN (cerca su TR): {t['isin']}")
    if t.get("importo_eur") is not None:
        out.append(f"• Importo: €{eur1(t['importo_eur'])} dalla riserva")
    if t.get("quantita") is not None:
        out.append(f"• Quantità: {t['quantita']}")
    if t.get("prezzo_limite_eur") is not None:
        out.append(f"• Prezzo limite: €{eur1(t['prezzo_limite_eur'])}")
    if t.get("netto_atteso_eur") is not None:
        out.append(f"• Netto atteso (post 26%): €{eur1(t['netto_atteso_eur'])}")
    if t.get("nota"):
        out += ["", t["nota"]]
    out += ["", "Esegui l'ordine su TR, poi conferma qui sotto 👇"]
    return "\n".join(out)


def ticket_keyboard(t):
    ok = "Comprato ✅" if t.get("lato", "buy") == "buy" else "Venduto ✅"
    tid = t["id"]
    return json.dumps({"inline_keyboard": [[
        {"text": ok, "callback_data": f"tk:{tid}:ok"},
        {"text": "Salta ❌", "callback_data": f"tk:{tid}:skip"},
        {"text": "Rimanda ⏰", "callback_data": f"tk:{tid}:snooze"},
    ]]})


def send_pending_tickets():
    """Invia le schede 'pending' e quelle 'rimandato' ormai dovute."""
    data = load_tickets()
    now = datetime.now(timezone.utc)
    changed = False
    for t in data["tickets"]:
        st = t.get("status", "pending")
        if st == "rimandato":
            rf = t.get("rimanda_fino")
            due = (not rf) or now.isoformat() >= rf
        elif st == "pending":
            due = True
        else:
            due = False
        if not due:
            continue
        try:
            resp = api("sendMessage", {
                "chat_id": CHAT,
                "text": ticket_text(t),
                "disable_web_page_preview": "true",
                "reply_markup": ticket_keyboard(t),
            })
            t["message_id"] = (resp.get("result") or {}).get("message_id")
            t["status"] = "sent"
            t.setdefault("history", []).append(
                f"{now.isoformat(timespec='seconds')} inviata")
            changed = True
            print(f"[tg-bridge] scheda inviata: {t['id']} (msg {t['message_id']})")
        except Exception as e:
            print(f"[tg-bridge] invio scheda {t.get('id')} fallito: {e}")
    if changed:
        save_tickets(data)


def record_trade(t):
    """Registra in trades.md l'operazione confermata (numeri esatti dal sync TR)."""
    lato = "COMPRATO" if t.get("lato", "buy") == "buy" else "VENDUTO"
    isin = f", {t['isin']}" if t.get("isin") else ""
    imp = f" ~€{eur1(t['importo_eur'])}" if t.get("importo_eur") is not None else ""
    lim = f" @ limite €{eur1(t['prezzo_limite_eur'])}" if t.get("prezzo_limite_eur") is not None else ""
    tesi = f" [tesi {t['tesi_id']}]" if t.get("tesi_id") else ""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = (f"- {ts}Z — {lato} {t['titolo']} ({t.get('ticker', '?')}{isin}){imp}{lim}"
            f"{tesi} — confermato via Telegram, DA RICONCILIARE con sync TR\n")
    with open(TRADES, "a") as f:
        f.write(line)


def handle_callback(cbq):
    """Gestisce il tap su un bottone della scheda-ordine."""
    data = cbq.get("data") or ""
    msg = cbq.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    cbid = cbq.get("id")
    if chat_id != CHAT:
        print(f"[tg-bridge] callback da chat non autorizzata {chat_id}: ignorato.")
        return
    if not data.startswith("tk:") or data.count(":") < 2:
        api("answerCallbackQuery", {"callback_query_id": cbid})
        return
    _, tid, action = data.split(":", 2)
    store = load_tickets()
    t = next((x for x in store["tickets"] if x["id"] == tid), None)
    if not t:
        api("answerCallbackQuery", {"callback_query_id": cbid, "text": "Scheda non più disponibile."})
        return
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%d/%m %H:%M")
    if action == "ok":
        t["status"] = "comprato" if t.get("lato", "buy") == "buy" else "venduto"
        record_trade(t)
        toast = "Registrato ✅ — riconcilio i numeri col sync TR."
        newtext = ticket_text(t) + f"\n\n✅ CONFERMATO il {stamp}Z — registrato in trades.md."
    elif action == "skip":
        t["status"] = "saltato"
        toast = "Scheda saltata."
        newtext = ticket_text(t) + f"\n\n❌ SALTATA il {stamp}Z."
    elif action == "snooze":
        t["status"] = "rimandato"
        t["rimanda_fino"] = (now + timedelta(hours=20)).isoformat(timespec="seconds")
        toast = "Rimandata — te la ripropongo."
        newtext = ticket_text(t) + f"\n\n⏰ RIMANDATA il {stamp}Z (te la ripropongo più tardi)."
    else:
        api("answerCallbackQuery", {"callback_query_id": cbid})
        return
    t.setdefault("history", []).append(f"{now.isoformat(timespec='seconds')} {action}")
    save_tickets(store)
    try:
        if t.get("message_id"):
            api("editMessageText", {
                "chat_id": CHAT, "message_id": t["message_id"],
                "text": newtext, "disable_web_page_preview": "true",
            })
    except Exception as e:
        print(f"[tg-bridge] edit messaggio fallito: {e}")
    api("answerCallbackQuery", {"callback_query_id": cbid, "text": toast})


def enqueue_from_file(path):
    """Mette in coda una o più schede-ordine da un file JSON (oggetto o lista)."""
    incoming = json.load(open(path))
    if isinstance(incoming, dict) and "tickets" in incoming:
        items = incoming["tickets"]
    elif isinstance(incoming, list):
        items = incoming
    else:
        items = [incoming]
    store = load_tickets()
    have = {x["id"] for x in store["tickets"] if "id" in x}
    added = 0
    for it in items:
        it.setdefault("created", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        it.setdefault("status", "pending")
        it.setdefault("history", [])
        it.setdefault("message_id", None)
        if "id" not in it:
            it["id"] = f"{datetime.now(timezone.utc).strftime('%m%d')}{str(it.get('ticker', 'x')).lower()}"
        if it["id"] in have:
            print(f"[tg-bridge] scheda {it['id']} già in coda: salto.")
            continue
        store["tickets"].append(it)
        have.add(it["id"])
        added += 1
    save_tickets(store)
    print(f"[tg-bridge] {added} scheda/e aggiunta/e alla coda.")


def list_tickets():
    store = load_tickets()
    if not store["tickets"]:
        print("Nessuna scheda-ordine in coda.")
        return
    for t in store["tickets"]:
        print(f"  {t['id']:14} {t.get('status', '?'):10} {t.get('lato', 'buy'):4} "
              f"{t['titolo']} ({t.get('ticker', '?')})")


def selftest():
    """Test offline del flusso schede-ordine (nessun Telegram, file temporanei)."""
    import tempfile
    global TICKETS, TRADES, CHAT, api
    tmp = tempfile.mkdtemp()
    TICKETS = os.path.join(tmp, "tickets.json")
    TRADES = os.path.join(tmp, "trades.md")
    CHAT = "42"
    calls = []

    def fake_api(method, params=None, timeout=30):
        calls.append((method, params or {}))
        if method == "sendMessage":
            return {"result": {"message_id": 999}}
        return {"result": True}
    api = fake_api

    save_tickets({"tickets": [{
        "id": "t1", "lato": "buy", "titolo": "Cameco", "ticker": "CCJ",
        "isin": "CA13321L1085", "importo_eur": 180, "prezzo_limite_eur": 84.5,
        "tesi_id": "cameco-ccj", "nota": "2ª tranche su uranio confermato >$90",
        "status": "pending", "history": [], "message_id": None}]})

    send_pending_tickets()
    t = load_tickets()["tickets"][0]
    assert t["status"] == "sent" and t["message_id"] == 999, t
    assert any(m == "sendMessage" and "reply_markup" in p for m, p in calls), "manca la tastiera inline"

    # tap "Comprato"
    handle_callback({"id": "cb1", "data": "tk:t1:ok",
                     "message": {"chat": {"id": "42"}, "message_id": 999}})
    t = load_tickets()["tickets"][0]
    assert t["status"] == "comprato", t
    trades = open(TRADES).read()
    assert "COMPRATO Cameco" in trades and "DA RICONCILIARE" in trades, trades
    assert any(m == "editMessageText" for m, _ in calls), "manca l'edit del messaggio"
    assert any(m == "answerCallbackQuery" for m, _ in calls), "manca la risposta al tap"

    # una scheda già confermata NON si reinvia
    n_send = sum(1 for m, _ in calls if m == "sendMessage")
    send_pending_tickets()
    assert sum(1 for m, _ in calls if m == "sendMessage") == n_send, "non deve reinviare"

    # tap su chat non autorizzata: ignorato
    before = load_tickets()["tickets"][0]["status"]
    handle_callback({"id": "cb2", "data": "tk:t1:skip",
                     "message": {"chat": {"id": "999"}, "message_id": 999}})
    assert load_tickets()["tickets"][0]["status"] == before, "chat non autorizzata non deve modificare"

    # process_update (percorso webhook): un comando testo viene gestito e risposto
    import time as _t
    calls.clear()
    need = process_update({"update_id": 7, "message": {"text": "ping",
                           "chat": {"id": "42"}, "date": int(_t.time())}}, False)
    assert need is False and any(m == "sendMessage" for m, _ in calls), "process_update non risponde a ping"
    calls.clear()
    process_update({"update_id": 8, "message": {"text": "ping",
                    "chat": {"id": "999"}, "date": int(_t.time())}}, False)
    assert not any(m == "sendMessage" for m, _ in calls), "chat non autorizzata non deve ricevere risposta"
    # process_update instrada anche i tap (callback_query)
    calls.clear()
    save_tickets({"tickets": [{"id": "t2", "lato": "buy", "titolo": "X", "ticker": "X",
                               "status": "sent", "history": [], "message_id": 5}]})
    process_update({"update_id": 9, "callback_query": {"id": "cb9", "data": "tk:t2:skip",
                    "message": {"chat": {"id": "42"}, "message_id": 5}}}, False)
    assert load_tickets()["tickets"][0]["status"] == "saltato", "process_update non instrada il tap"

    print("selftest tg-bridge OK: schede+bottoni, conferma, dedup, auth, process_update (cmd+tap).")


def process_update(u, digest_enabled):
    """Processa UN update Telegram (message o callback_query).

    Usato sia dal polling (poll) sia dal webhook (handle-update via
    repository_dispatch). Ritorna True se serve generare l'aggiornami AI.
    """
    if "callback_query" in u:
        try:
            handle_callback(u["callback_query"])
        except Exception as e:
            print(f"[tg-bridge] errore callback: {e}")
        return False
    msg = u.get("message") or {}
    text = msg.get("text")
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    date = msg.get("date", 0)
    if not text:
        return False
    if chat_id != CHAT:
        print(f"[tg-bridge] chat non autorizzata {chat_id}: ignorata.")
        return False
    if date and time.time() - date > FRESH_SECONDS:
        print(f"[tg-bridge] messaggio vecchio ({int(time.time() - date)}s): ignorato.")
        return False
    print(f"[tg-bridge] comando: {text!r}")
    try:
        reply, is_req = handle(text, digest_enabled)
        if reply:
            send(reply)
        if is_req:
            record_request(u.get("update_id", 0), text)
            return True
    except Exception as e:
        print(f"[tg-bridge] errore su {text!r}: {e}")
        send("⚠️ Errore interno elaborando il comando. Riprova tra poco.")
    return False


def handle_update_file(path):
    """Webhook: processa il singolo update ricevuto (JSON) da repository_dispatch."""
    digest_enabled = os.environ.get("DIGEST_ENABLED") == "1"
    try:
        u = json.load(open(path))
    except Exception as e:
        print(f"[tg-bridge] update non leggibile ({e}).")
        gha_output("need_digest", "false")
        return
    need = process_update(u, digest_enabled)
    gha_output("need_digest", "true" if need else "false")


def poll():
    if not TOKEN or not CHAT:
        print("[tg-bridge] secrets mancanti (TELEGRAM_BOT_TOKEN/CHAT_ID): esco.")
        return
    digest_enabled = os.environ.get("DIGEST_ENABLED") == "1"
    # Se è attivo un WEBHOOK, il poll si fa da parte: NON fa deleteWebhook né
    # getUpdates (li romperebbe). Così cron di fallback e webhook non litigano.
    try:
        info = api("getWebhookInfo", {}).get("result", {})
        if info.get("url"):
            print(f"[tg-bridge] webhook attivo ({info['url']}): il poll si fa da parte.")
            gha_output("need_digest", "false")
            return
    except Exception as e:
        print(f"[tg-bridge] getWebhookInfo: {e}")
    try:
        api("deleteWebhook", {"drop_pending_updates": "false"})
    except Exception as e:
        print(f"[tg-bridge] deleteWebhook: {e}")

    state = load_state()
    offset = int(state.get("offset", 0))
    need_digest = False
    try:
        resp = api("getUpdates", {"offset": offset, "timeout": 0,
                                  "allowed_updates": '["message","callback_query"]'})
        updates = resp.get("result", [])
    except Exception as e:
        print(f"[tg-bridge] getUpdates fallito: {e}")
        updates = []

    if updates:
        max_id = offset - 1
        for u in updates:
            max_id = max(max_id, u["update_id"])
            if process_update(u, digest_enabled):
                need_digest = True
        if max_id + 1 != offset:
            state["offset"] = max_id + 1
            save_state(state)
    else:
        print("[tg-bridge] nessun update.")

    # Sempre, anche senza update: manda le schede-ordine in attesa.
    try:
        send_pending_tickets()
    except Exception as e:
        print(f"[tg-bridge] invio schede fallito: {e}")

    gha_output("need_digest", "true" if need_digest else "false")


def send_file(path):
    if not TOKEN or not CHAT:
        print("[tg-bridge] secrets mancanti: non invio.")
        return
    try:
        with open(path) as f:
            body = f.read()
    except Exception as e:
        body = f"⚠️ Non sono riuscito a leggere il digest generato ({e})."
    send(body)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "poll"
    if action == "send":
        send_file(sys.argv[2])
    elif action == "ticket":          # mette in coda una scheda-ordine da file JSON
        enqueue_from_file(sys.argv[2])
    elif action == "tickets":         # elenca le schede in coda
        list_tickets()
    elif action == "handle-update":   # webhook: processa un singolo update (JSON)
        handle_update_file(sys.argv[2])
    elif action == "send-tickets":    # invia le schede-ordine in coda (workflow su push)
        send_pending_tickets()
    elif action == "selftest":        # test offline del flusso schede
        selftest()
    else:
        poll()
