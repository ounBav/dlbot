import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { createApi } from "../src/api.js";

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

function mockFetch(status, body, calls = []) {
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => {
        if (body === undefined) throw new SyntaxError("no body");
        return body;
      },
    };
  };
  return calls;
}

test("sends initData as the tma Authorization header to the configured base", async () => {
  const calls = mockFetch(200, { items: [] });
  await createApi("https://api.example/", "query_id=1&hash=abc").list();
  assert.equal(calls[0].url, "https://api.example/api/downloads");
  assert.equal(calls[0].init.headers.Authorization, "tma query_id=1&hash=abc");
  assert.equal(calls[0].init.cache, "no-store");
});

test("create posts JSON", async () => {
  const calls = mockFetch(200, { id: "x", status: "queued", cached: false });
  const res = await createApi("", "d").create("https://youtu.be/a", "audio");
  assert.equal(res.id, "x");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].init.body), { url: "https://youtu.be/a", format: "audio" });
});

test("server error detail becomes a problem with code and retry_after", async () => {
  mockFetch(429, { detail: { code: "E-RATELIMIT", retry_after: 17 } });
  await assert.rejects(createApi("", "d").create("u", "video"),
    { code: "E-RATELIMIT", status: 429, retryAfter: 17 });
});

test("422 validation errors and non-JSON failures are mapped", async () => {
  mockFetch(422, { detail: [{ msg: "bad" }] });
  await assert.rejects(createApi("", "d").create("u", "gif"), { code: "E-UNSUPPORTED" });
  mockFetch(502, undefined);
  await assert.rejects(createApi("", "d").me(), { code: "E-FAILED", status: 502 });
});

test("transport failure is E-NETWORK", async () => {
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(createApi("", "d").me(), { code: "E-NETWORK", status: 0 });
});

test("cancel encodes the id", async () => {
  const calls = mockFetch(200, { status: "cancelling" });
  await createApi("", "d").cancel("a/b");
  assert.equal(calls[0].url, "/api/downloads/a%2Fb/cancel");
});

test("profile batch calls: send, poll, cancel, keepalive discard", async () => {
  const calls = mockFetch(200, { id: "b1", state: "running" });
  const api = createApi("", "d");
  await api.send("s/1", ["7", "8"]);
  await api.batch("b/1");
  await api.cancelBatch("b1");
  await api.discard();
  assert.equal(calls[0].url, "/api/tiktok/scan/s%2F1/send");
  assert.deepEqual(JSON.parse(calls[0].init.body), { video_ids: ["7", "8"] });
  assert.equal(calls[1].url, "/api/tiktok/batches/b%2F1");
  assert.equal(calls[2].url, "/api/tiktok/batches/b1/cancel");
  assert.equal(calls[3].url, "/api/tiktok/scan/discard");
  assert.equal(calls[3].init.keepalive, true);
  assert.equal(calls[0].init.keepalive, false);
});

test("quota and busy details reach the caller", async () => {
  mockFetch(429, { detail: { code: "E-QUOTA", remaining: 4 } });
  await assert.rejects(createApi("", "d").send("s", ["1"]), { code: "E-QUOTA", remaining: 4 });
  mockFetch(409, { detail: { code: "E-BUSY", id: "b9" } });
  await assert.rejects(createApi("", "d").send("s", ["1"]), { code: "E-BUSY", id: "b9" });
});
