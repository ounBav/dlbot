// Mini App controller. The file itself is always delivered in the bot chat by the worker;
// this page only submits links and shows progress (see apps/bot/app/api.py).

import { createApi } from "./api.js";
import {
  detectPlatform, extractUrl, formatWait, isFinal, mergeItems, pollDelay,
  relativeTime, shortUrl, STEPS, stepIndex, toProblem,
} from "./logic.js";
import { errorText, S } from "./strings.js";

const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);
const el = {
  state: $("state"), stateIcon: $("state-icon"), stateTitle: $("state-title"),
  stateBody: $("state-body"), stateAction: $("state-action"),
  app: $("app"), greeting: $("greeting"), usage: $("usage"), usageText: $("usage-text"), usageBar: $("usage-bar"),
  form: $("form"), field: $("field"), link: $("link"), paste: $("paste"), clear: $("clear"),
  linkHint: $("link-hint"), limitHint: $("limit-hint"), formError: $("form-error"), submit: $("submit"),
  jobs: $("jobs"), jobList: $("job-list"),
  historyList: $("history-list"), historyEmpty: $("history-empty"), refresh: $("refresh"),
  terms: $("terms"), toast: $("toast"),
};

const state = {
  me: null,
  items: [],                 // server list (newest first), with optimistic "cancelling"
  lastStatus: new Map(),     // id -> status, to detect transitions
  cancelling: new Set(),
  sessionJobs: new Set(),    // submitted from this page: keep their final card until dismissed
  dismissed: new Set(),
  busy: false,
  cooldownUntil: 0,
  pollStarted: 0,
  pollTimer: 0,
  cooldownTimer: 0,
  inflight: null,
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

function fmtLabel(fmt) {
  return fmt === "audio" ? S.audio : S.video;
}

// ---------------------------------------------------------------- full-screen states

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
  hideMainButton();
}

const closeAction = tg?.initData ? { label: S.close, run: () => tg.close() } : null;

function handleFatal(problem) {
  if (problem.code === "E-AUTH") {
    showState({ icon: ICON.clock, title: S.authTitle, body: S.authBody, action: closeAction });
    return true;
  }
  if (problem.code === "E-BANNED") {
    showState({ icon: ICON.lock, title: S.bannedTitle, body: S.bannedBody, action: closeAction });
    return true;
  }
  return false;
}

// ---------------------------------------------------------------- submit control
// Telegram's native MainButton when available; the in-page button otherwise (and as a11y fallback).

const useMainButton = Boolean(tg?.initData && tg.MainButton && supports("6.1"));

function hideMainButton() {
  if (useMainButton) tg.MainButton.hide();
}

function selectedFormat() {
  return el.form.elements.format.value === "audio" ? "audio" : "video";
}

function currentLink() {
  const url = extractUrl(el.link.value);
  return { url, platform: detectPlatform(url, state.me?.platforms) };
}

function updateForm() {
  const raw = el.link.value.trim();
  const { url, platform } = currentLink();
  const cooling = Date.now() < state.cooldownUntil;
  const valid = Boolean(platform) && !state.busy && !cooling && Boolean(state.me);

  el.clear.hidden = !raw;
  el.paste.hidden = Boolean(raw) || !canPaste;
  el.field.classList.toggle("ok", Boolean(platform));
  el.field.classList.toggle("bad", Boolean(raw) && !platform);
  el.linkHint.className = "hint" + (platform ? " ok" : raw ? " bad" : "");
  el.linkHint.textContent = platform ? S.detected(platform.label)
    : raw ? (url ? S.notSupported : S.noLink) : "";

  let label = S.submit(selectedFormat());
  if (state.busy) label = S.submitting;
  else if (cooling) label = S.waitFor(formatWait((state.cooldownUntil - Date.now()) / 1000));

  el.submit.textContent = label;
  el.submit.disabled = !valid;

  if (useMainButton) {
    const theme = tg.themeParams || {};
    tg.MainButton.setParams({
      text: label,
      is_visible: true,
      is_active: valid,
      color: valid ? theme.button_color : (theme.hint_color || theme.button_color),
      text_color: theme.button_text_color,
    });
    if (state.busy) tg.MainButton.showProgress(false);
    else tg.MainButton.hideProgress();
  }
}

