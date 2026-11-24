// Mini App controller: home -> preview -> progress -> ready | error.
// TikTok profile tab: home -> profile (search, pick, Select all) -> batch (groups of PROFILE_BATCH_SIZE).
// Talks to apps/bot/app/api.py. "This device" delivery keeps the file behind a short-lived link
// (GET /api/files/<token>) that Telegram's downloadFile saves; "Bot chat" delivery sends it to the chat.
// The ?v= query on imports busts Telegram's webview cache; bump it with index.html.

import { createApi } from "./api.js?v=3";
import {
  absoluteUrl, defaultQuality, detectPlatform, extractUrl, formatBytes, formatDuration, formatWait,
  isFinal, jobErrorCode, lowerQuality, percent, relativeTime, retryDelay, screenFor, shortUrl, toProblem,
  batchPercent, cleanUsername, toggleAll, waveCount,
} from "./logic.js?v=3";
import { errorText, S } from "./strings.js?v=3";

const tg = window.Telegram?.WebApp;
const config = window.MINIAPP_CONFIG || {};
const $ = (id) => document.getElementById(id);
const el = {
  state: $("state"), stateIcon: $("state-icon"), stateTitle: $("state-title"),
  stateBody: $("state-body"), stateAction: $("state-action"),
  app: $("app"), greeting: $("greeting"), usage: $("usage"), usageText: $("usage-text"), usageBar: $("usage-bar"),
  form: $("form"), field: $("field"), fieldBadge: $("field-badge"), link: $("link"), paste: $("paste"),
  clear: $("clear"), linkHint: $("link-hint"), platformRow: $("platform-row"),
  historyList: $("history-list"), historyEmpty: $("history-empty"), refresh: $("refresh"), terms: $("terms"),
  media: $("media"), thumb: $("thumb"), thumbImg: $("thumb-img"), thumbPlatform: $("thumb-platform"),
  thumbDuration: $("thumb-duration"), previewTitle: $("preview-title"), previewUploader: $("preview-uploader"),
  previewStatus: $("preview-status"), options: $("options"), fmtGroup: $("fmt-group"), fmtHint: $("fmt-hint"),
  qualityGroup: $("quality-group"), qualityChips: $("quality-chips"), deliveryGroup: $("delivery-group"),
  deliveryHint: $("delivery-hint"),
  miniThumb: $("mini-thumb"), progressTitle: $("progress-title"), progressMeta: $("progress-meta"),
  stage: $("stage"), bar: $("bar"), barFill: $("bar-fill"), cancel: $("cancel"),
  readyTitle: $("ready-title"), readyBody: $("ready-body"), readyNote: $("ready-note"), another: $("another"),
  errorBody: $("error-body"), errorActions: $("error-actions"),
  dock: $("dock"), mainBtn: $("main-btn"), toast: $("toast"),
  profileEntry: $("profile-entry"), profileEntryMark: $("profile-entry-mark"),
  profileForm: $("profile-form"), username: $("username"), limitChips: $("limit-chips"),
  profileHint: $("profile-hint"), profileResults: $("profile-results"), resultsTitle: $("results-title"),
  selectAll: $("select-all"), pickHint: $("pick-hint"), videoList: $("video-list"),
  batchTitle: $("batch-title"), batchStage: $("batch-stage"), batchBar: $("batch-bar"),
  batchBarFill: $("batch-bar-fill"), batchCounts: $("batch-counts"), batchNote: $("batch-note"),
  batchList: $("batch-list"), batchCancel: $("batch-cancel"),
};
const screens = {
  home: $("screen-home"), preview: $("screen-preview"), progress: $("screen-progress"),
  ready: $("screen-ready"), error: $("screen-error"),
  profile: $("screen-profile"), batch: $("screen-batch"),
};

const POLL_MS = 1500;
const BATCH_POLL_MS = 2500;
const SESSION_KEY = "dl.active";

const state = {
  me: null,
  history: [],
  screen: "home",
  link: "",            // URL being previewed / downloaded
  preview: null,       // POST /api/info result
  previewSeq: 0,       // ignores a slow /info answer after the user went back
  fmt: "video",
  quality: null,
  delivery: "device",
  jobId: null,
  job: null,           // last GET /api/downloads/{id}
  failures: 0,
  pollTimer: 0,
  busy: false,
  cooldownUntil: 0,
  cooldownTimer: 0,
  // TikTok profile tab
  limit: 10,             // 10 | 50 | 100 | "all"
  scan: null,            // POST /api/tiktok/scan result (one-time listing)
  scannedFor: "",        // "username|limit" the listing belongs to
  picked: new Set(),     // selected video ids
  scanSeq: 0,
  batchId: null,
  batch: null,           // GET /api/tiktok/batches/{id}
  batchTimer: 0,
  batchFailures: 0,
};

let api;

// ---------------------------------------------------------------- helpers

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function svg(path) {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", path);
  p.setAttribute("fill-rule", "evenodd");
  s.append(p);
  return s;
}

const ICON = {
  video: "M4 5h11a2 2 0 0 1 2 2v2.5l4-2.5v10l-4-2.5V17a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z",
  audio: "M9 4v10.6A3.5 3.5 0 1 0 11 18V8h7V4z",
  telegram: "M21.5 4.3 2.9 11.5c-1.3.5-1.3 1.2-.2 1.5l4.8 1.5 1.8 5.6c.2.6.4.8.9.8.4 0 .6-.2.9-.4l2.3-2.2 4.7 3.5c.9.5 1.5.2 1.7-.8l3.1-14.6c.3-1.3-.5-1.8-1.4-1.6zM9.6 14.6l-.4 4-1.4-4.6 10.4-6.6z",
  lock: "M17 9V7A5 5 0 0 0 7 7v2a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2zM9 7a3 3 0 0 1 6 0v2H9z",
  clock: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 10.4 3.5 2-1 1.7L11 13V6h2z",
};

