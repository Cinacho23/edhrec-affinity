import assert from "node:assert/strict";
import test from "node:test";

import { watchBuildDiscovery } from "./buildDiscoveryRefresh.js";
import { getCurrentBuildDiscoveryCommanders } from "./buildStyleUtils.js";

const flush = () => new Promise((resolve) => globalThis.setImmediate(resolve));

function visibleDocument() {
  const listeners = new Set();
  return {
    visibilityState: "visible", listeners,
    addEventListener: (name, listener) => { assert.equal(name, "visibilitychange"); listeners.add(listener); },
    removeEventListener: (name, listener) => { assert.equal(name, "visibilitychange"); listeners.delete(listener); },
    change(state) { this.visibilityState = state; for (const listener of listeners) listener(); },
  };
}

function index(count) {
  const commanders = Array.from({ length: count }, (_, i) => ({ commander_slug: `commander-${i}`, total_edhrec_decks: 200 + i }));
  return { commanders, discovery: { policy: "current_collector", min_total_decks: 200, commander_slugs: commanders.map((entry) => entry.commander_slug) } };
}

test("visible page polls new indexes without reloading and preserves its last good list on failure", async () => {
  const document = visibleDocument();
  let tick, reads = 0, next = index(25), failure = false, displayed = [], errors = 0, execution;
  const cleanup = watchBuildDiscovery({
    visibilityDocument: document,
    schedule: (callback, ms) => { tick = callback; assert.equal(ms, 120000); return 42; },
    unschedule: (timer) => assert.equal(timer, 42),
    loadIndex: async () => { reads += 1; if (failure) throw new Error("Temporary failure"); return next; },
    loadExecution: async () => failure ? null : { status: "in_progress" },
    onIndex: (value) => { displayed = getCurrentBuildDiscoveryCommanders(value); },
    onExecution: (value) => { execution = value; }, onIndexError: () => { errors += 1; },
  });
  await flush();
  assert.equal(displayed.length, 25);
  next = index(66);
  tick();
  await flush();
  assert.equal(displayed.length, 66);
  failure = true;
  tick();
  await flush();
  assert.equal(displayed.length, 66);
  assert.equal(errors, 1);
  assert.equal(execution, null);
  document.change("hidden");
  tick();
  await flush();
  assert.equal(reads, 3);
  failure = false;
  document.change("visible");
  await flush();
  assert.equal(reads, 4);
  cleanup();
  tick();
  await flush();
  assert.equal(reads, 4);
  assert.equal(document.listeners.size, 0);
});

test("hidden pages defer reads and cleanup aborts work without publishing late results", async () => {
  const document = visibleDocument();
  document.visibilityState = "hidden";
  let resolveIndex, signal, calls = 0, updates = 0;
  const cleanup = watchBuildDiscovery({
    visibilityDocument: document, schedule: () => 1, unschedule: () => {},
    loadIndex: ({ signal: requestSignal }) => { signal = requestSignal; calls += 1; return new Promise((resolve) => { resolveIndex = resolve; }); },
    loadExecution: async () => null,
    onIndex: () => { updates += 1; }, onExecution: () => {}, onIndexError: () => {},
  });
  assert.equal(calls, 0);
  document.change("visible");
  assert.equal(calls, 1);
  cleanup();
  assert.equal(signal.aborted, true);
  resolveIndex(index(99));
  await flush();
  assert.equal(updates, 0);
  assert.equal(document.listeners.size, 0);
});
