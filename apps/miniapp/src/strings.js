// All Mini App copy lives here (same rule as apps/bot/app/messages.py). Keys are stable for i18n.
// Tone and error meanings mirror the bot's messages.py so chat and app say the same thing.

export const S = {
  appTitle: "Media Downloader",
  greeting: (name) => (name ? `Hi, ${name}` : "Hi there"),
  usage: (used, limit) => `${used}/${limit} today`,
  usageFull: "Daily limit reached",

  linkLabel: "Video link",
  linkPlaceholder: "Paste a TikTok, YouTube or Facebook link",
  paste: "Paste",
  clear: "Clear",
  detected: (label) => `${label} link detected`,
  notSupported: "This site isn't supported",
  noLink: "That doesn't look like a link",
  formatLabel: "Format",
  video: "Video",
  audio: "Audio",
  videoHint: "MP4, plays inline",
  audioHint: "MP3, 128 kbps",
  limitHint: (mb) => `Files up to ${mb} MB. The file is sent to your chat with the bot.`,
  submit: (fmt) => (fmt === "audio" ? "Download audio" : "Download video"),
  submitting: "Sending…",
  waitFor: (t) => `Try again in ${t}`,

  activeTitle: "In progress",
  steps: { queued: "Queued", downloading: "Downloading", uploading: "Sending" },
  status: {
    queued: "Waiting in the queue…",
    downloading: "Downloading…",
    retrying: "A temporary problem occurred. Retrying…",
    uploading: "Almost done, sending your file…",
    cancelling: "Cancelling…",
    done: "Sent to your chat",
    failed: "Failed",
    cancelled: "Cancelled",
    expired: "Timed out",
  },
  cancel: "Cancel",
  cancelConfirm: "Cancel this download?",
  openChat: "Open chat",
  tryAudio: "Try audio instead",
  retry: "Try again",
  dismiss: "Dismiss",

  historyTitle: "Recent downloads",
  historyEmpty: "Your downloads will appear here.",
  refresh: "Refresh",
  reuse: "Use this link again",

  cachedSent: "Sent instantly. Check your chat.",
  queuedToast: "Added to the queue. Your file will arrive in the chat.",
  copiedHint: "Long-press the field and choose Paste.",

  terms: "Terms of use",
  footer: "Download only content you have the right to use.",

  // Full-screen states
  outsideTitle: "Open this in Telegram",
  outsideBody: "This app runs inside Telegram. Open the bot and tap the Download button.",
  authTitle: "Session expired",
  authBody: "Close the app and open it again from the bot.",
  bannedTitle: "Access restricted",
  bannedBody: "Your access to this bot has been restricted. Contact the administrator.",
  loading: "Loading…",
  close: "Close",

  errors: {
    "E-UNSUPPORTED": "I can't read that link. I support public TikTok, YouTube and Facebook videos.",
    "E-PRIVATE": "This video is private or needs a login, so I can't download it.",
    "E-TOOBIG": (mb) => `That file is over ${mb} MB. Try the Audio option instead.`,
    "E-FAILED": "Something went wrong on my side. Please try again later.",
    "E-RATELIMIT": (wait) => `Slow down a little. Try again in ${wait}.`,
    "E-DAILY": "You've reached today's limit. Please try again tomorrow.",
    "E-NOCHAT": "Start a chat with the bot first (tap Start), then try again.",
    "E-NOCHAT-DENIED": "The bot needs permission to message you to deliver the file.",
    "E-FINISHED": "This download has already finished.",
    "E-NOTFOUND": "That download no longer exists.",
    "E-NETWORK": "Can't reach the server. Check your connection and try again.",
    "E-EXPIRED": "This download took too long and was dropped. Please try again.",
  },
};

export function errorText(code, { maxMb = 50, retryAfter = 0, formatWait = String } = {}) {
  if (code === "E-RATELIMIT") {
    return retryAfter > 3600 ? S.errors["E-DAILY"] : S.errors["E-RATELIMIT"](formatWait(retryAfter));
  }
  const entry = S.errors[code] ?? S.errors["E-FAILED"];
  return typeof entry === "function" ? entry(maxMb) : entry;
}
