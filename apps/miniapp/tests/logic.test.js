import assert from "node:assert/strict";
import { test } from "node:test";

import {
  detectPlatform, extractUrl, formatWait, isFinal, mergeItems, pollDelay,
  relativeTime, shortUrl, stepIndex, toProblem,
} from "../src/logic.js";
import { errorText, S } from "../src/strings.js";

// Same shape GET /api/me returns.
const PLATFORMS = [
  { key: "youtube", label: "YouTube", hosts: ["m.youtube.com", "www.youtube.com", "youtu.be", "youtube.com"] },
  { key: "tiktok", label: "TikTok", hosts: ["m.tiktok.com", "tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "www.tiktok.com"] },
  { key: "facebook", label: "Facebook", hosts: ["facebook.com", "fb.watch", "m.facebook.com", "web.facebook.com", "www.facebook.com"] },
];

test("extractUrl finds the link inside shared text and trims trailing punctuation", () => {
  assert.equal(extractUrl("watch this https://youtu.be/abc123!"), "https://youtu.be/abc123");
  assert.equal(extractUrl("(https://vm.tiktok.com/ZM123/)."), "https://vm.tiktok.com/ZM123/");
  assert.equal(extractUrl("no link here"), null);
  assert.equal(extractUrl(""), null);
  assert.equal(extractUrl(undefined), null);
});

test("detectPlatform uses the server allow-list by exact host", () => {
  assert.equal(detectPlatform("https://youtu.be/x", PLATFORMS).key, "youtube");
  assert.equal(detectPlatform("https://VM.TIKTOK.COM/x", PLATFORMS).key, "tiktok");
  assert.equal(detectPlatform("https://fb.watch/x", PLATFORMS).key, "facebook");
  assert.equal(detectPlatform("https://youtube.com.evil.example/x", PLATFORMS), null);
  assert.equal(detectPlatform("https://evil.example/?u=youtube.com", PLATFORMS), null);
  assert.equal(detectPlatform("not a url", PLATFORMS), null);
  assert.equal(detectPlatform("https://youtu.be/x", undefined), null); // before /me loads
});

test("final states stop polling; live states do not", () => {
  for (const s of ["done", "failed", "cancelled", "expired"]) assert.ok(isFinal(s), s);
  for (const s of ["queued", "downloading", "uploading", "retrying", "cancelling"]) assert.ok(!isFinal(s), s);
});

test("stepIndex maps stages to the progress bar", () => {
  assert.equal(stepIndex("queued"), 0);
  assert.equal(stepIndex("downloading"), 1);
  assert.equal(stepIndex("retrying"), 1);
  assert.equal(stepIndex("uploading"), 2);
  assert.equal(stepIndex("done"), 3);
  assert.equal(stepIndex("weird"), 0);
});

test("pollDelay backs off as a job ages", () => {
  assert.equal(pollDelay(0), 1500);
  assert.equal(pollDelay(30_000), 3000);
  assert.equal(pollDelay(10 * 60_000), 6000);
});

test("shortUrl drops scheme and www and truncates", () => {
  assert.equal(shortUrl("https://www.youtube.com/watch?v=1"), "youtube.com/watch");
  assert.equal(shortUrl("https://youtu.be/abcdef", 10), "youtu.be/…");
  assert.equal(shortUrl("garbage"), "garbage");
});

test("relativeTime", () => {
  const now = Date.parse("2026-11-22T12:00:00Z");
  assert.equal(relativeTime("2026-11-22T11:59:50Z", now), "just now");
  assert.equal(relativeTime("2026-11-22T11:55:00Z", now), "5 min ago");
  assert.equal(relativeTime("2026-11-22T09:00:00Z", now), "3 h ago");
  assert.equal(relativeTime("2026-11-21T10:00:00Z", now), "yesterday");
  assert.equal(relativeTime("2026-11-18T12:00:00Z", now), "4 days ago");
  assert.equal(relativeTime("nope", now), "");
});

test("formatWait", () => {
  assert.equal(formatWait(42), "42s");
  assert.equal(formatWait(41.2), "42s");
  assert.equal(formatWait(125), "2:05");
});

test("toProblem keeps API problems and maps anything else to E-NETWORK", () => {
  assert.deepEqual(toProblem({ code: "E-AUTH", status: 401, retryAfter: 0 }).code, "E-AUTH");
  assert.equal(toProblem(new TypeError("Failed to fetch")).code, "E-NETWORK");
  assert.equal(toProblem(undefined).code, "E-NETWORK");
});

test("mergeItems keeps an optimistic cancelling flag only until the job is final", () => {
  const items = [{ id: "a", status: "downloading" }, { id: "b", status: "cancelled" }];
  const merged = mergeItems(items, new Set(["a", "b"]));
  assert.equal(merged[0].status, "cancelling");
  assert.equal(merged[1].status, "cancelled");
});

test("errorText covers every server code with the bot's wording", () => {
  for (const code of ["E-UNSUPPORTED", "E-PRIVATE", "E-TOOBIG", "E-FAILED", "E-NOCHAT", "E-NETWORK"]) {
    assert.ok(errorText(code).length > 10, code);
  }
  assert.match(errorText("E-TOOBIG", { maxMb: 50 }), /50 MB/);
  assert.match(errorText("E-RATELIMIT", { retryAfter: 30, formatWait }), /30s/);
  assert.equal(errorText("E-RATELIMIT", { retryAfter: 40000, formatWait }), S.errors["E-DAILY"]);
  assert.equal(errorText("E-SOMETHING-NEW"), S.errors["E-FAILED"]);
});

test("every stage the API can return has a status label", () => {
  for (const s of ["queued", "downloading", "retrying", "uploading", "cancelling", "done", "failed", "cancelled", "expired"]) {
    assert.equal(typeof S.status[s], "string", s);
  }
});

test("toggleAll selects everything up to the cap, then clears", async () => {
  const { toggleAll } = await import("../src/logic.js");
  const ids = ["a", "b", "c"];
  assert.deepEqual([...toggleAll(ids, new Set())], ["a", "b", "c"]);
  assert.deepEqual([...toggleAll(ids, new Set(["a"]))], ["a", "b", "c"]);   // partial -> all
  assert.equal(toggleAll(ids, new Set(ids)).size, 0);                        // all -> none
  assert.deepEqual([...toggleAll(ids, new Set(), 2)], ["a", "b"]);
  assert.equal(toggleAll(ids, new Set(["a", "b"]), 2).size, 0);
  assert.equal(toggleAll([], new Set()).size, 0);
});

test("waveCount and batchPercent", async () => {
  const { batchPercent, waveCount } = await import("../src/logic.js");
  assert.deepEqual([0, 1, 10, 11, 25].map((n) => waveCount(n, 10)), [0, 1, 1, 2, 3]);
  assert.equal(waveCount(5, 0), 5);
  assert.equal(batchPercent({ total: 20, done: 8, failed: 1, cancelled: 1 }), 50);
  assert.equal(batchPercent(null), 0);
});
