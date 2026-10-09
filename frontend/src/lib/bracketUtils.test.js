import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCommanderBracketRows,
  buildCommanderThemeBracketRows,
  classifyCommanderRows,
} from "./bracketUtils.js";

function tag(tag_slug, z, tag_name = tag_slug) {
  return { tag_slug, tag_name, z };
}

test("classifies exact cEDH boundaries and the 4/5 flux band", () => {
  assert.equal(classifyCommanderRows([tag("cedh", 1.05, "cEDH")]).bracket_key, "5");
  assert.equal(classifyCommanderRows([tag("cedh", 1.01, "cEDH")]).bracket_key, "4/5");
  assert.equal(classifyCommanderRows([tag("cedh", 0.95, "cEDH")]).bracket_key, "4");
  assert.equal(classifyCommanderRows([tag("cedh", 0.05, "cEDH")]).bracket_key, "4");
});

test("gives a qualifying cEDH score precedence over a stronger archetype", () => {
  const classification = classifyCommanderRows([
    { ...tag("cedh", 0.65, "cEDH"), tag_decks: 8 },
    tag("midrange", 1.12, "Midrange"),
  ]);

  assert.equal(classification.bracket_key, "4");
  assert.equal(classification.decision_tag_name, "cEDH");
  assert.equal(classification.decision_z, 0.65);
  assert.equal(classification.decision_tag_decks, 8);
});

test("classifies archetype boundaries and both requested flux examples", () => {
  assert.equal(classifyCommanderRows([tag("control", 1.05, "Control")]).bracket_key, "3");
  assert.equal(classifyCommanderRows([tag("midrange", 0.98, "Midrange")]).bracket_key, "2/3");
  assert.equal(classifyCommanderRows([tag("tempo", 0.95, "Tempo")]).bracket_key, "2");
  assert.equal(classifyCommanderRows([tag("control", 0.02, "Control")]).bracket_key, "1/2");
});

test("uses the strongest qualifying archetype and otherwise falls back to Bracket 1", () => {
  const strong = classifyCommanderRows([
    tag("aggro", 0.4, "Aggro"),
    tag("combo", 1.2, "Combo"),
    tag("unrelated", 9, "Unrelated"),
  ]);

  assert.equal(strong.bracket_key, "3");
  assert.equal(strong.decision_tag_name, "Combo");
  assert.equal(classifyCommanderRows([tag("control", 0, "Control")]).bracket_key, "1");
  assert.equal(classifyCommanderRows([tag("cedh", 0.02, "cEDH")]).bracket_key, "1");
  assert.equal(classifyCommanderRows([]).bracket_key, "1");
});

test("groups tag rows into one result per commander", () => {
  const rows = buildCommanderBracketRows([
    { commander_slug: "alpha", commander_name: "Alpha", ...tag("aggro", 0.4, "Aggro"), tag_decks: 20 },
    { commander_slug: "alpha", commander_name: "Alpha", ...tag("combo", 1.1, "Combo"), tag_decks: 12 },
    { commander_slug: "beta", commander_name: "Beta", ...tag("tempo", -0.4, "Tempo") },
  ]);

  assert.equal(rows.length, 2);
  assert.equal(rows.find((row) => row.commander_slug === "alpha").bracket_key, "3");
  assert.equal(rows.find((row) => row.commander_slug === "alpha").decision_tag_decks, 12);
  assert.equal(rows.find((row) => row.commander_slug === "beta").bracket_key, "1");
});

test("requires both the selected theme and a Bracket 3 archetype at 1.05", () => {
  const rows = buildCommanderThemeBracketRows(
    [
      {
        commander_slug: "qualifies",
        commander_name: "Qualifies",
        tag_slug: "mutate",
        tag_name: "Mutate",
        z: 1.05,
        tag_decks: 12,
      },
      {
        commander_slug: "qualifies",
        commander_name: "Qualifies",
        ...tag("combo", 1.05, "Combo"),
      },
      {
        commander_slug: "weak-theme",
        commander_name: "Weak Theme",
        ...tag("mutate", 1.04, "Mutate"),
      },
      {
        commander_slug: "weak-theme",
        commander_name: "Weak Theme",
        ...tag("aggro", 4.2, "Aggro"),
      },
      {
        commander_slug: "weak-archetype",
        commander_name: "Weak Archetype",
        ...tag("mutate", 2.4, "Mutate"),
      },
      {
        commander_slug: "weak-archetype",
        commander_name: "Weak Archetype",
        ...tag("tempo", 1.04, "Tempo"),
      },
    ],
    "mutate"
  );

  assert.equal(rows.length, 2);
  assert.equal(rows.find((row) => row.id === "qualifies").bracket_key, "3");
  assert.equal(rows.find((row) => row.id === "qualifies").theme_z, 1.05);
  assert.equal(rows.find((row) => row.id === "qualifies").theme_tag_decks, 12);
  assert.equal(rows.find((row) => row.id === "weak-archetype").bracket_key, "2/3");
});