// Simple platform glyphs for badges (generic shapes in each platform's accent color).
const PLATFORM_ICON = {
  youtube: "M21.6 7.2a2.5 2.5 0 0 0-1.8-1.8C18.2 5 12 5 12 5s-6.2 0-7.8.4A2.5 2.5 0 0 0 2.4 7.2 26 26 0 0 0 2 12a26 26 0 0 0 .4 4.8 2.5 2.5 0 0 0 1.8 1.8C5.8 19 12 19 12 19s6.2 0 7.8-.4a2.5 2.5 0 0 0 1.8-1.8A26 26 0 0 0 22 12a26 26 0 0 0-.4-4.8zM10 15V9l5.2 3z",
  tiktok: "M16.6 5.8A4.3 4.3 0 0 1 15.5 3h-3.1v12.4a2.6 2.6 0 1 1-1.8-2.5V9.7a5.7 5.7 0 1 0 4.9 5.7V9.1a7.3 7.3 0 0 0 4.3 1.4V7.4a4.3 4.3 0 0 1-3.2-1.6z",
  facebook: "M14 8.5V6.8c0-.8.2-1.3 1.4-1.3H17V2.6a21 21 0 0 0-2.4-.1C12.2 2.5 10.6 4 10.6 6.6v1.9H8V12h2.6v9.5H14V12h2.7l.4-3.5z",
};

function platformMark(key, small = false) {
  return h("span", { class: `pf ${key}${small ? " sm" : ""}`, title: platformLabel(key) },
    svg(PLATFORM_ICON[key] || ICON.video));
}

function haptic(kind) {
  try {
    if (kind === "select") tg?.HapticFeedback?.selectionChanged();
    else tg?.HapticFeedback?.notificationOccurred(kind);
  } catch { /* old client */ }
}

function supports(version) {
  try { return Boolean(tg?.isVersionAtLeast?.(version)); } catch { return false; }
}

let toastTimer = 0;
function toast(text) {
  clearTimeout(toastTimer);
  el.toast.textContent = text;
  el.toast.hidden = false;
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, 3500);
}

function applyStrings(root = document) {
  root.querySelectorAll("[data-s]").forEach((n) => {
    const v = S[n.dataset.s];
    if (typeof v === "string") n.textContent = v;
  });
  root.querySelectorAll("[data-s-placeholder]").forEach((n) => { n.placeholder = S[n.dataset.sPlaceholder]; });
  root.querySelectorAll("[data-s-aria]").forEach((n) => { n.setAttribute("aria-label", S[n.dataset.sAria]); });
}

function platformLabel(key) {
  return state.me?.platforms?.find((p) => p.key === key)?.label ?? key;
}

function fmtLabel(fmt, quality) {
  if (fmt === "audio") return "MP3";
  return quality ? `MP4 · ${quality}p` : "MP4";
}

function maxMb() {
  return state.me?.max_upload_mb ?? 50;
}

function ttlMin() {
  return state.me?.file_ttl_min ?? 15;
}

const session = {
  save(value) {
    try {
      if (value) sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
      else sessionStorage.removeItem(SESSION_KEY);
    } catch { /* storage blocked: resume just won't work */ }
  },
  load() {
    try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null"); } catch { return null; }
  },
};

// ---------------------------------------------------------------- Telegram controls
// One MainButton handler at a time: always offClick the previous one before setting a new one.
// Without MainButton (old clients, plain browser) the in-page dock button does the same job.

const useMainButton = Boolean(tg?.initData && tg.MainButton && supports("6.1"));
let mainHandler = null;

function setMain(cfg) {
  if (useMainButton) {
    const mb = tg.MainButton;
    if (mainHandler) { mb.offClick(mainHandler); mainHandler = null; }
    if (!cfg) { mb.hideProgress(); mb.hide(); return; }
    const theme = tg.themeParams || {};
    const active = cfg.active !== false && !cfg.progress;
    const params = { text: cfg.text, is_visible: true, is_active: active };
    const color = active ? theme.button_color : (theme.hint_color || theme.button_color);
    if (color) params.color = color;
    if (theme.button_text_color) params.text_color = theme.button_text_color;
    mb.setParams(params);
    if (cfg.progress) mb.showProgress(false); else mb.hideProgress();
    if (cfg.onClick) {
      mainHandler = () => { if (!cfg.progress) cfg.onClick(); };
      mb.onClick(mainHandler);
    }
    return;
  }
  el.dock.hidden = !cfg;
  if (!cfg) return;
  el.mainBtn.textContent = cfg.text;
  el.mainBtn.disabled = cfg.active === false || Boolean(cfg.progress);
  el.mainBtn.setAttribute("aria-busy", cfg.progress ? "true" : "false");
  el.mainBtn.onclick = cfg.onClick || null;
}

let backHandler = null;
function setBack(fn) {
  if (!tg?.BackButton || !supports("6.1")) return;
  if (backHandler) tg.BackButton.offClick(backHandler);
  backHandler = fn || null;
  if (fn) {
    tg.BackButton.onClick(fn);
    tg.BackButton.show();
  } else {
    tg.BackButton.hide();
  }
}

function closingConfirmation(on) {
  if (!supports("6.2")) return;
  try {
    if (on) tg.enableClosingConfirmation();
    else tg.disableClosingConfirmation();
  } catch { /* ignore */ }
}

function confirm(text) {
  if (supports("6.2") && tg.showConfirm) return new Promise((resolve) => tg.showConfirm(text, resolve));
  return Promise.resolve(window.confirm(text));
}

// ---------------------------------------------------------------- screens

function show(name, { back = false } = {}) {
  for (const [key, node] of Object.entries(screens)) node.hidden = key !== name;
  const node = screens[name];
  node.classList.toggle("back", back);
  node.style.animation = "none";
  void node.offsetWidth; // restart the enter animation
  node.style.animation = "";
  state.screen = name;
  closingConfirmation(name === "progress");
  window.scrollTo(0, 0);
}

function showState({ icon, title, body = "", action = null }) {
  el.app.hidden = true;
  el.state.hidden = false;
  el.stateIcon.replaceChildren(icon ? svg(icon) : h("span", { class: "spinner" }));
  el.stateTitle.textContent = title;
  el.stateBody.textContent = body;
  el.stateAction.hidden = !action;
  if (action) {
    el.stateAction.textContent = action.label;
    el.stateAction.onclick = action.run;
  }
  setMain(null);
  setBack(null);
  closingConfirmation(false);
}

const closeAction = tg?.initData ? { label: S.close, run: () => tg.close() } : null;

function handleFatal(problem) {
  if (problem.code === "E-AUTH") {
    stopPolling();
    showState({ icon: ICON.clock, title: S.authTitle, body: S.authBody, action: closeAction });
    return true;
  }
  if (problem.code === "E-BANNED") {
    stopPolling();
    showState({ icon: ICON.lock, title: S.bannedTitle, body: S.bannedBody, action: closeAction });
    return true;
  }
  return false;
}

