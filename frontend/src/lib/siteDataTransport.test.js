import assert from "node:assert/strict";
import test from "node:test";
import { brotliCompressSync, brotliDecompressSync } from "node:zlib";
import { fetchSiteJson } from "./siteDataTransport.js";

const url = "/edhrec-affinity/data/latest/build-styles/commander.json";
const report = {
  commander_slug: "commander", sample_decks: 1000,
  deck_analysis: {
    packages: [{ id: 0, generated_deck: { primer: "[[Éowyn, Shieldmaiden]]", score: 6.71 } }],
    alternative_models: Array.from({ length: 27 }, (_, components) => ({ components, memberships: [0.11, 0.89] })),
  },
};
const decode = (bytes) => brotliDecompressSync(bytes);

test("production loads encoded resources without losing models, deck fields, or precision", async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async (path, options) => {
    assert.equal(path, url + ".br");
    assert.equal(options.signal, controller.signal);
    assert.equal(options.cache, "no-cache");
    return new Response(brotliCompressSync(JSON.stringify(report)));
  });
  assert.deepEqual(await fetchSiteJson(url, { preferBrotli: true, signal: controller.signal, cache: "no-cache", decode }), report);
});

test("plain and compressed deployments work in either preference order", async (t) => {
  for (const preferBrotli of [false, true]) {
    const calls = [];
    const mock = t.mock.method(globalThis, "fetch", async (path) => {
      calls.push(path);
      if (calls.length === 1) return new Response("missing", { status: 404 });
      return path.endsWith(".br")
        ? new Response(brotliCompressSync(JSON.stringify(report)))
        : Response.json(report);
    });
    assert.deepEqual(await fetchSiteJson(url, { preferBrotli, decode }), report);
    assert.deepEqual(calls, preferBrotli ? [url + ".br", url] : [url, url + ".br"]);
    mock.mock.restore();
  }
});

test("optional missing reports and SPA shells remain absent; required missing data errors", async (t) => {
  for (const body of [null, "<!doctype html><html>Shell</html>"]) {
    const mock = t.mock.method(globalThis, "fetch", async () => body === null
      ? new Response("missing", { status: 404 }) : new Response(body));
    assert.equal(await fetchSiteJson(url, { optional: true, preferBrotli: true, decode }), null);
    await assert.rejects(fetchSiteJson(url, { decode }), /Expected JSON/);
    mock.mock.restore();
  }
});

test("a host with automatic Brotli content decoding is not decoded twice", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify(report), { headers: { "content-encoding": "br" } }));
  assert.deepEqual(await fetchSiteJson(url, { preferBrotli: true, decode: () => assert.fail("already decoded by fetch") }), report);
});

test("corruption and service errors are visible rather than silently falling back", async (t) => {
  for (const response of [() => new Response("outage", { status: 503 }), () => new Response("invalid Brotli")]) {
    let calls = 0;
    const mock = t.mock.method(globalThis, "fetch", async () => { calls += 1; return response(); });
    await assert.rejects(fetchSiteJson(url, { optional: true, preferBrotli: true, decode }), /HTTP 503|Failed to decode Brotli/);
    assert.equal(calls, 1);
    mock.mock.restore();
  }
});

test("cancellation propagates without a retry", async (t) => {
  const controller = new AbortController();
  controller.abort();
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (_path, { signal }) => {
    calls += 1;
    signal.throwIfAborted();
  });
  await assert.rejects(fetchSiteJson(url, { signal: controller.signal }), { name: "AbortError" });
  assert.equal(calls, 1);
});
