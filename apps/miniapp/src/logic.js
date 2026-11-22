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