// ---------------------------------------------------------------- 1. home

function currentLink() {
  const url = extractUrl(el.link.value);
  return { url, platform: detectPlatform(url, state.me?.platforms) };
}

function cooling() {
  return Date.now() < state.cooldownUntil;
}

function renderHome() {
  const raw = el.link.value.trim();
  const { url, platform } = currentLink();

  el.clear.hidden = !raw;
  el.paste.hidden = Boolean(raw) || !canPaste();
  el.field.classList.toggle("ok", Boolean(platform));
  el.field.classList.toggle("bad", Boolean(raw) && !platform);
  el.fieldBadge.hidden = !platform;
  el.fieldBadge.replaceChildren(platform ? platformMark(platform.key, true) : "");
  el.linkHint.className = "hint" + (platform ? " ok" : raw ? " bad" : "");
  el.linkHint.textContent = platform ? S.detected(platform.label) : raw ? (url ? S.notSupported : S.noLink) : "";

  if (state.screen !== "home") return;
  const wait = cooling();
  setMain({
    text: wait ? S.waitFor(formatWait((state.cooldownUntil - Date.now()) / 1000)) : S.continue,
    active: Boolean(platform) && !wait && Boolean(state.me),
    onClick: () => goPreview(),
  });
}

function goHome({ clear = false } = {}) {
  stopPolling();
  stopBatchPolling();
  state.batchId = null;
  state.batch = null;
  session.save(null);
  state.jobId = null;
  state.job = null;
  state.preview = null;
  state.previewSeq += 1;
  if (clear) el.link.value = "";
  show("home", { back: true });
  setBack(null);
  renderHome();
  refreshHistory();
}

function startCooldown(seconds) {
  if (!(seconds > 0 && seconds <= 3600)) return;
  state.cooldownUntil = Date.now() + seconds * 1000;
  clearInterval(state.cooldownTimer);
  state.cooldownTimer = setInterval(() => {
    if (!cooling()) clearInterval(state.cooldownTimer);
    renderHome();
  }, 1000);
}

function renderHeader() {
  const me = state.me;
  if (!me) return;
  el.greeting.textContent = S.greeting(me.user?.first_name);
  const used = Math.min(me.usage_today, me.limit_per_day);
  const full = me.usage_today >= me.limit_per_day;
  el.usage.hidden = false;
  el.usage.classList.toggle("full", full);
  el.usageText.textContent = full ? S.usageFull : S.usage(me.usage_today, me.limit_per_day);
  el.usageBar.style.width = `${me.limit_per_day ? (used / me.limit_per_day) * 100 : 0}%`;
  el.platformRow.replaceChildren(...(me.platforms || []).map((p) => platformMark(p.key)));

  const terms = me.terms_url;
  const safe = typeof terms === "string" && /^https?:\/\//i.test(terms);
  el.terms.hidden = !safe;
  el.terms.onclick = safe ? () => (tg?.openLink ? tg.openLink(terms) : window.open(terms, "_blank", "noopener")) : null;
}

function historyRow(item) {
  const title = item.title || shortUrl(item.url);
  const badgeText = item.status === "done" ? "✓" : isFinal(item.status) ? S.status[item.status] : "…";
  return h("li", { class: "history-item" },
    h("button", {
      type: "button", class: "history-btn",
      "aria-label": `${title}. ${S.status[item.status] ?? ""}. ${S.reuse}`,
      onclick: () => {
        el.link.value = item.url;
        haptic("select");
        renderHome();
        window.scrollTo({ top: 0, behavior: "smooth" });
      },
    },
      platformMark(item.platform),
      h("span", { class: "history-text" },
        h("span", { class: "history-title" }, title),
        h("span", { class: "history-meta" },
          `${fmtLabel(item.format, item.quality)} · ${relativeTime(item.created_at)}`)),
      h("span", { class: `badge ${item.status}`, title: S.status[item.status] ?? "" }, badgeText)));
}

function renderHistory() {
  const items = state.history.slice(0, 8);
  el.historyList.replaceChildren(...items.map(historyRow));
  el.historyList.hidden = items.length === 0;
  el.historyEmpty.hidden = items.length > 0;
}

async function refreshHistory() {
  try {
    state.history = (await api.list()).items || [];
    renderHistory();
  } catch (err) {
    handleFatal(toProblem(err));
  }
}

async function refreshMe() {
  try {
    state.me = await api.me();
    renderHeader();
    renderHome();
  } catch (err) {
    handleFatal(toProblem(err));
  }
}

// ---------------------------------------------------------------- clipboard

function canPaste() {
  return (Boolean(navigator.clipboard?.readText) && window.isSecureContext)
    || (supports("6.4") && typeof tg?.readTextFromClipboard === "function");
}

function readTelegramClipboard() {
  return new Promise((resolve) => {
    try { tg.readTextFromClipboard((text) => resolve(text || "")); } catch { resolve(""); }
  });
}

async function paste() {
  let text = "";
  try {
    if (navigator.clipboard?.readText && window.isSecureContext) text = await navigator.clipboard.readText();
  } catch { /* permission denied or blocked in this webview */ }
  if (!text && supports("6.4") && tg?.readTextFromClipboard) text = await readTelegramClipboard();
  if (text) {
    el.link.value = (extractUrl(text) || text).trim();
    haptic("select");
    renderHome();
    return;
  }
  el.link.focus();
  toast(S.copiedHint);
}

// ---------------------------------------------------------------- 2. preview

