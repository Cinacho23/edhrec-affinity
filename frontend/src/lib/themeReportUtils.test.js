import assert from "node:assert/strict";
import test from "node:test";

import { BRACKET_OPTIONS } from "./bracketUtils.js";
import {
  DEFAULT_THEME_REPORT_FILTERS,
  buildThemeReportRows,
  prepareThemeReportRows,
} from "./themeReportUtils.js";

function signal(tag_slug, z, tag_name = tag_slug) {
  return { tag_slug, tag_name, z };
}

function commander(commander_slug, z, overrides = {}) {
  return {
    commander_slug,
    commander_name: commander_slug,
    theme_tag_slug: "tokens",
    theme_tag_name: "Tokens",
    theme_z: z,
    theme_tag_decks: 5,
    total_decks: 200,
    bracket_tag_rows: [],
    ...overrides,
  };
}

function theme(tag_slug, rows, tag_name = tag_slug) {
  return { tag_slug, tag_name, rows };
}

function tokens(rows) {
  return prepareThemeReportRows([theme("tokens", rows, "Tokens")]);
}

test("defaults select one winner per theme with at least 200 total and 5 theme decks", () => {
  assert.deepEqual(DEFAULT_THEME_REPORT_FILTERS, {
    themeQuery: "",
    commanderQuery: "",
    brackets: [],
    minTotalDecks: "200",
    minThemeDecks: "5",
    groupBy: "theme",
    topCount: "1",
  });
  const rows = tokens([
    commander("too-few-total", 5, { total_decks: 199 }),
    commander("too-few-theme", 4, { theme_tag_decks: 4 }),
    commander("winner", 3),
    commander("runner-up", 2),
  ]);

  assert.deepEqual(
    buildThemeReportRows(rows).map((row) => [row.commander_slug, row.report_rank]),
    [["winner", 1]]
  );
  assert.equal(buildThemeReportRows(rows, { minTotalDecks: "" })[0].commander_slug, "too-few-total");
  assert.equal(buildThemeReportRows(rows, { minThemeDecks: "" })[0].commander_slug, "too-few-theme");
});

test("preparation enforces ordinary-theme eligibility and allows all six signal-theme exceptions", () => {
  const ordinaryRows = tokens([
    commander("qualifies", 1.05),
    commander("below-threshold", 1.049),
    commander("missing-score", null),
  ]);
  assert.deepEqual(ordinaryRows.map((row) => row.commander_slug), ["qualifies"]);

  const signalThemes = ["cedh", "aggro", "control", "midrange", "tempo", "combo"];
  const rows = prepareThemeReportRows(
    signalThemes.map((slug) =>
      theme(slug, [
        commander(slug, 0.02, {
          theme_tag_slug: slug,
          theme_tag_name: slug,
          bracket_tag_rows: [signal(slug, 0.02)],
        }),
      ])
    )
  );
  assert.equal(buildThemeReportRows(rows).length, 6);
  assert.equal(rows.find((row) => row.theme_tag_slug === "cedh").bracket_key, "1");
  assert.ok(rows.filter((row) => row.theme_tag_slug !== "cedh").every((row) => row.bracket_key === "1/2"));
});

test("preparation preserves all bracket boundaries and cEDH precedence", () => {
  const examples = [
    ["cedh-high", "cedh", 1.05, "5"],
    ["cedh-split", "cedh", 1, "4/5"],
    ["cedh-upper", "cedh", 0.95, "4"],
    ["cedh-lower", "cedh", 0.05, "4"],
    ["archetype-high", "combo", 1.05, "3"],
    ["archetype-split", "combo", 1, "2/3"],
    ["archetype-upper", "combo", 0.95, "2"],
    ["archetype-lower", "combo", 0.05, "2"],
    ["archetype-low-split", "combo", 0.02, "1/2"],
    ["archetype-zero", "combo", 0, "1"],
  ];
  const rows = tokens(
    examples.map(([name, slug, z]) =>
      commander(name, 2, {
        bracket_tag_rows: [
          signal(slug, z),
          ...(slug === "cedh" ? [signal("aggro", 10)] : []),
        ],
      })
    )
  );
  for (const [name, , , expectedBracket] of examples) {
    assert.equal(rows.find((row) => row.commander_slug === name).bracket_key, expectedBracket);
  }
  for (const bracket of BRACKET_OPTIONS) {
    const result = buildThemeReportRows(rows, { brackets: [bracket.key], topCount: "10" });
    assert.ok(result.length > 0);
    assert.ok(result.every((row) => row.bracket_key === bracket.key));
  }
});

