import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_QUIRKY_BUILDS_FILTERS,
  buildQuirkyBuildsRows,
  prepareQuirkyBuildsRows,
} from "./quirkyBuildsUtils.js";

function build(slug, affinity, overrides = {}) {
  return {
    commander_slug: slug,
    commander_name: slug,
    total_decks: 10000,
    theme_tag_decks: 10,
    theme_affinity_pct: 0.001,
    theme_affinity_adjusted_pct: affinity,
    theme_affinity_z: -0.5,
    theme_affinity_model_status: "fitted",
    bracket_tag_rows: [],
    ...overrides,
  };
}

function theme(slug, rows, name = slug) {
  return { tag_slug: slug, tag_name: name, rows };
}

function tokens(rows) {
  return prepareQuirkyBuildsRows([theme("tokens", rows, "Tokens")]);
}

test("rarity ranks positive, negative and missing z independently of the Theme Report gate", () => {
  const rows = tokens([
    build("highest-z", 0.02, { theme_affinity_z: 4 }),
    build("negative-z", 0.004),
    build("missing-z", 0.002, { theme_affinity_z: null }),
    build("rare-positive-z", 0.001, { theme_affinity_z: 0.66 }),
  ]);
  assert.equal(rows.length, 4);
  const results = buildQuirkyBuildsRows(rows, { topCount: "4" });
  assert.deepEqual(results.map((r) => [r.commander_slug, r.report_rank]), [
    ["rare-positive-z", 1], ["missing-z", 2], ["negative-z", 3], ["highest-z", 4],
  ]);
  assert.equal(results[0].theme_build_rarity, 1000);
  assert.equal(results[1].theme_affinity_z, null);
});

test("editable defaults use 200 total, 5 tagged decks and rarity of at least 1 in 50", () => {
  assert.equal(DEFAULT_QUIRKY_BUILDS_FILTERS.minTotalDecks, "200");
  assert.equal(DEFAULT_QUIRKY_BUILDS_FILTERS.minThemeDecks, "5");
  assert.equal(DEFAULT_QUIRKY_BUILDS_FILTERS.minBuildRarity, "50");
  const rows = tokens([
    build("too-few-total", 0.0001, { total_decks: 199 }),
    build("too-few-tagged", 0.0002, { theme_tag_decks: 4 }),
    build("common", 0.1),
    build("boundary", 0.02, { total_decks: 200, theme_tag_decks: 5 }),
    build("winner", 0.0003),
  ]);
  assert.deepEqual(buildQuirkyBuildsRows(rows, { topCount: "10" }).map((r) => r.commander_slug), ["winner", "boundary"]);
  assert.equal(buildQuirkyBuildsRows(rows, { minTotalDecks: "" })[0].commander_slug, "too-few-total");
  assert.equal(buildQuirkyBuildsRows(rows, { minThemeDecks: "" })[0].commander_slug, "too-few-tagged");
  assert.ok(buildQuirkyBuildsRows(rows, { minBuildRarity: "", topCount: "10" }).some((r) => r.commander_slug === "common"));
});

test("missing, invalid and zero affinity cannot win, even when display floors are cleared", () => {
  const invalid = [null, undefined, "", " ", NaN, Infinity, -0.001, 2, 0, false];
  const rows = tokens([
    ...invalid.map((affinity, index) => build(`invalid-${index}`, affinity)),
    build("raw-only", null, { theme_affinity_pct: 0.000001 }),
    build("zero-tagged", 0.000001, { theme_tag_decks: 0 }),
    build("impossible-counts", 0.000001, { total_decks: 9, theme_tag_decks: 10 }),
    build("unknown-counts", 0.000001, { theme_tag_decks: null }),
    build("valid-fallback", 0.01, { theme_affinity_model_status: "fallback_sparse" }),
  ]);
  assert.deepEqual(buildQuirkyBuildsRows(rows, {
    minTotalDecks: "", minThemeDecks: "", minBuildRarity: "", topCount: "100",
  }).map((r) => r.commander_slug), ["valid-fallback"]);
});