function setRadio(group, attr, value) {
  group.querySelectorAll(`[${attr}]`).forEach((b) => {
    const on = b.getAttribute(attr) === String(value);
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
}

function renderPreviewSkeleton(platformKey) {
  el.media.setAttribute("aria-busy", "true");
  el.thumb.classList.add("loading");
  el.thumbImg.hidden = true;
  el.thumbImg.removeAttribute("src");
  el.thumbDuration.hidden = true;
  el.thumbPlatform.replaceChildren(platformKey ? platformMark(platformKey) : "");
  el.previewTitle.replaceChildren(h("span", { class: "skeleton line" }), h("span", { class: "skeleton line short" }));
  el.previewUploader.replaceChildren(h("span", { class: "skeleton line tiny" }));
  el.previewStatus.textContent = S.previewLoading;
  el.options.hidden = true;
}

function mainLabel() {
  return state.fmt === "audio" ? S.downloadAudio : S.downloadVideo(state.quality);
}

function renderPreview() {
  const info = state.preview;
  if (!info) return;
  el.media.setAttribute("aria-busy", "false");
  el.thumbPlatform.replaceChildren(platformMark(info.platform));
  if (info.thumbnail) {
    el.thumbImg.onload = () => { el.thumb.classList.remove("loading"); el.thumbImg.hidden = false; };
    el.thumbImg.onerror = () => { el.thumb.classList.remove("loading"); el.thumbImg.hidden = true; };
    el.thumbImg.src = info.thumbnail;
  } else {
    el.thumb.classList.remove("loading");
  }
  el.thumbDuration.hidden = !info.duration;
  el.thumbDuration.textContent = formatDuration(info.duration);
  el.previewTitle.textContent = info.title;
  el.previewUploader.textContent = [info.uploader, platformLabel(info.platform)].filter(Boolean).join(" · ");
  el.previewStatus.textContent = info.title;

  const videoOk = info.has_video !== false;
  if (!videoOk) state.fmt = "audio";
  el.fmtGroup.querySelector('[data-fmt="video"]').disabled = !videoOk;
  setRadio(el.fmtGroup, "data-fmt", state.fmt);
  el.fmtHint.textContent = !videoOk ? S.noVideoTrack : state.fmt === "audio" ? S.audioHint : S.videoHint;

  const qualities = info.qualities || [];
  el.qualityGroup.hidden = state.fmt === "audio" || qualities.length === 0;
  el.qualityChips.replaceChildren(...qualities.map((q) =>
    h("button", { type: "button", role: "radio", class: "chip", "data-q": String(q) }, `${q}p`)));
  setRadio(el.qualityChips, "data-q", state.quality);

  setRadio(el.deliveryGroup, "data-delivery", state.delivery);
  el.deliveryHint.textContent = state.delivery === "device"
    ? `${S.toDeviceHint(ttlMin())} ${S.sizeHint(maxMb())}`
    : `${S.toChatHint} ${S.sizeHint(maxMb())}`;
  el.options.hidden = false;

  setMain({ text: mainLabel(), onClick: () => startDownload() });
}

async function goPreview(url = currentLink().url) {
  if (!url || state.busy) return;
  const seq = ++state.previewSeq;
  state.link = url;
  state.preview = null;
  renderPreviewSkeleton(detectPlatform(url, state.me?.platforms)?.key);
  show("preview");
  setBack(() => goHome());
  setMain({ text: S.continue, progress: true });
  el.link.blur();
  try {
    const info = await api.info(url);
    if (seq !== state.previewSeq) return; // user went back meanwhile
    state.preview = info;
    state.link = info.url || url;
    state.fmt = info.has_video === false ? "audio" : "video";
    state.quality = defaultQuality(info.qualities);
    renderPreview();
  } catch (err) {
    if (seq !== state.previewSeq) return;
    const p = toProblem(err);
    if (handleFatal(p)) return;
    if (p.code === "E-RATELIMIT") startCooldown(p.retryAfter);
    const retryable = ["E-FAILED", "E-NETWORK", "E-BLOCKED", "E-RATELIMIT"].includes(p.code);
    showError(p, [
      retryable ? { label: S.retry, run: () => goPreview(url) } : null,
      { label: S.back, run: () => goHome() },
    ]);
  }
}

// ---------------------------------------------------------------- 3. progress

async function startDownload(retried = false) {
  if (state.busy || !state.link) return;
  state.busy = true;
  setMain({ text: mainLabel(), progress: true });
  const opts = { delivery: state.delivery, ...(state.fmt === "video" && state.quality ? { quality: state.quality } : {}) };
  try {
    const res = await api.create(state.link, state.fmt, opts);
    refreshMe();
    if (res.cached) {
      state.job = { status: "done", delivery: "chat", cached: true };
      showReady();
      return;
    }
    beginJob(res.id);
  } catch (err) {
    const p = toProblem(err);
    if (handleFatal(p)) return;
    // Chat delivery needs the bot to be allowed to message the user. Ask once, then retry.
    if (p.code === "E-NOCHAT" && !retried && supports("6.9") && tg.requestWriteAccess) {
      const granted = await new Promise((resolve) => tg.requestWriteAccess(resolve));
      if (granted) {
        state.busy = false;
        return startDownload(true);
      }
      p.code = "E-NOCHAT-DENIED";
    }
    if (p.code === "E-BUSY" && p.id) { // a job is already running: show it instead of an error
      beginJob(p.id);
      return;
    }
    if (p.code === "E-RATELIMIT") { startCooldown(p.retryAfter); refreshMe(); }
    showError(p, errorActions(p.code));
  } finally {
    state.busy = false;
  }
}

function beginJob(id) {
  state.jobId = id;
  state.job = { id, status: "queued", progress: null, format: state.fmt, quality: state.quality, delivery: state.delivery };
  session.save({ id, link: state.link, preview: state.preview, fmt: state.fmt, quality: state.quality, delivery: state.delivery });
  renderProgress();
  show("progress");
  setBack(null);
  setMain(null);
  state.failures = 0;
  pollTick();
}

function stopPolling() {
  clearTimeout(state.pollTimer);
  state.pollTimer = 0;
}

async function pollTick() {
  stopPolling();
  const id = state.jobId;
  if (!id || document.hidden) return; // resumed by visibilitychange
  try {
    const job = await api.get(id);
    if (id !== state.jobId) return;
    state.failures = 0;
    onJob(job);
  } catch (err) {
    if (id !== state.jobId) return;
    const p = toProblem(err);
    if (handleFatal(p)) return;
    if (p.code === "E-NOTFOUND") {
      session.save(null);
      state.jobId = null;
      showError(p, [{ label: S.another, run: () => goHome({ clear: true }) }]);
      return;
    }
    state.failures += 1;
    if (state.failures === 2) toast(errorText("E-NETWORK"));
    state.pollTimer = setTimeout(pollTick, retryDelay(state.failures));
  }
}

function onJob(job, { quiet = false } = {}) {
  const cancelling = state.job?.status === "cancelling" && !isFinal(job.status);
  state.job = cancelling ? { ...job, status: "cancelling" } : job;
  const where = screenFor(job);
  if (where === "progress") {
    renderProgress();
    if (state.screen !== "progress") show("progress");
    state.pollTimer = setTimeout(pollTick, POLL_MS);
    return;
  }
  stopPolling();
  state.jobId = null;
  if (where === "ready") {
    if (job.delivery === "device" && !job.file) {
      showError({ code: "E-GONE" }, errorActions("E-GONE"));
      return;
    }
    showReady({ quiet });
  } else if (where === "error") {
    session.save(null);
    const code = jobErrorCode(job);
    showError({ code }, errorActions(code));
  } else {
    if (!quiet) { haptic("warning"); toast(S.cancelledToast); }
    goHome();
  }
}

function setMiniThumb(src) {
  if (!src) { el.miniThumb.replaceChildren(svg(state.job?.format === "audio" ? ICON.audio : ICON.video)); return; }
  const img = h("img", { alt: "", referrerpolicy: "no-referrer", decoding: "async" });
  img.onerror = () => img.remove();
  img.src = src;
  el.miniThumb.replaceChildren(img);
}

let miniThumbFor = null;
function renderProgress() {
  const job = state.job || {};
  // A resumed "already running" job may not be the link on screen: only use a matching preview.
  const preview = state.preview && (!job.url || job.url === state.preview.url) ? state.preview : null;
  const title = preview?.title || job.title || shortUrl(job.url || state.link);
  el.progressTitle.textContent = title;
  el.progressMeta.textContent = [platformLabel(preview?.platform || job.platform),
    fmtLabel(job.format || state.fmt, job.quality ?? state.quality)].filter(Boolean).join(" · ");
  const thumb = preview?.thumbnail || null;
  if (miniThumbFor !== `${job.id}|${thumb}`) { miniThumbFor = `${job.id}|${thumb}`; setMiniThumb(thumb); }

  const pct = percent(job.progress);
  let text = S.status[job.status] ?? S.status.queued;
  if (job.status === "downloading" && pct != null) text = S.downloadingPct(pct);
  el.stage.textContent = text;

  const determinate = job.status === "downloading" && pct != null && pct > 0;
  el.bar.classList.toggle("indeterminate", !determinate);
  el.barFill.style.width = determinate ? `${pct}%` : "";
  if (determinate) el.bar.setAttribute("aria-valuenow", String(pct));
  else el.bar.removeAttribute("aria-valuenow");

  el.cancel.disabled = job.status === "cancelling" || job.status === "uploading";
}

async function cancelJob() {
  const id = state.jobId;
  if (!id || !(await confirm(S.cancelConfirm))) return;
  state.job = { ...state.job, status: "cancelling" };
  renderProgress();
  try {
    await api.cancel(id);
    haptic("warning");
  } catch (err) {
    const p = toProblem(err);
    if (handleFatal(p)) return;
    if (p.code !== "E-FINISHED") toast(errorText(p.code));
  }
  pollTick();
}

// ---------------------------------------------------------------- 4. ready

function restartCheck() {
  const check = screens.ready.querySelector(".check");
  check.replaceWith(check.cloneNode(true)); // replays the draw animation
}

function showReady({ quiet = false } = {}) {
  const job = state.job;
  restartCheck();
  if (job.delivery === "device" && job.file) {
    el.readyTitle.textContent = S.readyTitle;
    el.readyBody.textContent = `${job.file.name} · ${formatBytes(job.file.size)}`;
    el.readyNote.textContent = S.expiresIn(ttlMin());
    setMain({ text: S.saveToDevice, onClick: saveFile });
  } else {
    session.save(null);
    el.readyTitle.textContent = job.cached ? S.cachedSent : S.sentTitle;
    el.readyBody.textContent = S.sentBody;
    el.readyNote.textContent = "";
    setMain({ text: S.openChat, onClick: () => tg.close() });
  }
  show("ready");
  setBack(() => goHome({ clear: true }));
  if (!quiet) haptic("success");
}

function saveFile() {
  const file = state.job?.file;
  if (!file) return;
  if (file.expires_at && Date.parse(file.expires_at) <= Date.now()) {
    showError({ code: "E-GONE" }, errorActions("E-GONE"));
    return;
  }
  const url = absoluteUrl(config.apiBase, file.download_path, location.origin);
  if (supports("8.0") && typeof tg.downloadFile === "function") {
    try {
      tg.downloadFile({ url, file_name: file.name }, (accepted) => {
        if (accepted) { haptic("success"); toast(S.saveStarted); }
      });
      return;
    } catch { /* fall through to openLink */ }
  }
  if (tg?.openLink) tg.openLink(url);
  else window.open(url, "_blank", "noopener");
}

// ---------------------------------------------------------------- 5. error

function backToPreview() {
  if (!state.preview) { goHome(); return; }
  show("preview", { back: true });
  setBack(() => goHome());
  renderPreview();
}

function errorActions(code) {
  const back = { label: S.back, run: backToPreview };
  const retry = { label: S.retry, run: () => startDownload() };
  if (code === "E-TOOBIG") {
    const lower = state.fmt === "video" ? lowerQuality(state.preview?.qualities, state.quality) : null;
    const audio = state.fmt === "video"
      ? { label: S.tryAudio, run: () => { state.fmt = "audio"; startDownload(); } } : null;
    return [lower ? { label: S.lowerQuality(lower), run: () => { state.quality = lower; startDownload(); } } : null, audio, back];
  }
  if (code === "E-GONE") return [{ label: S.retry, run: () => startDownload() }, { label: S.another, run: () => goHome({ clear: true }) }];
  if (["E-FAILED", "E-NETWORK", "E-EXPIRED", "E-BLOCKED", "E-RATELIMIT"].includes(code)) return [retry, back];
  if (code === "E-NOTFOUND" || code === "E-UNAVAILABLE") return [{ label: S.another, run: () => goHome({ clear: true }) }];
  return [back];
}

function showError(problem, actions, back = null) {
  const code = problem.code || "E-FAILED";
  const sized = code === "E-TOOBIG" && state.fmt === "video" && state.quality ? "E-TOOBIG-Q" : code;
  el.errorBody.textContent = errorText(sized, { maxMb: maxMb(), retryAfter: problem.retryAfter, formatWait });
  const list = actions.filter(Boolean);
  const [primary, ...rest] = list;
  el.errorActions.replaceChildren(...rest.map((a) =>
    h("button", { type: "button", class: "btn secondary block", onclick: a.run }, a.label)));
  show("error");
  setMain(primary ? { text: primary.label, onClick: primary.run } : null);
  setBack(back || (state.preview ? backToPreview : () => goHome()));
  haptic("error");
}

// ---------------------------------------------------------------- 6. TikTok profile

function maxSend() {
  return state.scan?.max_send ?? state.me?.profile_max_send ?? 10;
}

function batchSize() {
  return state.scan?.batch_size ?? state.me?.profile_batch_size ?? 10;
}

function quotaLeft() {
  const me = state.me;
  return me ? Math.max(0, me.limit_per_day - me.usage_today) : Infinity;
}

function scanKey() {
  return `${cleanUsername(el.username.value).toLowerCase()}|${state.limit}`;
}

/** The listing on screen still matches the search fields (else the main button searches again). */
function listingCurrent() {
  return Boolean(state.scan) && state.scannedFor === scanKey();
}

function discardScan() {
  if (!state.scan) return;
  state.scan = null;
  state.picked = new Set();
  api.discard().catch(() => { /* best effort: the server also forgets it after its TTL */ });
}

function goProfile() {
  stopPolling();
  show("profile");
  setBack(leaveProfile);
  renderProfile();
  if (!state.scan) el.username.focus();
}

function leaveProfile() {
  state.scanSeq += 1;     // ignore a scan still in flight
  discardScan();
  el.profileHint.textContent = "";
  goHome();
}

function videoRow(v) {
  const thumb = h("span", { class: "vthumb" });
  if (v.thumbnail) {
    const img = h("img", { alt: "", referrerpolicy: "no-referrer", decoding: "async", loading: "lazy" });
    img.onerror = () => img.remove();
    img.src = v.thumbnail;
    thumb.append(img);
  }
  const meta = [v.duration ? formatDuration(v.duration) : null,
    v.timestamp ? relativeTime(new Date(v.timestamp * 1000).toISOString()) : null].filter(Boolean).join(" · ");
  return h("li", { class: "history-item" },
    h("button", {
      type: "button", class: "video-btn", role: "checkbox", "data-id": v.id,
      "aria-checked": state.picked.has(v.id) ? "true" : "false",
    },
      h("span", { class: "tick", "aria-hidden": "true" }, svg("M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z")),
      thumb,
      h("span", { class: "history-text" },
        h("span", { class: "history-title" }, v.title || shortUrl(v.url)),
        meta ? h("span", { class: "history-meta" }, meta) : null)));
}

function renderPicks() {
  const scan = state.scan;
  if (!scan) return;
  const n = state.picked.size;
  const ids = scan.videos.map((v) => v.id);
  const target = ids.slice(0, maxSend());
  const all = target.length > 0 && target.every((id) => state.picked.has(id));
  el.selectAll.textContent = all ? S.clearAll : S.selectAll;
  el.selectAll.setAttribute("aria-pressed", all ? "true" : "false");
  const overQuota = n > quotaLeft();
  const parts = [S.selected(n, ids.length)];
  if (n) parts.push(S.groupsHint(batchSize(), waveCount(n, batchSize())));
  if (ids.length > maxSend()) parts.push(S.maxPick(maxSend()));
  if (overQuota) parts.push(S.quotaHint(quotaLeft()));
  el.pickHint.textContent = parts.join(" ");
  el.pickHint.className = "hint pick-hint" + (overQuota ? " bad" : "");
  el.videoList.querySelectorAll("[data-id]").forEach((b) => {
    b.setAttribute("aria-checked", state.picked.has(b.dataset.id) ? "true" : "false");
  });
  if (state.screen === "profile" && listingCurrent()) {
    setMain({ text: S.sendSelected(n), active: n > 0, onClick: () => sendPicked() });
  }
}

function renderProfile() {
  setRadio(el.limitChips, "data-limit", state.limit);
  const scan = state.scan;
  el.profileResults.hidden = !scan;
  if (scan) {
    el.resultsTitle.textContent = `@${scan.username} · ${S.profileCount(scan.count, scan.truncated)}`;
    if (el.videoList.dataset.scan !== scan.scan_id) {
      el.videoList.dataset.scan = scan.scan_id;
      el.videoList.replaceChildren(...scan.videos.map(videoRow));
    }
    renderPicks();
  } else {
    el.videoList.dataset.scan = "";
    el.videoList.replaceChildren();
  }
  if (state.screen !== "profile" || listingCurrent()) return;
  setMain({ text: S.findVideos, active: Boolean(cleanUsername(el.username.value)), onClick: () => runScan() });
}

async function runScan() {
  const username = cleanUsername(el.username.value);
  if (!username || state.busy) return;
  const seq = ++state.scanSeq;
  const key = scanKey();
  el.username.blur();
  el.profileHint.className = "hint";
  el.profileHint.textContent = S.scanning;
  setMain({ text: S.findVideos, progress: true });
  try {
    const scan = await api.scan(username, state.limit);
    if (seq !== state.scanSeq) return;
    state.scan = scan;
    state.scannedFor = key;
    state.picked = new Set();
    el.profileHint.textContent = "";
    haptic("success");
    renderProfile();
  } catch (err) {
    if (seq !== state.scanSeq) return;
    const p = toProblem(err);
    if (handleFatal(p)) return;
    state.scan = null;   // the server drops the old listing before it scans
    el.profileHint.className = "hint bad";
    el.profileHint.textContent = errorText(p.code, { retryAfter: p.retryAfter, formatWait });
    haptic("error");
    renderProfile();
  }
}

function togglePick(id) {
  if (state.picked.has(id)) state.picked.delete(id);
  else if (state.picked.size >= maxSend()) { toast(S.maxPick(maxSend())); return; }
  else state.picked.add(id);
  haptic("select");
  renderPicks();
}

function backToProfile() {
  show("profile", { back: true });
  setBack(leaveProfile);
  renderProfile();
}

async function sendPicked(retried = false) {
  const scan = state.scan;
  if (!scan || state.busy || !state.picked.size) return;
  state.busy = true;
  setMain({ text: S.sendSelected(state.picked.size), progress: true });
  const ids = scan.videos.map((v) => v.id).filter((id) => state.picked.has(id));   // listing order
  try {
    const batch = await api.send(scan.scan_id, ids);
    state.scan = null;      // one-time listing: the server forgot it
    state.picked = new Set();
    refreshMe();
    haptic("success");
    beginBatch(batch);
  } catch (err) {
    const p = toProblem(err);
    if (handleFatal(p)) return;
    // Chat delivery needs the bot to be allowed to message the user. Ask once, then retry.
    if (p.code === "E-NOCHAT" && !retried && supports("6.9") && tg.requestWriteAccess) {
      const granted = await new Promise((resolve) => tg.requestWriteAccess(resolve));
      if (granted) {
        state.busy = false;
        return sendPicked(true);
      }
      p.code = "E-NOCHAT-DENIED";
    }
    if (p.code === "E-BUSY" && p.id) {   // a batch is already running: show it instead
      toast(errorText("E-BATCHBUSY"));
      beginBatch(p.id);
      return;
    }
    const back = { label: S.back, run: backToProfile };
    if (p.code === "E-SCANGONE") {
      state.scan = null;
      showError(p, [{ label: S.findVideos, run: () => { backToProfile(); runScan(); } }, back], backToProfile);
      return;
    }
    if (p.code === "E-QUOTA" && p.remaining > 0) {
      const first = {
        label: S.sendFirst(p.remaining),
        run: () => {
          state.picked = new Set(ids.slice(0, p.remaining));
          backToProfile();
          sendPicked();
        },
      };
      showError(p, [first, back], backToProfile);
      return;
    }
    if (p.code === "E-RATELIMIT") refreshMe();
    const retry = ["E-FAILED", "E-NETWORK"].includes(p.code)
      ? { label: S.retry, run: () => { backToProfile(); sendPicked(); } } : null;
    showError(p, [retry, back], backToProfile);
  } finally {
    state.busy = false;
  }
}

// ---------------------------------------------------------------- 7. batch progress

function stopBatchPolling() {
  clearTimeout(state.batchTimer);
  state.batchTimer = 0;
}

/** batch: the send response (a batch view) or just its id (resume / already running). */
function beginBatch(batch) {
  const id = typeof batch === "string" ? batch : batch.id;
  stopBatchPolling();
  state.batchId = id;
  state.batch = typeof batch === "string" ? null : batch;
  state.batchFailures = 0;
  session.save({ batch: id });
  show("batch");
  setBack(() => goHome());   // leaving does not stop the batch: the server keeps sending
  renderBatch();
  if (state.batch && state.batch.state !== "running") { session.save(null); return; }
  state.batchTimer = setTimeout(pollBatch, state.batch ? BATCH_POLL_MS : 0);
}

async function pollBatch() {
  stopBatchPolling();
  const id = state.batchId;
  if (!id || document.hidden) return; // resumed by visibilitychange
  try {
    const batch = await api.batch(id);
    if (id !== state.batchId) return;
    state.batch = batch;
    state.batchFailures = 0;
    renderBatch();
    if (batch.state === "running") {
      state.batchTimer = setTimeout(pollBatch, BATCH_POLL_MS);
    } else {
      session.save(null);
      haptic(batch.state === "done" ? "success" : "warning");
      refreshMe();
    }
  } catch (err) {
    if (id !== state.batchId) return;
    const p = toProblem(err);
    if (handleFatal(p)) return;
    if (p.code === "E-NOTFOUND") {   // forgotten by the server (restart or expiry)
      session.save(null);
      state.batchId = null;
      showError(p, [{ label: S.another, run: () => goHome({ clear: true }) }]);
      return;
    }
    state.batchFailures += 1;
    if (state.batchFailures === 2) toast(errorText("E-NETWORK"));
    state.batchTimer = setTimeout(pollBatch, retryDelay(state.batchFailures));
  }
}

function batchRow(item) {
  const label = item.status === "waiting" ? S.itemStatus.waiting : (S.status[item.status] ?? S.status.queued);
  return h("li", { class: "history-item batch-row" },
    platformMark("tiktok"),
    h("span", { class: "history-text" },
      h("span", { class: "history-title" }, item.title || item.video_id),
      item.error_code ? h("span", { class: "history-meta" }, errorText(item.error_code, { maxMb: maxMb() })) : null),
    h("span", { class: `badge ${item.status}`, title: label }, item.status === "done" ? "✓" : label));
}

function renderBatch() {
  const b = state.batch;
  if (!b) {
    el.batchTitle.textContent = "";
    el.batchStage.textContent = S.loading;
    el.batchBar.classList.add("indeterminate");
    el.batchCounts.textContent = "";
    el.batchNote.textContent = "";
    el.batchList.replaceChildren();
    el.batchCancel.hidden = true;
    setMain(null);
    return;
  }
  const running = b.state === "running";
  el.batchTitle.textContent = S.batchTitle(b.username);
  el.batchStage.textContent = running ? S.batchWave(Math.max(1, b.wave), b.waves, b.batch_size)
    : b.state === "done" ? S.batchDone : S.batchCancelled;
  const pct = batchPercent(b);
  el.batchBar.classList.remove("indeterminate");
  el.batchBarFill.style.width = `${pct}%`;
  el.batchBar.setAttribute("aria-valuenow", String(pct));
  el.batchCounts.textContent = running ? S.batchCounts(b.done, b.failed, b.waiting) : S.batchDoneBody(b.done, b.total);
  el.batchNote.textContent = running ? S.batchKeepOpen : "";

  const rows = [];
  let wave = 0;
  for (const item of b.items) {
    if (b.waves > 1 && item.wave !== wave) {
      wave = item.wave;
      rows.push(h("li", { class: "wave-head", "aria-hidden": "true" }, S.batchWave(wave, b.waves, b.batch_size)));
    }
    rows.push(batchRow(item));
  }
  el.batchList.replaceChildren(...rows);

  el.batchCancel.hidden = !running;
  if (state.screen !== "batch") return;
  if (running) setMain(null);
  else setMain({ text: S.openChat, onClick: () => tg.close() });
}

async function cancelBatch() {
  const id = state.batchId;
  if (!id || !(await confirm(S.cancelBatchConfirm))) return;
  el.batchCancel.disabled = true;
  try {
    state.batch = await api.cancelBatch(id);
    haptic("warning");
    renderBatch();
    refreshMe();   // never-started videos were given back to today's quota
  } catch (err) {
    const p = toProblem(err);
    if (handleFatal(p)) return;
    toast(errorText(p.code));
  } finally {
    el.batchCancel.disabled = false;
  }
  pollBatch();
}

async function resumeBatch(id) {
  try {
    const batch = await api.batch(id);
    if (batch.state === "running") {
      beginBatch(batch);
      return true;
    }
  } catch (err) {
    if (handleFatal(toProblem(err))) return true;
  }
  session.save(null);
  return false;
}

// ---------------------------------------------------------------- boot

function applyTheme() {
  if (tg?.colorScheme) document.documentElement.dataset.scheme = tg.colorScheme;
  if (supports("6.1")) {
    try {
      tg.setHeaderColor("bg_color");
      tg.setBackgroundColor("bg_color");
    } catch { /* ignore */ }
  }
}

let bound = false;
function bindEvents() {
  if (bound) return; // boot() re-runs on "Try again"
  bound = true;
  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (currentLink().platform && !cooling()) goPreview();
  });
  el.link.addEventListener("input", renderHome);
  el.paste.addEventListener("click", paste);
  el.clear.addEventListener("click", () => {
    el.link.value = "";
    renderHome();
    el.link.focus();
  });
  el.refresh.addEventListener("click", () => {
    haptic("select");
    refreshMe();
    refreshHistory();
  });

  el.fmtGroup.addEventListener("click", (e) => {
    const b = e.target.closest("[data-fmt]");
    if (!b || b.disabled || b.dataset.fmt === state.fmt) return;
    state.fmt = b.dataset.fmt;
    haptic("select");
    renderPreview();
  });
  el.qualityChips.addEventListener("click", (e) => {
    const b = e.target.closest("[data-q]");
    if (!b) return;
    state.quality = Number(b.dataset.q);
    haptic("select");
    renderPreview();
  });
  el.deliveryGroup.addEventListener("click", (e) => {
    const b = e.target.closest("[data-delivery]");
    if (!b || b.dataset.delivery === state.delivery) return;
    state.delivery = b.dataset.delivery;
    haptic("select");
    renderPreview();
  });
  // Arrow keys move between options of a radio group (WAI-ARIA radio pattern).
  for (const group of [el.fmtGroup, el.qualityChips, el.deliveryGroup, el.limitChips]) {
    group.addEventListener("keydown", (e) => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
      const items = [...group.querySelectorAll('[role="radio"]:not(:disabled)')];
      const i = items.indexOf(document.activeElement);
      if (i === -1) return;
      e.preventDefault();
      const next = items[(i + (e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1) + items.length) % items.length];
      next.click();
      group.querySelector('[aria-checked="true"]')?.focus();
    });
  }

  el.cancel.addEventListener("click", cancelJob);

  el.profileEntryMark.replaceChildren(svg(PLATFORM_ICON.tiktok));
  el.profileEntry.addEventListener("click", () => { haptic("select"); goProfile(); });
  el.profileForm.addEventListener("submit", (e) => {
    e.preventDefault();
    if (cleanUsername(el.username.value)) runScan();
  });
  el.username.addEventListener("input", renderProfile);
  el.limitChips.addEventListener("click", (e) => {
    const b = e.target.closest("[data-limit]");
    if (!b) return;
    state.limit = b.dataset.limit === "all" ? "all" : Number(b.dataset.limit);
    haptic("select");
    renderProfile();
  });
  el.videoList.addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]");
    if (b) togglePick(b.dataset.id);
  });
  el.selectAll.addEventListener("click", () => {
    if (!state.scan) return;
    state.picked = toggleAll(state.scan.videos.map((v) => v.id), state.picked, maxSend());
    haptic("select");
    renderPicks();
  });
  el.batchCancel.addEventListener("click", cancelBatch);
  // Closing the app forgets the one-time listing right away (the server would after its TTL).
  window.addEventListener("pagehide", () => { if (state.scan) discardScan(); });
  el.another.addEventListener("click", () => goHome({ clear: true }));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.jobId) pollTick();
    if (!document.hidden && state.batchId && state.batch?.state !== "done"
        && state.batch?.state !== "cancelled") pollBatch();
  });
  tg?.onEvent?.("themeChanged", () => {
    applyTheme();
    // MainButton colors come from the theme: re-apply the current screen's button.
    if (state.screen === "home") renderHome();
    else if (state.screen === "preview" && state.preview) renderPreview();
    else if (state.screen === "profile") { el.videoList.dataset.scan = ""; renderProfile(); }
    else if (state.screen === "batch") renderBatch();
  });
}

