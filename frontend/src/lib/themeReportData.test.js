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
