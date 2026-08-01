import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCommanderBracketRows,
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