function outsideTelegram() {
  const bot = typeof config.botUsername === "string" && /^[A-Za-z0-9_]{5,32}$/.test(config.botUsername)
    ? config.botUsername : null;
  showState({
    icon: ICON.telegram, title: S.outsideTitle, body: S.outsideBody,
    action: bot ? { label: S.openInTelegram, run: () => { location.href = `https://t.me/${bot}`; } } : null,
  });
}

async function resume(saved) {
  Object.assign(state, {
    link: saved.link || "", preview: saved.preview || null, fmt: saved.fmt || "video",
    quality: saved.quality ?? null, delivery: saved.delivery || "device", jobId: saved.id,
  });
  try {
    const job = await api.get(saved.id);
    state.job = job;
    if (screenFor(job) === "progress") {
      beginJob(saved.id);
      return true;
    }
    if (job.status === "done" && job.delivery === "device" && job.file) {
      onJob(job, { quiet: true });
      return true;
    }
  } catch (err) {
    if (handleFatal(toProblem(err))) return true;
  }
  session.save(null);
  state.jobId = null;
  state.job = null;
  return false;
}

async function boot() {
  applyStrings();
  document.title = S.appTitle;

  if (!tg || !tg.initData) {
    outsideTelegram();
    return;
  }

  tg.ready();
  tg.expand();
  if (supports("7.7")) {
    try { tg.disableVerticalSwipes(); } catch { /* ignore */ }
  }
  applyTheme();

  api = createApi(config.apiBase ?? "", tg.initData);
  bindEvents();

  const [meRes, listRes] = await Promise.allSettled([api.me(), api.list()]);
  if (meRes.status === "rejected") {
    const p = toProblem(meRes.reason);
    if (handleFatal(p)) return;
    showState({
      icon: ICON.clock, title: errorText(p.code), body: "",
      action: { label: S.retry, run: () => { showState({ title: S.loading }); boot(); } },
    });
    return;
  }

  state.me = meRes.value;
  if (listRes.status === "fulfilled") state.history = listRes.value.items || [];

  el.state.hidden = true;
  el.app.hidden = false;
  renderHeader();
  renderHistory();

  const saved = session.load();
  if (saved?.id && (await resume(saved))) return;
  if (saved?.batch && (await resumeBatch(saved.batch))) return;

  show("home");
  renderHome();
}

boot();