test("classifies compact theme export rows with the shared cEDH precedence", () => {
  const rows = buildCommanderThemeBracketRows(
    [
      {
        commander_slug: "nested",
        commander_name: "Nested",
        theme_tag_slug: "mutate",
        theme_tag_name: "Mutate",
        theme_z: 2.2,
        theme_tag_decks: 18,
        bracket_tag_rows: [
          tag("cedh", 0.4, "cEDH"),
          tag("midrange", 3.8, "Midrange"),
        ],
      },
    ],
    "Mutate"
  );

  assert.equal(rows.length, 1);
  assert.equal(rows[0].bracket_key, "4");
  assert.equal(rows[0].decision_tag_name, "cEDH");
  assert.equal(rows[0].theme_z, 2.2);
  assert.equal("bracket_tag_rows" in rows[0], false);
});

test("uses bracket rules without a second 1.05 gate for bracket-signal themes", () => {
  const aggroRows = buildCommanderThemeBracketRows(
    [
      {
        commander_slug: "aggro-two",
        commander_name: "Aggro Two",
        ...tag("aggro", 0.4, "Aggro"),
      },
      {
        commander_slug: "aggro-one-two",
        commander_name: "Aggro One Two",
        ...tag("aggro", 0.02, "Aggro"),
      },
      {
        commander_slug: "aggro-one",
        commander_name: "Aggro One",
        ...tag("aggro", 0, "Aggro"),
      },
    ],
    "aggro"
  );
  const cedhRows = buildCommanderThemeBracketRows(
    [
      {
        commander_slug: "cedh-four",
        commander_name: "cEDH Four",
        ...tag("cedh", 0.4, "cEDH"),
      },
      {
        commander_slug: "cedh-five",
        commander_name: "cEDH Five",
        ...tag("cedh", 1.05, "cEDH"),
      },
    ],
    "cedh"
  );

  assert.deepEqual(
    aggroRows.map((row) => row.bracket_key),
    ["2", "1/2", "1"]
  );
  assert.deepEqual(
    cedhRows.map((row) => row.bracket_key),
    ["4", "5"]
  );
});

test("affinity upgrades preserve every legacy bracket boundary and missing signal", () => {
  for (const slug of ["cedh", "combo"]) {
    for (const score of [null, -2, 0, 0.02, 0.05, 0.95, 0.98, 1.05, 2]) {
      const oldRow = { ...tag(slug, score), tag_decks: 17 };
      const upgraded = { ...oldRow, z: 9, legacy_z: score };
      const { decision_affinity_z: newAffinity, ...newClassification } = classifyCommanderRows([upgraded]);
      const { decision_affinity_z: oldAffinity, ...oldClassification } = classifyCommanderRows([oldRow]);
      assert.deepEqual(newClassification, oldClassification);
      assert.equal(newAffinity, newClassification.decision_tag_slug ? 9 : null);
      assert.equal(oldAffinity, oldClassification.decision_tag_slug ? score : null);
    }
  }
});

function ownMetrics(row) {
  return Object.fromEntries(Object.entries(row).filter(([key]) =>
    key.startsWith("decision_") || key.startsWith("theme_") || key.startsWith("bracket_")
  ));
}

test("deciding metrics follow the selected tag instead of the commander's first row", () => {
  const identity = { commander_slug: "alpha", commander_name: "Alpha" };
  const rows = [
    { ...identity, ...tag("tokens", 88), legacy_z: 2, tag_affinity_pct: .9,
      tag_affinity_adjusted_pct: .89, rank_within_tag_by_z: 1 },
    { ...identity, ...tag("combo", 3), legacy_z: 1.2, tag_affinity_pct: .2,
      tag_affinity_adjusted_pct: .18, tag_affinity_lower_pct: .15,
      tag_affinity_upper_pct: .21, rank_within_tag_by_z: 7,
      affinity_model_status: "fitted", affinity_model_version: "beta_binomial_v1" },
    { ...identity, ...tag("cedh", -.2), legacy_z: .4, tag_affinity_pct: .03,
      tag_affinity_adjusted_pct: .025, tag_affinity_lower_pct: .01,
      tag_affinity_upper_pct: .04, rank_within_tag_by_z: 19,
      affinity_model_status: "fitted", affinity_model_version: "beta_binomial_v1" },
  ];
  const decision = buildCommanderBracketRows(rows)[0];
  assert.equal(decision.bracket_key, "4");
  assert.equal(decision.decision_tag_slug, "cedh");
  assert.equal(decision.decision_z, .4);
  assert.equal(decision.decision_affinity_z, -.2);
  assert.equal(decision.decision_tag_affinity_pct, .03);
  assert.equal(decision.decision_tag_affinity_adjusted_pct, .025);
  assert.equal(decision.decision_tag_affinity_lower_pct, .01);
  assert.equal(decision.decision_tag_affinity_upper_pct, .04);
  assert.equal(decision.decision_rank_within_tag_by_z, 19);
  assert.equal(decision.decision_affinity_model_status, "fitted");
  assert.equal(decision.decision_affinity_model_version, "beta_binomial_v1");

  const alphaNoCompetitive = buildCommanderBracketRows(rows.slice(0, 2))[0];
  assert.equal(alphaNoCompetitive.decision_tag_slug, "combo");
  assert.equal(alphaNoCompetitive.decision_tag_affinity_pct, .2);
  assert.equal(alphaNoCompetitive.decision_affinity_z, 3);
});

