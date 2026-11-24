// All Mini App copy lives here (same rule as apps/bot/app/messages.py). Keys are stable for i18n.
// Tone and error meanings mirror the bot's messages.py so chat and app say the same thing.
// DRAFT (2026-11-22): keys marked "draft" were added for the preview/save-to-device flow and still
// need product sign-off. Existing wording is unchanged.

export const S = {
  appTitle: "Media Downloader",
  greeting: (name) => (name ? `Hi, ${name}` : "Hi there"),
  usage: (used, limit) => `${used}/${limit} today`,
  usageFull: "Daily limit reached",
  tagline: "Save videos & audio from YouTube, TikTok and Facebook", // draft

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
  sizeHint: (mb) => `Files up to ${mb} MB.`, // draft
  continue: "Continue", // draft
  supported: "Works with", // draft
  waitFor: (t) => `Try again in ${t}`,

  // Preview (draft)
  qualityLabel: "Quality",
  deliveryLabel: "Deliver to",
  toDevice: "This device",
  toChat: "Bot chat",
  toChatHint: "The file is sent to your chat with the bot.",
  toDeviceHint: (min) => `You get a download link, valid for ${min} min.`,
  downloadVideo: (q) => (q ? `Download MP4 · ${q}p` : "Download MP4"),
  downloadAudio: "Download MP3",
  noVideoTrack: "This link has no video track, so only audio is available.",
  previewLoading: "Reading the link…",

  // Progress
  activeTitle: "In progress",
  steps: { queued: "Queued", downloading: "Downloading", uploading: "Sending" },
  status: {
    queued: "Waiting in the queue…",
    downloading: "Downloading…",
    processing: "Converting…", // draft
    retrying: "A temporary problem occurred. Retrying…",
    uploading: "Almost done, sending your file…",
    cancelling: "Cancelling…",
    done: "Sent to your chat",
    failed: "Failed",
    cancelled: "Cancelled",
    expired: "Timed out",
  },
  downloadingPct: (pct) => `Downloading… ${pct}%`, // draft
  keepOpen: "You can keep using Telegram. Come back here to save the file.", // draft
  cancel: "Cancel",
  cancelConfirm: "Cancel this download?",
  cancelledToast: "Download cancelled.", // draft

  // Ready (draft)
  readyTitle: "Your file is ready",
  sentTitle: "Sent to your chat",
  sentBody: "Open the chat with the bot to watch or save it.",
  cachedSent: "Sent instantly. Check your chat.",
  saveToDevice: "Save to device",
  saveStarted: "Download started. Check your downloads.",
  expiresIn: (min) => `The link expires ${min} min after the file is ready.`,
  another: "Download another",
  openChat: "Open chat",

  // Error screen (draft titles)
  errorTitle: "Couldn't download this",
  tryAudio: "Try audio instead",
  lowerQuality: (q) => `Try ${q}p`,
  retry: "Try again",
  back: "Back",
  dismiss: "Dismiss",

  // TikTok profile tab + batch queue (draft, 2026-11-24: needs product sign-off)
  profileEntry: "Download from a TikTok profile",
  profileEntryHint: "List a profile's videos and send them all to your chat.",
  profileTitle: "TikTok profile",
  profileLede: "Enter a username to list its public videos.",
  usernameLabel: "Username",
  usernamePlaceholder: "@username",
  howMany: "How many",
  limitAll: "All",
  findVideos: "Find videos",
  scanning: "Listing videos…",
  profileCount: (n, truncated) => `${n} video${n === 1 ? "" : "s"}${truncated ? " (most recent)" : ""}`,
  selectAll: "Select all",
  clearAll: "Clear all",
  selected: (n, total) => `${n} of ${total} selected`,
  groupsHint: (size, groups) => `Sent in ${groups} group${groups === 1 ? "" : "s"} of up to ${size}, one group after another.`,
  quotaHint: (left) => `You can send ${left} more today.`,
  sendSelected: (n) => (n ? `Send ${n} to chat` : "Pick videos to send"),
  sendFirst: (n) => `Send the first ${n}`,
  maxPick: (n) => `You can pick up to ${n} videos at a time.`,
  batchTitle: (user) => `Sending from @${user}`,
  batchWave: (wave, waves, size) => `Group ${wave} of ${waves} · ${size} per group`,
  batchCounts: (done, failed, waiting) =>
    [`${done} sent`, failed ? `${failed} failed` : null, waiting ? `${waiting} waiting` : null].filter(Boolean).join(" · "),
  batchKeepOpen: "Videos arrive in your chat one by one. You can close the app: sending continues.",
  batchDone: "All videos are on their way",
  batchDoneBody: (done, total) => `${done} of ${total} sent to your chat.`,
  batchCancelled: "Sending stopped",
  cancelBatch: "Stop sending",
  cancelBatchConfirm: "Stop sending? Videos not yet sent will be skipped.",
  itemStatus: { waiting: "Waiting" },

  historyTitle: "Recent downloads",
  historyEmpty: "Your downloads will appear here.",
  refresh: "Refresh",
  reuse: "Use this link again",

  copiedHint: "Long-press the field and choose Paste.",

  terms: "Terms of use",
  footer: "Download only content you have the right to use.",

  // Full-screen states
  outsideTitle: "Open this in Telegram",
  outsideBody: "This app runs inside Telegram. Open the bot and tap the Download button.",
  openInTelegram: "Open in Telegram", // draft
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
    // draft
    "E-TOOBIG-Q": (mb) => `At this quality the file is over ${mb} MB. Pick a lower quality or Audio.`,
    "E-BUSY": "You already have a download running. Wait for it to finish.",
    "E-LIVE": "Live streams and premieres can't be downloaded. Try again after it ends.",
    "E-PLAYLIST": "That's a playlist. Send a link to a single video.",
    "E-TOOLONG": "This video is too long for the downloader.",
    "E-BLOCKED": "YouTube is blocking our server right now. Please try again later.",
    "E-GEO": "This video isn't available in the server's region.",
    "E-UNAVAILABLE": "This video was removed or isn't available.",
    "E-GONE": "The download link has expired. Download the file again.",
    // TikTok profile tab (draft)
    "E-USERNAME": "That doesn't look like a TikTok username.",
    "E-PROFILE": "Couldn't open that profile. Check the username or try again later.",
    "E-NOVIDEOS": "This profile has no public videos.",
    "E-SCANGONE": "This list has expired. Search again.",
    "E-TOOMANY": "Too many videos picked at once.",
    "E-QUOTA": "That's more than you have left today. Pick fewer videos.",
    "E-BATCHBUSY": "You're already sending videos from a profile. Wait for it to finish.",
  },
};

export function errorText(code, { maxMb = 50, retryAfter = 0, formatWait = String } = {}) {
  if (code === "E-RATELIMIT") {
    return retryAfter > 3600 ? S.errors["E-DAILY"] : S.errors["E-RATELIMIT"](formatWait(retryAfter));
  }
  const entry = S.errors[code] ?? S.errors["E-FAILED"];
  return typeof entry === "function" ? entry(maxMb) : entry;
}
