import assert from "node:assert/strict";
import test from "node:test";

import { buildCommanderThemeBracketRows } from "./bracketUtils.js";
import { expandThemeReport } from "./themeReportData.js";
import { buildThemeReportRows, prepareThemeReportRows } from "./themeReportUtils.js";

function signal(tag_slug, z, tag_name = tag_slug) {
  return { tag_slug, tag_name, z, tag_decks: 8 };
}

function commander(commander_slug, bracket_tag_rows = []) {
  return {
    commander_slug,
    commander_name: commander_slug,
    total_decks: 200,
    color_identity: ["U", "G"],
    bracket_tag_rows,
  };
}

function themeRow(commander_slug, theme_z, theme_tag_decks = 5) {
  return { commander_slug, theme_z, theme_tag_decks, theme_affinity_pct: 0.15 };
}

test("compact report hydration preserves Theme Brackets classifications and metadata", () => {
  const commanders = [
    commander("cedh-precedence", [signal("cedh", 0.65, "cEDH"), signal("aggro", 4, "Aggro")]),
    commander("cedh-split", [signal("cedh", 1, "cEDH")]),
    commander("archetype-split", [signal("tempo", 1, "Tempo")]),
    commander("archetype-low-split", [signal("control", 0.02, "Control")]),
    commander("tied-signals", [signal("tempo", 2, "Tempo"), signal("combo", 2, "Combo")]),
    commander("no-signals"),
  ];
  const data = {
    commanders,
    themes: [{
      tag_slug: "tokens",
      tag_name: "Tokens",
      rows: commanders.map((row) => themeRow(row.commander_slug, 2)),
    }],
  };
  const snapshot = JSON.stringify(data);
  const groups = expandThemeReport(data);
  const compactResults = prepareThemeReportRows(groups);
  const expected = buildCommanderThemeBracketRows(commanders.map((row) => ({
    ...row,
    theme_tag_slug: "tokens",
    theme_tag_name: "Tokens",
    ...themeRow(row.commander_slug, 2),
  })), "tokens");

  assert.deepEqual(compactResults, expected.map((row) => ({
    ...row,
    id: JSON.stringify([row.theme_tag_slug, row.id]),
  })));
  assert.deepEqual(compactResults.map((row) => row.bracket_key), ["4", "4/5", "2/3", "1/2", "3", "1"]);
  assert.equal(compactResults.find((row) => row.commander_slug === "tied-signals").decision_tag_name, "Tempo");
  assert.equal(JSON.stringify(data), snapshot);
});

test("compact ordinary themes keep their threshold while signal themes allow lower numeric scores", () => {
  const signalSlugs = ["cedh", "aggro", "control", "midrange", "tempo", "combo"];
  const data = {
    commanders: [commander("shared", [signal("cedh", 0.4, "cEDH")])],
    themes: [
      { tag_slug: "tokens", tag_name: "Tokens", rows: [themeRow("shared", 1.04)] },
      { tag_slug: "artifacts", tag_name: "Artifacts", rows: [themeRow("shared", 1.05)] },
      ...signalSlugs.map((tag_slug) => ({ tag_slug, rows: [themeRow("shared", 0.02)] })),
    ],
  };
  const rankedRows = buildThemeReportRows(prepareThemeReportRows(expandThemeReport(data)));

  assert.equal(rankedRows.length, 7);
  assert.ok(rankedRows.every((row) => row.bracket_key === "4"));
  assert.equal(rankedRows.find((row) => row.theme_tag_slug === "tokens"), undefined);
  assert.equal(new Set(rankedRows.map((row) => row.id)).size, 7);
  assert.equal(rankedRows.find((row) => row.theme_tag_slug === "aggro").theme_tag_name, "aggro");
});

test("duplicate theme rows for a commander are consolidated to their highest score", () => {
  const groups = expandThemeReport({
    commanders: [commander("shared")],
    themes: [{
      tag_slug: "tokens", tag_name: "Tokens",
      rows: [themeRow("shared", 2, 8), themeRow("shared", 3, 9)],
    }],
  });
  const rows = buildThemeReportRows(prepareThemeReportRows(groups), { topCount: "10" });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].theme_z, 3);
  assert.equal(rows[0].theme_tag_decks, 9);
});

test("missing commander metadata or bracket data fails instead of silently assigning Bracket 1", () => {
  const themes = [{ tag_slug: "tokens", rows: [themeRow("missing", 2)] }];
  assert.throws(() => expandThemeReport({ commanders: [], themes }), /Missing bracket data for missing/);

  for (const bracket_tag_rows of [undefined, null, {}, ""]) {
    assert.throws(
      () => expandThemeReport({ commanders: [{ commander_slug: "missing", bracket_tag_rows }], themes }),
      /Missing bracket data for missing/
    );
  }
  const rows = prepareThemeReportRows(expandThemeReport({ commanders: [commander("missing")], themes }));
  assert.equal(rows[0].bracket_key, "1");
});

