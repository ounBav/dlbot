// Thin client for apps/bot/app/api.py. Every call sends `Authorization: tma <initData>`.
// Failures throw {code, status, retryAfter} using the server's E-* codes (E-NETWORK for transport).

const TIMEOUT_MS = 15_000;
const INFO_TIMEOUT_MS = 40_000;   // the server allows yt-dlp 30 s to read a link

export function createApi(baseUrl, initData) {
  const base = (baseUrl || "").replace(/\/+$/, "");

  async function request(method, path, body) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), path === "/info" ? INFO_TIMEOUT_MS : TIMEOUT_MS);
    let resp;
    try {
      resp = await fetch(`${base}/api${path}`, {
        method,
        headers: {
          Authorization: `tma ${initData}`,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
        cache: "no-store",
        signal: ctrl.signal,
      });
    } catch {
      throw { code: "E-NETWORK", status: 0, retryAfter: 0 };
    } finally {
      clearTimeout(timer);
    }

    let data = null;
    try {
      data = await resp.json();
    } catch {
      /* empty or non-JSON body */
    }
    if (resp.ok) return data;

    const detail = data && typeof data.detail === "object" && data.detail ? data.detail : {};
    // 422 = request validation (bad format value); treat like an unsupported link.
    const code = detail.code || (resp.status === 422 ? "E-UNSUPPORTED" : "E-FAILED");
    throw { code, status: resp.status, retryAfter: Number(detail.retry_after) || 0, id: detail.id ?? null };
  }

  return {
    me: () => request("GET", "/me"),
    list: () => request("GET", "/downloads"),
    info: (url) => request("POST", "/info", { url }),
    get: (id) => request("GET", `/downloads/${encodeURIComponent(id)}`),
    // opts: {quality: 360|480|720|1080, delivery: "chat"|"device"}. Without opts: the original call.
    create: (url, format, opts = {}) => request("POST", "/downloads", {
      url, format,
      ...(opts.quality ? { quality: opts.quality } : {}),
      ...(opts.delivery ? { delivery: opts.delivery } : {}),
    }),
    cancel: (id) => request("POST", `/downloads/${encodeURIComponent(id)}/cancel`),
  };
}
