#!/usr/bin/env python3
"""Imposta / verifica / rimuove il webhook Telegram (da lanciare in LOCALE).

Con il webhook attivo, Telegram chiama il Worker Cloudflare a ogni update -> niente
più polling. Rimuovendolo si torna al polling (telegram-command.yml, da riattivare).

Creds: env TELEGRAM_BOT_TOKEN oppure market-watch/.secrets/telegram.json.

Uso:
  python3 scripts/set-telegram-webhook.py set <URL_WORKER> <SECRET>
      # registra il webhook sul Worker, con secret_token e updates message+callback
  python3 scripts/set-telegram-webhook.py info      # stato attuale del webhook
  python3 scripts/set-telegram-webhook.py delete    # rimuove il webhook (torna al polling)
"""
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SECRET_FILE = os.path.join(REPO, "market-watch", ".secrets", "telegram.json")


def token():
    t = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if t:
        return t
    try:
        return json.load(open(SECRET_FILE))["bot_token"]
    except Exception:
        raise SystemExit("Token mancante: esporta TELEGRAM_BOT_TOKEN o crea "
                         "market-watch/.secrets/telegram.json")


def call(method, params):
    api = f"https://api.telegram.org/bot{token()}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{api}/{method}", data=data), timeout=20) as r:
        return json.load(r)


def cmd_set(url, secret):
    r = call("setWebhook", {
        "url": url,
        "secret_token": secret,
        "allowed_updates": '["message","callback_query"]',
        "drop_pending_updates": "false",
    })
    print(json.dumps(r, ensure_ascii=False))
    print("OK: webhook impostato." if r.get("ok") else "ERRORE nell'impostare il webhook.")


def cmd_info():
    r = call("getWebhookInfo", {})
    res = r.get("result", {})
    print(f"url: {res.get('url')!r}")
    print(f"pending_update_count: {res.get('pending_update_count')}")
    print(f"last_error_message: {res.get('last_error_message')}")
    print(f"allowed_updates: {res.get('allowed_updates')}")


def cmd_delete():
    r = call("deleteWebhook", {"drop_pending_updates": "false"})
    print(json.dumps(r, ensure_ascii=False))
    print("OK: webhook rimosso (torni al polling)." if r.get("ok") else "ERRORE.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(2)
    if args[0] == "set" and len(args) == 3:
        cmd_set(args[1], args[2])
    elif args[0] == "info":
        cmd_info()
    elif args[0] == "delete":
        cmd_delete()
    else:
        print(__doc__); sys.exit(2)
