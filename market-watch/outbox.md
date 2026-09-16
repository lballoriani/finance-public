# Outbox Telegram

Le routine cloud non possono chiamare `api.telegram.org` direttamente (rete dell'ambiente bloccata),
ma possono fare push su GitHub. Ogni riga `- ...` aggiunta qui in un push su `main` viene inviata
su Telegram dal workflow `telegram-notify.yml`.

**Formato riga:** `- YYYY-MM-DD HH:MM — 🟢/🟡/🟠/🔴 messaggio`

<!-- Le notifiche vengono aggiunte qui dagli agents. -->
