import assert from "node:assert/strict";
import test from "node:test";

import { createQuirkyBuildsDataLoader, expandQuirkyBuildsReport } from "./quirkyBuildsData.js";

const row_fields = [
  "commander_slug", "theme_affinity_z", "theme_tag_decks", "theme_affinity_pct",
  "theme_affinity_adjusted_pct", "theme_affinity_lower_pct", "theme_affinity_upper_pct",
  "theme_affinity_model_status", "theme_affinity_model_version", "theme_rank_within_tag_by_z",
];

function dataset() {
  return {
    schema_version: 1,
    coverage: "all_observed_commander_tags",
    row_fields,
    commanders: [{ commander_slug: "yshtola", commander_name: "Y’shtola", total_decks: 1000,
      bracket_tag_rows: [{ tag_slug: "control", z: 2 }] }],
    themes: [{ tag_slug: "blink", tag_name: "Blink", rows: [
      ["yshtola", -0.6, 5, 0.005, 0.0055, 0.002, 0.011, "fitted", "beta_binomial_v1", 500],
    ] }],
  };
}

test("columnar decoding preserves the selected theme metrics and commander signals", () => {
  const result = expandQuirkyBuildsReport(dataset());
  assert.equal(result[0].rows[0].theme_affinity_z, -0.6);
  assert.equal(result[0].rows[0].theme_affinity_adjusted_pct, 0.0055);
  assert.equal(result[0].rows[0].theme_tag_slug, "blink");
  assert.equal(result[0].rows[0].commander_name, "Y’shtola");
  assert.deepEqual(result[0].rows[0].bracket_tag_rows, [{ tag_slug: "control", z: 2 }]);
  assert.deepEqual(expandQuirkyBuildsReport({ ...dataset(), commanders: [], themes: [] }), []);
});

test("a gated Theme Report, malformed rows or missing signals cannot silently provide quirky rankings", () => {
  for (const data of [
    null,
    { ...dataset(), coverage: "qualified_themes" },
    { ...dataset(), schema_version: 2 },
    { ...dataset(), row_fields: row_fields.slice(1) },
    { ...dataset(), row_fields: [...row_fields, "commander_slug"] },
    { ...dataset(), themes: [{ tag_slug: "blink", rows: [["yshtola"]] }] },
    { ...dataset(), commanders: [] },
    { ...dataset(), commanders: [{ commander_slug: "yshtola" }] },
  ]) {
    assert.throws(() => expandQuirkyBuildsReport(data));
  }
});

test("the loader shares its in-flight report and retries a failed request", async () => {
  let requests = 0;
  const loader = createQuirkyBuildsDataLoader({ loadReport: async () => { requests += 1; return dataset(); } });
  const first = loader.loadReport();
  const second = loader.loadReport();
  assert.equal(first, second);
  assert.deepEqual(await first, await second);
  assert.equal(requests, 1);
  await loader.loadReport();
  assert.equal(requests, 1);

  let attempts = 0;
  const retrying = createQuirkyBuildsDataLoader({ loadReport: async () => {
    attempts += 1;
    if (attempts === 1) throw new Error("temporary failure");
    return dataset();
  } });
  await assert.rejects(retrying.loadReport(), /temporary failure/);
  assert.equal((await retrying.loadReport())[0].rows[0].commander_slug, "yshtola");
  assert.equal(attempts, 2);
});