test("filters apply before top N and do not let an excluded winner hide a matching build", () => {
  const rows = tokens([
    build("rarest", 0.0001, { bracket_tag_rows: [{ tag_slug: "cedh", tag_name: "cEDH", z: 2 }] }),
    build("runner-up", 0.001, { commander_name: "The Runner-Up", bracket_tag_rows: [{ tag_slug: "control", tag_name: "Control", z: 2 }] }),
    build("third", 0.002),
  ]);
  assert.equal(buildQuirkyBuildsRows(rows, { brackets: ["3"] })[0].commander_slug, "runner-up");
  assert.equal(buildQuirkyBuildsRows(rows, { commanderQuery: "the runnerup" })[0].commander_slug, "runner-up");
  assert.equal(buildQuirkyBuildsRows(rows, { commanderQuery: "Control" })[0].commander_slug, "runner-up");
  assert.deepEqual(buildQuirkyBuildsRows(rows, { themeQuery: "Blink" }), []);
  assert.equal(buildQuirkyBuildsRows(rows, { themeQuery: "tokens" }).length, 1);
});

test("top counts and ranks are independent per theme and per theme/bracket", () => {
  const rows = prepareQuirkyBuildsRows([
    theme("tokens", [
      build("rare-high", 0.0001, { bracket_tag_rows: [{ tag_slug: "cedh", z: 2 }] }),
      build("second-high", 0.0002, { bracket_tag_rows: [{ tag_slug: "cedh", z: 2 }] }),
      build("rare-low", 0.0003), build("second-low", 0.0004),
    ], "Tokens"),
    theme("artifacts", [build("rare-low", 0.001)], "Artifacts"),
  ]);
  assert.equal(new Set(rows.map((r) => r.id)).size, 5);
  assert.deepEqual(buildQuirkyBuildsRows(rows).map((r) => [r.commander_slug, r.report_rank]), [["rare-low", 1], ["rare-high", 1]]);
  assert.deepEqual(buildQuirkyBuildsRows(rows, { groupBy: "bracket", topCount: "2" }).map((r) => [r.commander_slug, r.report_rank]), [
    ["rare-low", 1], ["rare-high", 1], ["second-high", 2], ["rare-low", 1], ["second-low", 2],
  ]);
});

test("ties use supporting decks and deterministic names, without rounding rarity or mutating input", () => {
  const rows = tokens([
    build("zulu", 0.002, { commander_name: "Zulu" }),
    build("beta", 0.002, { commander_name: "Alpha" }),
    build("alpha", 0.002, { commander_name: "Alpha" }),
    build("more-total", 0.002, { total_decks: 11000 }),
    build("more-tagged", 0.002, { theme_tag_decks: 11 }),
    build("slightly-rarer", 0.00199999),
  ]);
  const original = JSON.stringify(rows);
  const expected = ["slightly-rarer", "more-tagged", "more-total", "alpha", "beta", "zulu"];
  assert.deepEqual(buildQuirkyBuildsRows(rows, { topCount: "10" }).map((r) => r.commander_slug), expected);
  assert.deepEqual(buildQuirkyBuildsRows([...rows].reverse(), { topCount: "10" }).map((r) => r.commander_slug), expected);
  assert.equal(JSON.stringify(rows), original);
  for (const topCount of ["", "invalid", "0", "-5", Infinity]) {
    assert.equal(buildQuirkyBuildsRows(rows, { topCount }).length, 1);
  }
  assert.equal(buildQuirkyBuildsRows(rows, { topCount: "2.8" }).length, 2);
  assert.deepEqual(buildQuirkyBuildsRows([]), []);
});

test("the selected theme keeps its own affinity while cEDH determines the commander bracket", () => {
  const rows = tokens([build("shared", 0.001, {
    theme_affinity_z: -0.5,
    bracket_tag_rows: [{ tag_slug: "cedh", tag_name: "cEDH", z: 2, tag_affinity_adjusted_pct: 0.2 }],
  })]);
  const result = buildQuirkyBuildsRows(rows)[0];
  assert.equal(result.bracket_key, "5");
  assert.equal(result.theme_build_rarity, 1000);
  assert.equal(result.decision_tag_affinity_adjusted_pct, 0.2);
  assert.equal(result.theme_affinity_z, -0.5);
});
