import assert from "node:assert/strict";
import test from "node:test";

import {
  formatBuildPoints, formatBuildShare, getBuildModel, getBuildModelOptions, getComparableProfilePairs,
  getDeckMixtureRows, getSupportedThemes, safeExternalUrl,
} from "./buildStyleUtils.js";

test("models keep any number of packages and match alternative weights by deck ID", () => {
  for (const count of [1, 3, 7]) {
    const primary = {
      packages: [{ id: 0 }],
      decks: [{ id: "a", source_tags: [] }, { id: "b", source_tags: ["Tokens", "Counters", "Sacrifice"] }],
      alternative_models: [{
        components: count,
        packages: Array.from({ length: count }, (_, id) => ({ id })),
        memberships: [{ id: "b", weights: Array(count).fill(1 / count) }, { id: "a", weights: Array(count).fill(0) }],
      }],
    };
    assert.equal(getBuildModel(primary), primary);
    assert.equal(getBuildModel(primary, "missing"), primary);
    const result = getBuildModel(primary, String(count));
    assert.equal(result.packages.length, count);
    assert.deepEqual(result.decks[0].memberships, Array(count).fill(0));
    assert.deepEqual(result.decks[1].memberships, Array(count).fill(1 / count));
    assert.deepEqual(result.decks[1].source_tags, ["Tokens", "Counters", "Sacrifice"]);
    assert.equal(primary.decks[0].memberships, undefined);
  }
});

test("selector lists only available positive integer package counts in numeric order", () => {
  assert.deepEqual(getBuildModelOptions({ alternative_models: [1, 7, 3, 1, 0, 2.5].map((components) => ({ components, packages: [] })) }), [1, 3, 7]);
  assert.deepEqual(getBuildModelOptions(undefined), []);
});

test("all supported suggested themes survive, including single themes and more than three", () => {
  for (const count of [0, 1, 3, 8]) {
    const themes = Array.from({ length: count }, (_, index) => ({ tag_slug: `theme-${index}`, tag_name: `Theme ${index}` }));
    assert.deepEqual(getSupportedThemes({ tag_suggestions: [...themes, ...themes] }), themes);
  }
  assert.deepEqual(getSupportedThemes({}), []);
});

test("mixture filtering uses the selected package and preserves untagged decks", () => {
  const decks = [
    { id: "a", source_tags: [], memberships: [0.1, 0.2, 0.3, 0.4] },
    { id: "b", source_tags: ["Tokens"], memberships: [0, 0, 0, 1] },
    { id: "c", source_tags: ["A", "B", "C"], memberships: [1, 0, 0, 0] },
  ];
  assert.equal(getDeckMixtureRows({ decks }), decks);
  assert.deepEqual(getDeckMixtureRows({ decks }, "3", 0.2).map((deck) => deck.id), ["b", "a"]);
  assert.deepEqual(decks.map((deck) => deck.id), ["a", "b", "c"]);
});

test("profile comparisons reject unavailable profiles and missing similarities", () => {
  const lower = { left: "a", right: "b", similarity: 0 };
  const higher = { left: "b", right: "a", similarity: 0.8 };
  const profiles = {
    profiles: [{ tag_slug: "a" }, { tag_slug: "b" }],
    pairs: [lower, { left: "a", right: "missing", similarity: 1 }, higher, { left: "a", right: "b", similarity: null }],
  };
  assert.deepEqual(getComparableProfilePairs(profiles), [higher, lower]);
});

test("shares distinguish missing evidence from a measured zero; external links disallow scripts", () => {
  assert.equal(formatBuildShare(null), "—");
  assert.equal(formatBuildShare(0), "0%");
  assert.equal(formatBuildShare(0.1234, 1), "12.3%");
  assert.equal(formatBuildPoints(0.1234), "+12.3 pts");
  assert.equal(formatBuildPoints(null), "—");
  assert.equal(safeExternalUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalUrl("data:text/html,test"), null);
  assert.equal(safeExternalUrl("/relative"), null);
  assert.equal(safeExternalUrl("https://edhrec.com/deckpreview/abc"), "https://edhrec.com/deckpreview/abc");
});
