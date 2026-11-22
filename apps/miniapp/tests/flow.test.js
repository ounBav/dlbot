import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { createApi } from "../src/api.js";
import {
  absoluteUrl, defaultQuality, formatBytes, formatDuration, jobErrorCode, lowerQuality, percent,
  retryDelay, screenFor,
} from "../src/logic.js";
import { errorText, S } from "../src/strings.js";

test("defaultQuality picks the highest at or below 720p", () => {
  assert.equal(defaultQuality([360, 480, 720, 1080]), 720);
  assert.equal(defaultQuality([1080, 360, 480]), 480);
  assert.equal(defaultQuality([1080]), 1080);
  assert.equal(defaultQuality([]), null);
  assert.equal(defaultQuality(undefined), null);
});

test("lowerQuality steps down one rung for 'file too large'", () => {
  assert.equal(lowerQuality([360, 480, 720, 1080], 1080), 720);
  assert.equal(lowerQuality([360, 480, 720], 480), 360);
  assert.equal(lowerQuality([360, 480], 360), null);
  assert.equal(lowerQuality(undefined, 720), null);
});

test("formatDuration and formatBytes", () => {
  assert.equal(formatDuration(0), "0:00");
  assert.equal(formatDuration(212), "3:32");
  assert.equal(formatDuration(3725), "1:02:05");
  assert.equal(formatDuration("junk"), "0:00");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(2048), "2 KB");
  assert.equal(formatBytes(5 * 1024 * 1024 + 300_000), "5.3 MB");
  assert.equal(formatBytes(48 * 1024 * 1024), "48 MB");
});

test("percent clamps and tolerates missing progress", () => {
  assert.equal(percent(0.427), 42);
  assert.equal(percent(1.2), 100);
  assert.equal(percent(-1), 0);
  assert.equal(percent(null), null);
  assert.equal(percent(undefined), null);
  assert.equal(percent("x"), null);
});

test("retryDelay backs off exponentially with a cap", () => {
  assert.deepEqual([0, 1, 2, 3, 4, 10].map(retryDelay), [1500, 3000, 6000, 12000, 12000, 12000]);
});

test("absoluteUrl joins the API base and a server path", () => {
  assert.equal(absoluteUrl("https://api.example/", "/api/files/t"), "https://api.example/api/files/t");
  assert.equal(absoluteUrl("", "/api/files/t", "https://app.example"), "https://app.example/api/files/t");
  assert.equal(absoluteUrl("https://api.example", "api/files/t"), "https://api.example/api/files/t");
});

test("screenFor routes a job to the right screen", () => {
  assert.equal(screenFor(null), "home");
  for (const s of ["queued", "downloading", "processing", "uploading", "retrying", "cancelling"]) {
    assert.equal(screenFor({ status: s }), "progress", s);
  }
  assert.equal(screenFor({ status: "done" }), "ready");
  assert.equal(screenFor({ status: "failed" }), "error");
  assert.equal(screenFor({ status: "expired" }), "error");
  assert.equal(screenFor({ status: "cancelled" }), "home");
});

test("jobErrorCode covers expired jobs and expired links", () => {
  assert.equal(jobErrorCode({ status: "failed", error_code: "E-TOOBIG" }), "E-TOOBIG");
  assert.equal(jobErrorCode({ status: "failed" }), "E-FAILED");
  assert.equal(jobErrorCode({ status: "expired" }), "E-EXPIRED");
  assert.equal(jobErrorCode({ status: "done", delivery: "device", file: null }), "E-GONE");
});

test("every server error code has its own message", () => {
  const generic = S.errors["E-FAILED"];
  for (const code of ["E-BUSY", "E-LIVE", "E-PLAYLIST", "E-TOOLONG", "E-BLOCKED", "E-GEO", "E-UNAVAILABLE", "E-GONE"]) {
    assert.notEqual(errorText(code), generic, code);
  }
  assert.match(errorText("E-TOOBIG-Q", { maxMb: 50 }), /50 MB/);
  assert.equal(S.downloadVideo(720), "Download MP4 · 720p");
  assert.equal(S.downloadAudio, "Download MP3");
});

// ---------------------------------------------------------------- API client

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

function mockFetch(status, body, calls = []) {
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  };
  return calls;
}

test("info posts the link", async () => {
  const calls = mockFetch(200, { title: "x", qualities: [360] });
  const res = await createApi("https://api.example", "d").info("https://youtu.be/a");
  assert.equal(res.title, "x");
  assert.equal(calls[0].url, "https://api.example/api/info");
  assert.deepEqual(JSON.parse(calls[0].init.body), { url: "https://youtu.be/a" });
});

test("create sends quality and delivery only when given", async () => {
  const calls = mockFetch(200, { id: "j", status: "queued", cached: false });
  const api = createApi("", "d");
  await api.create("https://youtu.be/a", "video", { quality: 480, delivery: "device" });
  await api.create("https://youtu.be/a", "audio", { delivery: "chat" });
  assert.deepEqual(JSON.parse(calls[0].init.body),
    { url: "https://youtu.be/a", format: "video", quality: 480, delivery: "device" });
  assert.deepEqual(JSON.parse(calls[1].init.body), { url: "https://youtu.be/a", format: "audio", delivery: "chat" });
});

test("get fetches one job by id", async () => {
  const calls = mockFetch(200, { id: "a/b", status: "queued" });
  await createApi("", "d").get("a/b");
  assert.equal(calls[0].url, "/api/downloads/a%2Fb");
});

test("E-BUSY carries the running job id", async () => {
  mockFetch(409, { detail: { code: "E-BUSY", id: "job-1" } });
  await assert.rejects(createApi("", "d").create("https://youtu.be/a", "video"),
    (err) => err.code === "E-BUSY" && err.id === "job-1" && err.status === 409);
});