test("filters commanders and deciding tags before selecting winners", () => {
  const rows = tokens([
    commander("overall-winner", 5, { bracket_tag_rows: [signal("cedh", 2, "cEDH")] }),
    commander("the-runner-up", 4, { commander_name: "The Runner-Up", bracket_tag_rows: [signal("midrange", 1.1, "Midrange")] }),
    commander("third-place", 3, { bracket_tag_rows: [signal("midrange", 1.2, "Midrange")] }),
  ]);
  assert.equal(buildThemeReportRows(rows, { commanderQuery: "the runnerup" })[0].commander_slug, "the-runner-up");
  assert.equal(buildThemeReportRows(rows, { commanderQuery: "MID RANGE" })[0].commander_slug, "the-runner-up");
  assert.equal(buildThemeReportRows(rows, { brackets: ["3"] })[0].commander_slug, "the-runner-up");
  assert.equal(buildThemeReportRows(rows, { themeQuery: "toke-ns" }).length, 1);
  assert.deepEqual(buildThemeReportRows(rows, { themeQuery: "mutate" }), []);
  assert.deepEqual(buildThemeReportRows(rows, { commanderQuery: "unknown" }), []);
});

test("ranks top N independently per theme and per bracket, and displays themes alphabetically", () => {
  const rows = prepareThemeReportRows([
    theme("tokens", [
      commander("top-high", 5, { bracket_tag_rows: [signal("cedh", 2)] }),
      commander("second-high", 4, { bracket_tag_rows: [signal("cedh", 2)] }),
      commander("third-high", 3, { bracket_tag_rows: [signal("cedh", 2)] }),
      commander("top-low", 2),
      commander("second-low", 1.5),
      commander("third-low", 1.2),
    ], "Tokens"),
    theme("artifacts", [commander("artifact-winner", 1.1, {
      theme_tag_slug: "artifacts", theme_tag_name: "Artifacts",
    })], "Artifacts"),
  ]);
  assert.deepEqual(
    buildThemeReportRows(rows, { topCount: "2" }).map((row) => [row.commander_slug, row.report_rank]),
    [["artifact-winner", 1], ["top-high", 1], ["second-high", 2]]
  );
  assert.deepEqual(
    buildThemeReportRows(rows, { groupBy: "bracket", topCount: "2" }).map((row) => [row.commander_slug, row.report_rank]),
    [["artifact-winner", 1], ["top-high", 1], ["second-high", 2], ["top-low", 1], ["second-low", 2]]
  );
  assert.equal(buildThemeReportRows(rows, { groupBy: "bracket", brackets: ["5", "1"] }).length, 3);
  assert.equal(buildThemeReportRows(rows, { groupBy: "bracket", brackets: ["2/3", "4/5"] }).length, 0);
});

test("only finite theme z-scores are ranked, including zero and negative signal-theme scores", () => {
  const rows = prepareThemeReportRows([
    theme("aggro", [null, undefined, "", "invalid", Infinity, -Infinity, 0, -0.5, "0.5"].map((z, index) =>
      commander(`commander-${index}`, z, { theme_tag_slug: "aggro", theme_tag_name: "Aggro" })
    )),
  ]);
  assert.equal(rows.length, 9);
  assert.deepEqual(buildThemeReportRows(rows, { topCount: "10" }).map((row) => row.theme_z), [0.5, 0, -0.5]);
});