test("invalid compact structures fail and empty themes are retained", () => {
  for (const data of [null, {}, { commanders: [], themes: {} }, { commanders: {}, themes: [] }]) {
    assert.throws(() => expandThemeReport(data), /dataset is invalid/);
  }
  for (const theme of [{ rows: [] }, { tag_slug: "tokens" }, { tag_slug: "tokens", rows: {} }]) {
    assert.throws(() => expandThemeReport({ commanders: [], themes: [theme] }), /invalid theme/);
  }
  const data = { commanders: [], themes: [{ tag_slug: "snow", tag_name: "Snow", rows: [] }] };
  assert.deepEqual(expandThemeReport(data), data.themes);
  assert.deepEqual(expandThemeReport({ commanders: [], themes: [] }), []);
});

test("one commander in multiple themes retains each theme's metrics and separate deciding metrics", () => {
  const groups = expandThemeReport({
    commanders: [{ ...commander("shared", [{ ...signal("cedh", -.2), legacy_z: .4,
      tag_affinity_pct: .03, tag_affinity_adjusted_pct: .025,
      tag_affinity_lower_pct: .01, tag_affinity_upper_pct: .04,
      rank_within_tag_by_z: 19, affinity_model_status: "fitted",
      affinity_model_version: "beta_binomial_v1" }]),
      tag_affinity_pct: .99, tag_affinity_adjusted_pct: .98 }],
    themes: [
      { tag_slug: "tokens", tag_name: "Tokens", rows: [{
        ...themeRow("shared", 1.2), theme_legacy_z: 1.2, theme_affinity_z: 3,
        theme_affinity_pct: .2, theme_affinity_adjusted_pct: .18,
        theme_affinity_lower_pct: .15, theme_affinity_upper_pct: .21,
        theme_rank_within_tag_by_z: 7, theme_affinity_model_status: "fitted",
        theme_affinity_model_version: "beta_binomial_v1",
      }] },
      { tag_slug: "snow", tag_name: "Snow", rows: [{
        ...themeRow("shared", 1.3), theme_legacy_z: 1.3, theme_affinity_z: 88,
        theme_affinity_pct: .1, theme_affinity_adjusted_pct: .09,
        theme_affinity_lower_pct: .08, theme_affinity_upper_pct: .11,
        theme_rank_within_tag_by_z: 1, theme_affinity_model_status: "fitted",
        theme_affinity_model_version: "beta_binomial_v1",
      }] },
      { tag_slug: "lands", tag_name: "Lands", rows: [{
        ...themeRow("shared", 1.4), theme_legacy_z: 1.4, theme_affinity_z: null,
        theme_affinity_pct: .4, theme_affinity_adjusted_pct: null,
        theme_affinity_lower_pct: null, theme_affinity_upper_pct: null,
      }] },
    ],
  });
  const prepared = prepareThemeReportRows(groups);
  const tokens = prepared.find((row) => row.theme_tag_slug === "tokens");
  const snow = prepared.find((row) => row.theme_tag_slug === "snow");
  const lands = prepared.find((row) => row.theme_tag_slug === "lands");
  assert.equal(tokens.theme_z, 3);
  assert.equal(tokens.theme_affinity_adjusted_pct, .18);
  assert.equal(tokens.theme_rank_within_tag_by_z, 7);
  assert.equal(snow.theme_z, 88);
  assert.equal(snow.theme_affinity_adjusted_pct, .09);
  assert.equal(snow.theme_rank_within_tag_by_z, 1);
  assert.equal(lands.theme_z, null);
  assert.equal(lands.theme_affinity_adjusted_pct, null);
  assert.equal(lands.theme_affinity_model_status, null);
  for (const row of prepared) {
    assert.equal(row.bracket_key, "4");
    assert.equal(row.decision_z, .4);
    assert.equal(row.decision_affinity_z, -.2);
    assert.equal(row.decision_tag_affinity_pct, .03);
    assert.equal(row.decision_tag_affinity_adjusted_pct, .025);
    assert.equal(row.decision_tag_affinity_lower_pct, .01);
    assert.equal(row.decision_tag_affinity_upper_pct, .04);
    assert.equal(row.decision_rank_within_tag_by_z, 19);
  }
  assert.equal(buildThemeReportRows(prepared).some((row) => row.theme_tag_slug === "lands"), false);
});
