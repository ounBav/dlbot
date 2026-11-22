// Runtime config. On Render this file is regenerated at build time from API_BASE_URL
// (see render.yaml, service bot-miniapp). Locally, point it at your web service / ngrok URL.
// There is deliberately no ?api= URL override: a crafted link could otherwise send the user's
// signed initData to a third-party server, which could replay it against the real API.
// botUsername (optional) powers the "Open in Telegram" button shown outside Telegram.
window.MINIAPP_CONFIG = {
  apiBase: "http://localhost:8000",
  botUsername: "",
};
