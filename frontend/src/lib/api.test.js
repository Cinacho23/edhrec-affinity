import assert from "node:assert/strict";
import test from "node:test";

import { loadBuildStyleIndex, loadCommanderBuildStyles, loadThemeBracketDetail, loadThemeReport } from "./api.js";

test("compact report loading rejects legacy-qualified data so complete tags can be used", async () => {
  const previousFetch = globalThis.fetch;

  try {
    const legacyReport = { commanders: [], themes: [] };
    globalThis.fetch = async () => ({
      ok: true,
      text: async () => JSON.stringify(legacyReport),
    });
    await assert.rejects(loadThemeReport(), /outdated qualification score/);

    for (const minimum_z of [undefined, 1.05]) {
      globalThis.fetch = async () => ({
        ok: true,
        text: async () => JSON.stringify({ ...legacyReport, qualification_score_field: "z", minimum_z }),
      });
      await assert.rejects(loadThemeReport(), /outdated qualification score or threshold/);
    }

    const upgradedReport = { ...legacyReport, qualification_score_field: "z", minimum_z: 1 };
    globalThis.fetch = async () => ({
      ok: true,
      text: async () => JSON.stringify(upgradedReport),
    });
    assert.deepEqual(await loadThemeReport(), upgradedReport);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("optional build reports tolerate absent files and static host HTML fallbacks", async () => {
  const previousFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: false, status: 404 });
    assert.equal(await loadCommanderBuildStyles("ghave-guru-of-spores"), null);
    globalThis.fetch = async () => ({ ok: true, text: async () => "  <!doctype html><html>App shell</html>" });
    assert.equal(await loadCommanderBuildStyles("ghave-guru-of-spores"), null);
    globalThis.fetch = async () => ({ ok: false, status: 503 });
    await assert.rejects(loadCommanderBuildStyles("ghave-guru-of-spores"), /HTTP 503/);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("build report loading uses safe per-commander URLs and forwards cancellation", async () => {
  const previousFetch = globalThis.fetch;
  const report = { commander_slug: "ghave-guru-of-spores", profiles: { profiles: [] }, deck_analysis: { packages: [{ id: 0 }] } };
  const controller = new AbortController();
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, "/data/latest/build-styles/ghave-guru-of-spores.json");
      assert.equal(options.signal, controller.signal);
      return { ok: true, text: async () => JSON.stringify(report) };
    };
    assert.deepEqual(await loadCommanderBuildStyles("Ghave Guru of Spores", { signal: controller.signal }), report);
    globalThis.fetch = async () => ({ ok: true, text: async () => JSON.stringify({ ...report, commander_slug: "other" }) });
    await assert.rejects(loadCommanderBuildStyles("ghave-guru-of-spores"), /another commander/);
    globalThis.fetch = async () => ({ ok: true, text: async () => "{broken json" });
    await assert.rejects(loadCommanderBuildStyles("ghave-guru-of-spores"), /Failed to parse JSON/);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("build discovery index tolerates absent deployments and validates real coverage", async () => {
  const previousFetch = globalThis.fetch;
  const emptyCoverage = { schema_version: 1, coverage: "sampled_commanders", total_available: 0, commanders: [] };
  const controller = new AbortController();
  try {
    globalThis.fetch = async () => ({ ok: false, status: 404 });
    assert.deepEqual(await loadBuildStyleIndex(), emptyCoverage);
    globalThis.fetch = async () => ({ ok: true, text: async () => "<!doctype html><html>App shell</html>" });
    assert.deepEqual(await loadBuildStyleIndex(), emptyCoverage);
    const index = { ...emptyCoverage, total_available: 1, commanders: [{ commander_slug: "ghave-guru-of-spores", package_count: 2, unique_decks: 60 }] };
    globalThis.fetch = async (url, options) => {
      assert.equal(url, "/data/latest/build-styles/index.json");
      assert.equal(options.signal, controller.signal);
      return { ok: true, text: async () => JSON.stringify(index) };
    };
    assert.deepEqual(await loadBuildStyleIndex({ signal: controller.signal }), index);
    for (const invalid of [{ commanders: [] }, { schema_version: 1, commanders: [null] }, { schema_version: 1, commanders: [{ commander_slug: "" }] }]) {
      globalThis.fetch = async () => ({ ok: true, text: async () => JSON.stringify(invalid) });
      await assert.rejects(loadBuildStyleIndex(), /coverage index is invalid/);
    }
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("compact theme loading rejects old empty lists and unwraps upgraded qualification files", async () => {
  const previousFetch = globalThis.fetch;

  try {
    globalThis.fetch = async () => ({ ok: true, text: async () => "[]" });
    await assert.rejects(loadThemeBracketDetail("tokens"), /outdated qualification score/);

    const eligible = { commander_slug: "newly-eligible", theme_affinity_z: 1.4 };
    for (const rows of [[], [eligible]]) {
      for (const minimum_z of [undefined, 1.05]) {
        globalThis.fetch = async () => ({
          ok: true,
          text: async () => JSON.stringify({ qualification_score_field: "z", minimum_z, rows }),
        });
        await assert.rejects(loadThemeBracketDetail("tokens"), /outdated qualification score or threshold/);
      }
      globalThis.fetch = async () => ({
        ok: true,
        text: async () => JSON.stringify({ qualification_score_field: "z", minimum_z: 1, rows }),
      });
      assert.deepEqual(await loadThemeBracketDetail("tokens"), rows);
    }
  } finally {
    globalThis.fetch = previousFetch;
  }
});
