// Cloudflare Worker — endpoint webhook Telegram (sempre acceso, gratis).
//
// Telegram chiama questo Worker a ogni messaggio/tap; il Worker:
//   1. verifica il secret token (header X-Telegram-Bot-Api-Secret-Token);
//   2. accetta solo la chat autorizzata (TELEGRAM_CHAT_ID);
//   3. per i tap risponde SUBITO (answerCallbackQuery) -> lo spinner si ferma;
//   4. inoltra l'update a GitHub Actions via repository_dispatch
//      (event_type: telegram_update); il workflow telegram-webhook.yml fa il
//      lavoro vero col codice Python (comandi, cruscotto, schede, aggiornami).
//
// Variabili del Worker (Settings -> Variables and Secrets):
//   TELEGRAM_BOT_TOKEN  (secret)  token del bot
//   GH_PAT              (secret)  GitHub fine-grained PAT sul repo: Contents R/W
//   WEBHOOK_SECRET      (secret)  stringa a caso, la stessa passata a setWebhook
//   GH_OWNER            (var)     es. LucaDev990
//   GH_REPO             (var)     es. finance
//   TELEGRAM_CHAT_ID    (var)     il tuo chat id autorizzato

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok"); // health check

    if (env.WEBHOOK_SECRET &&
        request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try { update = await request.json(); } catch { return new Response("bad", { status: 400 }); }

    const chatId = String(
      update?.message?.chat?.id ?? update?.callback_query?.message?.chat?.id ?? "");
    if (env.TELEGRAM_CHAT_ID && chatId && chatId !== String(env.TELEGRAM_CHAT_ID)) {
      return new Response("ignored"); // chat non autorizzata: si scarta
    }

    // Tap su un bottone: ferma subito lo spinner con un ack.
    if (update.callback_query) {
      await fetch(
        `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            callback_query_id: update.callback_query.id,
            text: "Ricevuto ✓ aggiorno tra poco…",
          }),
        }
      ).catch((e) => console.log("answerCallbackQuery err", e));
    }

    // Inoltra a GitHub Actions (il lavoro vero lo fa il Python nel workflow).
    const resp = await fetch(
      `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_PAT}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "tg-webhook-worker",
          "content-type": "application/json",
        },
        body: JSON.stringify({ event_type: "telegram_update", client_payload: { update } }),
      }
    );
    if (!resp.ok) console.log("dispatch failed", resp.status, await resp.text());

    // Rispondi sempre 200 a Telegram (altrimenti ritenta e duplica).
    return new Response("ok");
  },
};