function setFormError(text) {
  el.formError.textContent = text || "";
  el.formError.hidden = !text;
}

function startCooldown(seconds) {
  state.cooldownUntil = Date.now() + seconds * 1000;
  clearInterval(state.cooldownTimer);
  state.cooldownTimer = setInterval(() => {
    if (Date.now() >= state.cooldownUntil) {
      clearInterval(state.cooldownTimer);
      setFormError("");
    }
    updateForm();
  }, 1000);
  updateForm();
}

// ---------------------------------------------------------------- submit flow

async function submit() {
  const { url, platform } = currentLink();
  if (!platform || state.busy || Date.now() < state.cooldownUntil) return;
  state.busy = true;
  setFormError("");
  updateForm();
  try {
    await createWithChatAccess(url, selectedFormat());
  } finally {
    state.busy = false;
    updateForm();
  }
}

async function createWithChatAccess(url, fmt, retried = false) {
  try {
    const res = await api.create(url, fmt);
    el.link.value = "";
    haptic("success");
    if (res.cached) {
      toast(S.cachedSent);
    } else {
      state.sessionJobs.add(res.id);
      state.lastStatus.set(res.id, "queued"); // so a job that finishes before the next poll still notifies
      toast(S.queuedToast);
      el.link.blur();
      startPolling();
    }
    refreshMe();
  } catch (err) {
    const p = toProblem(err);
    if (handleFatal(p)) return;

    // The bot can only message users who started it. Ask Telegram for permission, then retry once.
    if (p.code === "E-NOCHAT" && !retried && supports("6.9") && tg.requestWriteAccess) {
      const granted = await new Promise((resolve) => tg.requestWriteAccess(resolve));
      if (granted) return createWithChatAccess(url, fmt, true);
      p.code = "E-NOCHAT-DENIED";
    }

    haptic("error");
    if (p.code === "E-RATELIMIT" && p.retryAfter > 0 && p.retryAfter <= 3600) startCooldown(p.retryAfter);
    setFormError(errorText(p.code, { maxMb: state.me?.max_upload_mb, retryAfter: p.retryAfter, formatWait }));
    if (p.code === "E-RATELIMIT") refreshMe();
  }
}

async function cancelJob(id) {
  const confirmed = await confirm(S.cancelConfirm);
  if (!confirmed) return;
  state.cancelling.add(id);
  render();
  try {
    await api.cancel(id);
    haptic("warning");
  } catch (err) {
    const p = toProblem(err);
    state.cancelling.delete(id);
    if (handleFatal(p)) return;
    if (p.code !== "E-FINISHED") toast(errorText(p.code));
  }
  startPolling();
}

function confirm(text) {
  if (supports("6.2") && tg.showConfirm) return new Promise((resolve) => tg.showConfirm(text, resolve));
  return Promise.resolve(window.confirm(text));
}

function reuse(item, fmt = item.format) {
  el.link.value = item.url;
  const radio = [...el.form.elements.format].find((r) => r.value === fmt);
  if (radio) radio.checked = true;
  if (isFinal(item.status)) state.dismissed.add(item.id);
  setFormError("");
  updateForm();
  render();
  haptic("select");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ---------------------------------------------------------------- data + polling

async function refreshMe() {
  try {
    state.me = await api.me();
    renderHeader();
    updateForm();
  } catch (err) {
    handleFatal(toProblem(err));
  }
}

async function refreshList() {
  if (state.inflight) return state.inflight;
  state.inflight = (async () => {
    try {
      const { items } = await api.list();
      for (const item of items) if (isFinal(item.status)) state.cancelling.delete(item.id);
      state.items = mergeItems(items, state.cancelling);
      notifyTransitions();
      render();
      return true;
    } catch (err) {
      const p = toProblem(err);
      if (!handleFatal(p) && p.code === "E-NETWORK") toast(errorText("E-NETWORK"));
      return false;
    } finally {
      state.inflight = null;
    }
  })();
  return state.inflight;
}

function notifyTransitions() {
  let finishedOne = false;
  for (const item of state.items) {
    const prev = state.lastStatus.get(item.id);
    if (prev && prev !== item.status && isFinal(item.status) && state.sessionJobs.has(item.id)) {
      haptic(item.status === "done" ? "success" : item.status === "cancelled" ? "warning" : "error");
      finishedOne = true;
    }
    state.lastStatus.set(item.id, item.status);
  }
  if (finishedOne) refreshMe();
}

const hasActive = () => state.items.some((i) => !isFinal(i.status));

function startPolling() {
  state.pollStarted = Date.now();
  clearTimeout(state.pollTimer);
  tick();
}

async function tick() {
  clearTimeout(state.pollTimer);
  if (document.hidden) return; // resumed by visibilitychange
  await refreshList();
  if (hasActive()) {
    state.pollTimer = setTimeout(tick, pollDelay(Date.now() - state.pollStarted));
  }
}

// ---------------------------------------------------------------- rendering

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
  el.limitHint.textContent = S.limitHint(me.max_upload_mb);

  const terms = me.terms_url;
  const safe = typeof terms === "string" && /^https?:\/\//i.test(terms);
  el.terms.hidden = !safe;
  el.terms.onclick = safe ? () => (tg?.openLink ? tg.openLink(terms) : window.open(terms, "_blank", "noopener")) : null;
}

