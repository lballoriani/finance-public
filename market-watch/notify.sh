#!/usr/bin/env bash
# Invia un avviso a Luca su Telegram.
# Uso:  market-watch/notify.sh "testo del messaggio"
# Creds: env TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, oppure market-watch/.secrets/telegram.json
set -euo pipefail
MSG="${1:?uso: notify.sh \"messaggio\"}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRET="$DIR/.secrets/telegram.json"

TOKEN="${TELEGRAM_BOT_TOKEN:-}"; CHAT="${TELEGRAM_CHAT_ID:-}"
if [[ -z "$TOKEN" && -f "$SECRET" ]]; then
  TOKEN="$(python3 -c "import json,sys;print(json.load(open('$SECRET'))['bot_token'])")"
  CHAT="$(python3 -c "import json,sys;print(json.load(open('$SECRET'))['chat_id'])")"
fi
[[ -n "$TOKEN" && -n "$CHAT" ]] || { echo "creds Telegram mancanti" >&2; exit 1; }

curl -s "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT}" \
  --data-urlencode "text=${MSG}" \
  --data-urlencode "parse_mode=Markdown" \
  -d "disable_web_page_preview=true" >/dev/null && echo "inviato"