test("breaks tied scores by theme decks, total decks, commander name, then slug", () => {
  const rows = tokens([
    commander("last-by-name", 3, { commander_name: "Zulu" }),
    commander("zulu-slug", 3, { commander_name: "Alpha" }),
    commander("alpha-slug", 3, { commander_name: "Alpha" }),
    commander("more-total", 3, { total_decks: 201 }),
    commander("more-theme", 3, { theme_tag_decks: 6 }),
    commander("higher-score", 3.1),
  ]);
  const snapshot = JSON.stringify(rows);
  const expected = ["higher-score", "more-theme", "more-total", "alpha-slug", "zulu-slug", "last-by-name"];
  assert.deepEqual(buildThemeReportRows(rows, { topCount: "10" }).map((row) => row.commander_slug), expected);
  assert.deepEqual(buildThemeReportRows([...rows].reverse(), { topCount: "10" }).map((row) => row.commander_slug), expected);
  assert.equal(JSON.stringify(rows), snapshot);
  assert.ok(rows.every((row) => row.report_rank === undefined));
});

test("prepares flat tag data and uses distinct identities when a commander ranks in multiple themes", () => {
  const rows = prepareThemeReportRows([
    theme("tokens", [
      { commander_slug: "shared", commander_name: "Shared", total_decks: 200, tag_decks: 5, ...signal("tokens", 3, "Tokens") },
      { commander_slug: "shared", ...signal("combo", 1.1, "Combo") },
    ], "Tokens"),
    theme("artifacts", [commander("shared", 2, {
      theme_tag_slug: "artifacts", theme_tag_name: "Artifacts",
    })], "Artifacts"),
  ]);
  assert.equal(rows.length, 2);
  assert.equal(new Set(rows.map((row) => row.id)).size, 2);
  assert.equal(rows.find((row) => row.theme_tag_slug === "tokens").bracket_key, "3");
  assert.equal(buildThemeReportRows(rows).length, 2);
});

test("handles empty reports and normalizes invalid or fractional top counts", () => {
  assert.deepEqual(prepareThemeReportRows([]), []);
  assert.deepEqual(buildThemeReportRows([]), []);
  const rows = tokens([commander("alpha", 4), commander("beta", 3), commander("gamma", 2)]);
  for (const topCount of ["", "invalid", "0", "-5", Infinity]) {
    assert.equal(buildThemeReportRows(rows, { topCount }).length, 1);
  }
  assert.equal(buildThemeReportRows(rows, { topCount: "2.8" }).length, 2);
  assert.equal(buildThemeReportRows(rows, { minTotalDecks: "201" }).length, 0);
  assert.equal(buildThemeReportRows(rows, { minThemeDecks: "6" }).length, 0);
});

test("report ranks upgraded affinity while keeping legacy theme gates and brackets", () => {
  const prepared = tokens([
    commander("highest-new", 3, {
      theme_legacy_z: 1.1,
      bracket_tag_rows: [{ ...signal("combo", -2), legacy_z: 1.05 }],
    }),
    commander("highest-old", 1, {
      theme_legacy_z: 4,
      bracket_tag_rows: [{ ...signal("cedh", -2), legacy_z: 0.4 }],
    }),
    commander("excluded-by-old-gate", 9, { theme_legacy_z: 1.04 }),
  ]);
  assert.equal(prepared.length, 2);
  assert.equal(prepared.find((row) => row.commander_slug === "highest-new").bracket_key, "3");
  assert.equal(prepared.find((row) => row.commander_slug === "highest-old").bracket_key, "4");
  const report = buildThemeReportRows(prepared);
  assert.equal(report[0].commander_slug, "highest-new");
  assert.equal(report[0].theme_z, 3);
});
