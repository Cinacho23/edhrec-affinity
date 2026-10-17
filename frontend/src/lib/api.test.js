import assert from "node:assert/strict";
import test from "node:test";

import { loadThemeBracketDetail, loadThemeReport } from "./api.js";

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