function itemTitle(item) {
  return item.title || shortUrl(item.url);
}

function jobCard(item) {
  const status = item.status;
  const final = isFinal(status);
  const meta = `${platformLabel(item.platform)} · ${fmtLabel(item.format)}`;

  let statusText = S.status[status] ?? S.status.queued;
  if (status === "failed") statusText = errorText(item.error_code || "E-FAILED", { maxMb: state.me?.max_upload_mb });
  if (status === "expired") statusText = errorText("E-EXPIRED");

  const actions = [];
  if (!final && status !== "cancelling") {
    actions.push(h("button", { type: "button", class: "btn danger small", onclick: () => cancelJob(item.id) }, S.cancel));
  }
  if (status === "done" && tg?.initData) {
    actions.push(h("button", { type: "button", class: "btn primary small", onclick: () => tg.close() }, S.openChat));
  }
  if (status === "failed" && item.error_code === "E-TOOBIG" && item.format === "video") {
    actions.push(h("button", { type: "button", class: "btn secondary small", onclick: () => reuse(item, "audio") }, S.tryAudio));
  } else if ((status === "failed" && (item.error_code || "E-FAILED") === "E-FAILED") || status === "expired") {
    actions.push(h("button", { type: "button", class: "btn secondary small", onclick: () => reuse(item) }, S.retry));
  }
  if (final) {
    actions.push(h("button", {
      type: "button", class: "btn secondary small",
      onclick: () => { state.dismissed.add(item.id); render(); },
    }, S.dismiss));
  }

  const current = stepIndex(status === "cancelling" ? "queued" : status);
  const steps = final ? null : h("ol", { class: "steps", "aria-hidden": "true" },
    STEPS.map((step, i) => h("li", {
      class: "step" + (i < current ? " complete" : i === current ? " current" : ""),
    }, h("span", { class: "step-bar" }), S.steps[step])));

  return h("article", { class: `card job ${status}`, "aria-label": itemTitle(item) },
    h("div", { class: "job-head" },
      h("span", { class: "mark" }, svg(item.format === "audio" ? ICON.audio : ICON.video)),
      h("div", { class: "job-text" },
        h("p", { class: "job-title" }, itemTitle(item)),
        h("p", { class: "job-meta" }, meta))),
    steps,
    h("p", { class: "job-status", role: "status" }, statusText),
    actions.length ? h("div", { class: "job-actions" }, actions) : null);
}

function historyRow(item) {
  // "done" reads as a check mark; its long status text ("Sent to your chat") stays in the aria-label.
  const badgeText = item.status === "done" ? "✓" : isFinal(item.status) ? S.status[item.status] : "…";
  return h("li", { class: "history-item" },
    h("button", {
      type: "button", class: "history-btn", onclick: () => reuse(item),
      "aria-label": `${itemTitle(item)}. ${S.status[item.status] ?? ""}. ${S.reuse}`,
    },
      h("span", { class: "mark" }, svg(item.format === "audio" ? ICON.audio : ICON.video)),
      h("span", { class: "history-text" },
        h("span", { class: "history-title" }, itemTitle(item)),
        h("span", { class: "history-meta" },
          `${platformLabel(item.platform)} · ${fmtLabel(item.format)} · ${relativeTime(item.created_at)}`)),
      h("span", { class: `badge ${item.status}`, title: S.status[item.status] ?? "" }, badgeText)));
}