test("full and compact theme data preserve independent selected and deciding tag metrics", () => {
  const identity = { commander_slug: "alpha", commander_name: "Alpha" };
  const theme = { ...identity, ...tag("tokens", 3), legacy_z: 1.2,
    tag_affinity_pct: .2, tag_affinity_adjusted_pct: .18,
    tag_affinity_lower_pct: .15, tag_affinity_upper_pct: .21,
    tag_decks: 200, rank_within_tag_by_z: 7,
    affinity_model_status: "fitted", affinity_model_version: "beta_binomial_v1" };
  const deciding = { ...identity, ...tag("cedh", null), legacy_z: .4,
    tag_affinity_pct: .03, tag_affinity_adjusted_pct: .03,
    tag_affinity_lower_pct: null, tag_affinity_upper_pct: null,
    tag_decks: 30, rank_within_tag_by_z: null,
    affinity_model_status: "fallback_fit_failed", affinity_model_version: "beta_binomial_v1" };
  const firstUnrelated = { ...identity, ...tag("lands", 99), legacy_z: 4,
    tag_affinity_pct: .9, tag_affinity_adjusted_pct: .899 };
  const compact = { ...identity, theme_tag_slug: "tokens", theme_tag_name: "tokens",
    theme_z: 1.2, theme_legacy_z: 1.2, theme_affinity_z: 3,
    theme_tag_decks: 200, theme_affinity_pct: .2, theme_affinity_adjusted_pct: .18,
    theme_affinity_lower_pct: .15, theme_affinity_upper_pct: .21,
    theme_rank_within_tag_by_z: 7, theme_affinity_model_status: "fitted",
    theme_affinity_model_version: "beta_binomial_v1", bracket_tag_rows: [deciding] };
  const fullResult = buildCommanderThemeBracketRows([firstUnrelated, theme, deciding], "tokens")[0];
  const compactResult = buildCommanderThemeBracketRows([compact], "tokens")[0];
  assert.deepEqual(ownMetrics(fullResult), ownMetrics(compactResult));
  assert.equal(fullResult.theme_affinity_pct, .2);
  assert.equal(fullResult.theme_affinity_adjusted_pct, .18);
  assert.equal(fullResult.theme_affinity_lower_pct, .15);
  assert.equal(fullResult.theme_affinity_upper_pct, .21);
  assert.equal(fullResult.theme_z, 1.2);
  assert.equal(fullResult.theme_affinity_z, 3);
  assert.equal(fullResult.theme_rank_within_tag_by_z, 7);
  assert.equal(fullResult.decision_tag_affinity_pct, .03);
  assert.equal(fullResult.decision_affinity_z, null);
  assert.equal(fullResult.decision_tag_affinity_lower_pct, null);
});

test("missing selected metrics stay null without borrowing unrelated or legacy fields", () => {
  const result = buildCommanderThemeBracketRows([{
    commander_slug: "missing", theme_tag_slug: "tokens", theme_z: 1.2,
    theme_affinity_z: null, tag_affinity_pct: .99, tag_affinity_adjusted_pct: .98,
    rank_within_tag_by_z: 1, affinity_model_status: "fitted",
    bracket_tag_rows: [tag("combo", 1.2)],
  }], "tokens")[0];
  assert.equal(result.theme_affinity_z, null);
  assert.equal(result.theme_affinity_pct, null);
  assert.equal(result.theme_affinity_adjusted_pct, null);
  assert.equal(result.theme_rank_within_tag_by_z, null);
  assert.equal(result.theme_affinity_model_status, null);
  assert.equal(result.decision_tag_affinity_pct, null);
  assert.equal(result.decision_tag_affinity_adjusted_pct, null);
});

test("theme bracket eligibility and displayed score retain legacy affinity", () => {
  const rows = [
    { commander_slug: "retained", ...tag("tokens", 0.2), legacy_z: 1.2 },
    { commander_slug: "retained", ...tag("combo", -0.4), legacy_z: 1.05 },
    { commander_slug: "excluded", ...tag("tokens", 5), legacy_z: 1.04 },
    { commander_slug: "excluded", ...tag("combo", 5), legacy_z: 1.05 },
    { commander_slug: "missing", ...tag("tokens", 5), legacy_z: null },
  ];
  const results = buildCommanderThemeBracketRows(rows, "tokens");
  assert.equal(results.length, 1);
  assert.equal(results[0].commander_slug, "retained");
  assert.equal(results[0].bracket_key, "3");
  assert.equal(results[0].theme_z, 1.2);
  assert.equal(results[0].theme_affinity_z, 0.2);
  assert.equal(results[0].decision_z, 1.05);
});
