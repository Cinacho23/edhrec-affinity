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