// Keyed rendering: a node is only rebuilt when what it shows changes, so polling never
// steals a tap or resets focus on a button the user is about to press.
const cardCache = new Map();
const rowCache = new Map();

function keyed(cache, items, sigOf, build) {
  const seen = new Set();
  const nodes = items.map((item) => {
    const sig = sigOf(item);
    const hit = cache.get(item.id);
    seen.add(item.id);
    if (hit && hit.sig === sig) return hit.node;
    const node = build(item);
    cache.set(item.id, { sig, node });
    return node;
  });
  for (const id of cache.keys()) if (!seen.has(id)) cache.delete(id);
  return nodes;
}

function syncChildren(parent, nodes) {
  nodes.forEach((node, i) => {
    if (parent.children[i] !== node) parent.insertBefore(node, parent.children[i] || null);
  });
  while (parent.children.length > nodes.length) parent.lastElementChild.remove();
}

function render() {
  const cards = state.items.filter((i) =>
    !state.dismissed.has(i.id) && (!isFinal(i.status) || state.sessionJobs.has(i.id)));
  syncChildren(el.jobList, keyed(cardCache, cards,
    (i) => `${i.status}|${i.title}|${i.error_code}`, jobCard));
  el.jobs.hidden = cards.length === 0;

  const shown = new Set(cards.map((i) => i.id));
  const history = state.items.filter((i) => !shown.has(i.id));
  const minute = Math.floor(Date.now() / 60000); // refresh relative times once a minute
  syncChildren(el.historyList, keyed(rowCache, history,
    (i) => `${i.status}|${i.title}|${minute}`, historyRow));
  el.historyList.hidden = history.length === 0;
  el.historyEmpty.hidden = history.length > 0 || cards.length > 0;
}

// ---------------------------------------------------------------- clipboard

const canPaste = Boolean(navigator.clipboard?.readText) && window.isSecureContext;

async function paste() {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      el.link.value = (extractUrl(text) || text).trim();
      haptic("select");
      updateForm();
      return;
    }
  } catch { /* permission denied or not supported in this webview */ }
  el.link.focus();
  toast(S.copiedHint);
}

// ---------------------------------------------------------------- boot

function applyTheme() {
  if (tg?.colorScheme) document.documentElement.dataset.scheme = tg.colorScheme;
  if (supports("6.1")) {
    try {
      tg.setHeaderColor("secondary_bg_color");
      tg.setBackgroundColor("secondary_bg_color");
    } catch { /* ignore */ }
  }
}

let bound = false;
function bindEvents() {
  if (bound) return; // boot() re-runs on "Try again"
  bound = true;
  el.form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  el.link.addEventListener("input", () => { setFormError(""); updateForm(); });
  el.form.addEventListener("change", (e) => {
    if (e.target.name === "format") { haptic("select"); updateForm(); }
  });
  el.paste.addEventListener("click", paste);
  el.clear.addEventListener("click", () => {
    el.link.value = "";
    setFormError("");
    updateForm();
    el.link.focus();
  });
  el.refresh.addEventListener("click", async () => {
    haptic("select");
    await Promise.all([refreshMe(), refreshList()]);
    if (hasActive()) startPolling();
  });
  if (useMainButton) {
    el.submit.hidden = true;
    tg.MainButton.onClick(submit);
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && hasActive()) tick();
  });
  tg?.onEvent?.("themeChanged", () => { applyTheme(); updateForm(); });
}

async function boot() {
  applyStrings();
  document.title = S.appTitle;

  if (!tg || !tg.initData) {
    showState({ icon: ICON.telegram, title: S.outsideTitle, body: S.outsideBody });
    return;
  }

  tg.ready();
  tg.expand();
  applyTheme();

  const base = window.MINIAPP_CONFIG?.apiBase ?? "";
  api = createApi(base, tg.initData);
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
  if (listRes.status === "fulfilled") {
    state.items = listRes.value.items;
    for (const i of state.items) state.lastStatus.set(i.id, i.status);
  }

  el.state.hidden = true;
  el.app.hidden = false;
  renderHeader();
  render();
  updateForm();
  if (hasActive()) startPolling();
}

boot();
