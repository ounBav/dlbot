// Pure helpers: no DOM, no network. Unit-tested with `node --test` (tests/logic.test.js).

// Same pattern as apps/bot/app/url_utils.py so "link detected" matches what the server accepts.
const URL_RE = /https?:\/\/[^\s<>"']+/i;
const TRAILING = /[.,;:!?)]+$/;

export const FINAL = new Set(["done", "failed", "cancelled", "expired"]);
export const STEPS = ["queued", "downloading", "uploading"];

export function extractUrl(text) {
  const match = URL_RE.exec(text || "");
  return match ? match[0].replace(TRAILING, "") : null;
}

/** platforms: [{key, label, hosts: [...]}] from GET /api/me. Returns the platform or null. */
export function detectPlatform(url, platforms) {
  if (!url) return null;
  let host;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
  return (platforms || []).find((p) => p.hosts.includes(host)) || null;
}

export function isFinal(status) {
  return FINAL.has(status);
}

/** Index into STEPS for the progress bar. "retrying" counts as downloading. */
export function stepIndex(status) {
  if (status === "retrying") return 1;
  if (status === "done") return STEPS.length;
  const i = STEPS.indexOf(status);
  return i === -1 ? 0 : i;
}

/** Poll faster while a job is fresh, back off as it ages (ms since polling started). */
export function pollDelay(elapsedMs) {
  if (elapsedMs < 20_000) return 1500;
  if (elapsedMs < 120_000) return 3000;
  return 6000;
}

export function shortUrl(url, max = 48) {
  try {
    const u = new URL(url);
    const text = u.hostname.replace(/^www\./, "") + u.pathname.replace(/\/$/, "");
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  } catch {
    return String(url || "").slice(0, max);
  }
}

export function relativeTime(iso, now = Date.now()) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const s = Math.max(0, Math.round((now - then) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

export function formatWait(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

/** Normalise any thrown/returned API problem into {code, retryAfter, status}. */
export function toProblem(err) {
  if (err && typeof err.code === "string") return err;
  return { code: "E-NETWORK", status: 0, retryAfter: 0 };
}

/** Merge a fresh server list into local items, keeping optimistic "cancelling" flags. */
export function mergeItems(serverItems, cancelling) {
  return serverItems.map((item) =>
    cancelling.has(item.id) && !isFinal(item.status) ? { ...item, status: "cancelling" } : item,
  );
}

// ---------------------------------------------------------------- preview / save-to-device flow

/** Default quality chip: the highest at or below 720p, else the lowest offered. */
export function defaultQuality(qualities) {
  const list = [...(qualities || [])].sort((a, b) => a - b);
  if (!list.length) return null;
  const upTo720 = list.filter((q) => q <= 720);
  return upTo720.length ? upTo720[upTo720.length - 1] : list[0];
}

/** Next quality below `current` (for "file too large"), or null. */
export function lowerQuality(qualities, current) {
  const below = (qualities || []).filter((q) => q < current).sort((a, b) => b - a);
  return below.length ? below[0] : null;
}

export function formatDuration(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/** 0-1 fraction (or null) -> whole percent 0-100 (or null). */
export function percent(fraction) {
  if (fraction == null || Number.isNaN(Number(fraction))) return null;
  return Math.max(0, Math.min(100, Math.floor(Number(fraction) * 100)));
}

/** Back-off after consecutive network failures while polling: 1.5 s, 3 s, 6 s, 12 s (cap). */
export function retryDelay(failures) {
  return Math.min(1500 * 2 ** Math.max(0, failures), 12_000);
}

/** Absolute URL for a server path like /api/files/<token>. `base` is config.js apiBase. */
export function absoluteUrl(base, path, origin = "") {
  const root = (base || origin || "").replace(/\/+$/, "");
  return `${root}${path.startsWith("/") ? "" : "/"}${path}`;
}

/** Which screen a polled job belongs on. */
export function screenFor(job) {
  if (!job) return "home";
  if (job.status === "done") return "ready";
  if (job.status === "cancelled") return "home";
  if (job.status === "failed" || job.status === "expired") return "error";
  return "progress";
}

/** Error code shown for a finished job (expired jobs and expired links get their own). */
export function jobErrorCode(job) {
  if (job.status === "expired") return "E-EXPIRED";
  if (job.status === "done" && job.delivery === "device" && !job.file) return "E-GONE";
  return job.error_code || "E-FAILED";
}

// ---------------------------------------------------------------- TikTok profile batches

/** "@Name", profile links etc. -> what the user typed, trimmed. The server does the real parsing. */
export function cleanUsername(raw) {
  return String(raw || "").trim();
}

/** Toggle "Select all": everything (capped at `max`) unless that is already selected. */
export function toggleAll(ids, selected, max = Infinity) {
  const target = ids.slice(0, max);
  const all = target.length > 0 && target.every((id) => selected.has(id));
  return all ? new Set() : new Set(target);
}

/** Number of waves (groups) a batch of `n` videos is split into. */
export function waveCount(n, size) {
  return n > 0 ? Math.ceil(n / Math.max(1, size)) : 0;
}

export function batchPercent(batch) {
  if (!batch?.total) return 0;
  const over = (batch.done || 0) + (batch.failed || 0) + (batch.cancelled || 0);
  return Math.round((over / batch.total) * 100);
}
